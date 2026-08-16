"""Docling parsing backend.

Docling is the single production parser and owns OCR/ASR/Vision extraction.
Legacy parsers and project-specific OCR providers are not part of the path.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from app.config import settings
from app.models import Unit

DOCLING_PARSER_VERSION = "docling-v2"

_SUPPORTED = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".csv",
    ".html", ".htm", ".md", ".txt", ".adoc", ".asciidoc", ".xml",
    ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp",
    ".odt", ".ods", ".odp", ".epub", ".eml",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4", ".mov", ".avi", ".mkv", ".vtt",
}


def can_parse_with_docling(path: str | Path) -> bool:
    return Path(path).suffix.lower() in _SUPPORTED


def parse_with_docling(path: str | Path) -> tuple[list[Unit], dict]:
    """分层自适应解析:最快可靠路径优先,按需逐级升级。

    路由:FAST_NATIVE → STRUCTURED → SELECTIVE_OCR → FULL_OCR(→ VISION 预留)
    - 原生格式(docx/pptx/html/md/txt 等):直接结构化,不 OCR
    - PDF:先零模型文本层提取,质量好直接用;否则版面解析(模型);
      仍差的页面才页面级 OCR;全部页面差才整份 OCR。
    """
    source = Path(path)
    ext = source.suffix.lower()
    if not can_parse_with_docling(source):
        raise ValueError(f"UNSUPPORTED_FORMAT: {ext}")

    with _parse_lock:
        if ext == ".pdf":
            return _parse_pdf_layered(source)
        if ext == ".xml":
            return _xml_text_units(source)
        return _convert(source, ext, do_ocr=False)


def _xml_text_units(source: Path) -> tuple[list[Unit], dict]:
    """XML 轻量文本提取(RFC/JATS 等以文本为主的 XML,不进版面模型)。

    XML 结构标签剥离后按段落提取为 text units;失败显式报错。
    """
    import xml.etree.ElementTree as ET

    tree = ET.parse(str(source))
    text_parts: list[str] = []
    for elem in tree.iter():
        if elem.text and elem.text.strip():
            text_parts.append(elem.text.strip())
        if elem.tail and elem.tail.strip():
            text_parts.append(elem.tail.strip())
    blocks = [p for p in text_parts if len(p) > 1]
    units = [
        Unit(material_id=0, kind="text", content=block, paragraph=index,
             metadata_json=json.dumps({"source_type": "xml_text"}, ensure_ascii=False))
        for index, block in enumerate(blocks, start=1)
    ]
    profile = {
        "parser": DOCLING_PARSER_VERSION,
        "file_name": source.name,
        "file_type": "xml",
        "parse_method": "XML_TEXT",
        "page_count": 0,
        "text_count": len(units),
        "table_count": 0,
        "image_count": 0,
        "markdown_chars": sum(len(b) for b in blocks),
        "ocr_enabled": False,
    }
    return units, profile


def _parse_pdf_layered(source: Path) -> tuple[list[Unit], dict]:
    """PDF 分层路由(最快可靠路径优先,判断偏保守:拿不准就升级)。"""
    total_pages = _pdf_page_count(source)
    # 结构存疑页:页面含图像对象(图表/扫描图)→ 文本层结构不足,升级到 STRUCTURED
    graphic_pages = _graphic_heavy_pages(source)
    # 1. FAST_NATIVE:文本层提取(零模型);仅当全部页文本质量 ok 且无结构存疑页
    native_units = _native_text_units(source)
    native_status = _assess_units_by_page(native_units, total_pages)
    if (native_units and total_pages > 0
            and len(native_status) >= total_pages
            and all(status == "ok" for status in native_status.values())
            and not graphic_pages):
        return native_units, _layered_profile(source, "FAST_NATIVE", native_status)

    # 2. STRUCTURED:Docling 版面解析(模型,无 OCR)
    try:
        units, profile = _convert(source, ".pdf", do_ocr=False)
    except Exception:
        # STRUCTURED 失败 → 显式降级 FULL_OCR,不静默返回空
        units, profile = _convert(source, ".pdf", do_ocr=True)
        profile.update(_layered_meta("FULL_OCR", _assess_units_by_page(native_units, total_pages),
                                     ocr_pages="all", fallback_reason="structured_failed"))
        return units, profile
    status = _assess_units_by_page(units, total_pages)
    poor_pages = [page for page, s in status.items() if s != "ok"]
    if not poor_pages:
        profile.update(_layered_meta("STRUCTURED", status))
        return units, profile

    if total_pages >= 2 and len(poor_pages) >= total_pages:
        # 3. FULL_OCR:几乎全部页面质量差 → 整份 OCR
        try:
            units, profile = _convert(source, ".pdf", do_ocr=True)
        except Exception:
            # FULL_OCR 失败 → 保留文本层结果并标注(内容优先,宁有文本不空)
            if native_units:
                return native_units, _layered_profile(source, "FAST_NATIVE_FALLBACK", native_status)
            raise
        profile.update(_layered_meta("FULL_OCR", status, ocr_pages="all",
                                     fallback_reason=f"pages_poor:{len(poor_pages)}/{total_pages}"))
        return units, profile

    # 4. SELECTIVE_OCR:只对问题页做页面级 OCR(不整份)
    ocr_units = _ocr_pages_units(source, poor_pages)
    merged = units + ocr_units
    profile.update(_layered_meta("SELECTIVE_OCR", status, ocr_pages=poor_pages,
                                 fallback_reason=f"pages_poor:{len(poor_pages)}/{total_pages}"))
    return merged, profile


def _graphic_heavy_pages(source: Path) -> set[int]:
    """页面含图像对象(图表/扫描图)→ 结构存疑页集合(保守信号,无阈值)。"""
    try:
        import fitz
        doc = fitz.open(str(source))
        pages: set[int] = set()
        for index in range(len(doc)):
            if doc[index].get_images():
                pages.add(index + 1)
        doc.close()
        return pages
    except Exception:
        return set()


def _pdf_page_count(source: Path) -> int:
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(source))
        count = len(pdf)
        pdf.close()
        return count
    except Exception:
        return 0


def _layered_profile(source: Path, method: str, page_status: dict) -> dict:
    return _layered_meta(method, page_status) | {
        "parser": DOCLING_PARSER_VERSION,
        "file_name": source.name,
        "file_type": source.suffix.lower().lstrip("."),
    }


def _layered_meta(method: str, page_status: dict, ocr_pages=None, fallback_reason: str = "") -> dict:
    return {
        "parse_method": method,
        "fallback_reason": fallback_reason,
        "ocr_pages": ocr_pages if ocr_pages is not None else [],
        "vision_pages": [],
        "page_quality": {str(k): v for k, v in sorted(page_status.items())},
        "ocr_enabled": method in ("SELECTIVE_OCR", "FULL_OCR"),
    }


def _native_text_units(source: Path) -> list[Unit]:
    """零模型文本层提取(pypdfium2,嵌入文本层)。"""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    units: list[Unit] = []
    try:
        pdf = pdfium.PdfDocument(str(source))
        for index in range(len(pdf)):
            page = pdf[index]
            text = ""
            try:
                text = (page.get_textpage().get_text_range() or "").strip()
            except Exception:
                text = ""
            if text:
                units.append(Unit(
                    material_id=0, kind="text", content=text,
                    page=index + 1, paragraph=index + 1,
                    metadata_json=json.dumps({"source_type": "text_layer"}, ensure_ascii=False),
                ))
        pdf.close()
    except Exception:
        return []
    return units


def _assess_units_by_page(units: list[Unit], total_pages: int = 0) -> dict[int, str]:
    """按页聚合文本做轻量质量判断(无业务阈值:空/替换符/可读字符不过半)。

    缺失页(无任何 unit)视为 empty——图片页/空白页会触发升级,不误判为 ok。
    """
    by_page: dict[int, str] = {}
    for unit in units:
        if unit.page is None:
            continue
        text = unit.content or ""
        if unit.kind != "image":
            by_page[unit.page] = by_page.get(unit.page, "") + text
    status = {}
    for page in range(1, total_pages + 1):
        text = by_page.get(page, "")
        status[page] = _page_quality(text)
    return status


def _page_quality(text: str) -> str:
    """页面质量信号:empty(无文本)/ garbled(替换符)/ unreadable(可读字符不过半)/ ok。"""
    stripped = "".join(ch for ch in (text or "") if not ch.isspace())
    if not stripped:
        return "empty"
    if "\ufffd" in stripped:
        return "garbled"
    readable = sum(1 for ch in stripped if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if readable <= len(stripped) / 2:
        return "unreadable"
    return "ok"


_OCR_ENGINE = None


def _get_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import ModelType
        model_type = (settings.docling_ocr_model or "medium").strip().lower()
        _MODEL_TYPE = {"tiny": ModelType.TINY, "small": ModelType.SMALL, "medium": ModelType.MEDIUM}
        mt = _MODEL_TYPE.get(model_type, ModelType.MEDIUM)
        _OCR_ENGINE = RapidOCR(params={
            "Det.model_type": mt,
            "Rec.model_type": mt,
        })
    return _OCR_ENGINE


def _ocr_pages_units(source: Path, pages: list[int]) -> list[Unit]:
    """页面级 OCR:仅渲染并识别指定页(不整份 OCR)。"""
    if not pages:
        return []
    try:
        import numpy as np
        import pypdfium2 as pdfium
    except ImportError:
        return []
    ocr = _get_ocr_engine()
    units: list[Unit] = []
    try:
        pdf = pdfium.PdfDocument(str(source))
        for index in range(len(pdf)):
            page_no = index + 1
            if page_no not in pages:
                continue
            page = pdf[index]
            bitmap = page.render(scale=2.0).to_pil()
            result = ocr(np.asarray(bitmap))
            lines = []
            # rapidocr 3.9:返回 RapidOCROutput(.txts);旧版:元组 (result, elapse)
            if result is not None and hasattr(result, "txts"):
                lines = [str(t).strip() for t in result.txts if t and str(t).strip()]
            elif isinstance(result, (list, tuple)) and result and result[0]:
                lines = [str(t).strip() for _box, t, _score in result[0] if t and str(t).strip()]
            if lines:
                units.append(Unit(
                    material_id=0, kind="text", content="\n".join(lines),
                    page=page_no, paragraph=0,
                    metadata_json=json.dumps({"source_type": "ocr", "ocr_page": True}, ensure_ascii=False),
                ))
        pdf.close()
    except Exception:
        return []
    return units


def _convert(source: Path, ext: str, do_ocr: bool) -> tuple[list[Unit], dict]:
    converter = _build_converter(ext, do_ocr=do_ocr)
    result = converter.convert(str(source))
    document = result.document
    data = document.export_to_dict()
    markdown = (document.export_to_markdown() or "").strip()
    units = _units_from_docling_dict(data)
    if not units and markdown:
        units = _units_from_markdown(markdown)
    profile = _profile_from_docling_dict(data, markdown, source)
    profile["conversion_status"] = str(getattr(result, "status", "") or "")
    profile["ocr_enabled"] = bool(do_ocr)
    return units, profile


_CONVERTER_CACHE: dict = {}
# 统一 Parse Scheduler:所有 Docling 解析(任务材料/模板/后台)必经此入口,
# 串行执行避免 converter 缓存与 CUDA 上下文并发竞争。
_parse_lock = threading.Lock()


_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _build_converter(ext: str, do_ocr: bool = False):
    """构建(并缓存)Docling 转换器。

    模型/管线按 (格式, 是否OCR) 缓存复用——多份文档解析只加载一次模型,
    避免每份重建管线(单份重建含模型加载,可占数分钟)。
    """
    key = (ext, bool(do_ocr))
    if key in _CONVERTER_CACHE:
        return _CONVERTER_CACHE[key]
    from docling.document_converter import (
        AudioFormatOption,
        DocumentConverter,
        PdfFormatOption,
        VideoFormatOption,
    )
    from docling.datamodel.base_models import InputFormat

    format_options = {}
    if ext == ".pdf":
        format_options[InputFormat.PDF] = PdfFormatOption(pipeline_options=_pdf_pipeline_options(do_ocr=do_ocr))
    elif ext in _AUDIO_EXTS or ext in _VIDEO_EXTS:
        asr_pipeline = _asr_pipeline_options(ext)
        if asr_pipeline is not None:
            fmt = InputFormat.AUDIO if ext in _AUDIO_EXTS else InputFormat.VIDEO
            option_cls = AudioFormatOption if ext in _AUDIO_EXTS else VideoFormatOption
            format_options[fmt] = option_cls(pipeline_options=asr_pipeline)
    converter = DocumentConverter(format_options=format_options or None)
    _CONVERTER_CACHE[key] = converter
    return converter


def _asr_pipeline_options(ext: str):
    """ASR 管线配置:whisper 档位(默认 small,可配)+ 显式语言(中文材料避免自动检测失败)。"""
    from docling.datamodel.asr_model_specs import (
        WHISPER_BASE,
        WHISPER_LARGE,
        WHISPER_MEDIUM,
        WHISPER_SMALL,
        WHISPER_TINY,
    )
    from docling.datamodel.pipeline_options import AsrPipelineOptions, VideoPipelineOptions

    _SPECS = {
        "tiny": WHISPER_TINY, "base": WHISPER_BASE, "small": WHISPER_SMALL,
        "medium": WHISPER_MEDIUM, "large": WHISPER_LARGE,
    }
    base = _SPECS.get((settings.asr_model or "small").strip().lower(), WHISPER_SMALL)
    lang = (settings.asr_language or "").strip()
    asr_options = base.model_copy(update={"language": lang}) if lang else base
    if ext in _AUDIO_EXTS:
        return AsrPipelineOptions(asr_options=asr_options)
    return VideoPipelineOptions(asr_options=asr_options)


def _pdf_pipeline_options(do_ocr: bool):
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,
        OcrAutoOptions,
        PdfPipelineOptions,
        RapidOcrOptions,
        TesseractOcrOptions,
    )

    options = PdfPipelineOptions()
    options.do_ocr = bool(do_ocr)
    if not do_ocr:
        options.do_table_structure = True
        return options
    engine = (settings.docling_ocr_engine or "auto").strip().lower()
    if engine == "auto":
        options.ocr_options = OcrAutoOptions(lang=["ch_sim", "en"])
    elif engine == "rapidocr":
        options.ocr_options = RapidOcrOptions(lang=["ch_sim", "en"])
    elif engine == "easyocr":
        options.ocr_options = EasyOcrOptions(lang=["ch_sim", "en"])
    elif engine == "tesseract":
        options.ocr_options = TesseractOcrOptions(lang=["chi_sim", "eng"])
    else:
        raise ValueError(f"UNSUPPORTED_DOCLING_OCR_ENGINE: {engine}")
    return options


def _units_from_docling_dict(data: dict) -> list[Unit]:
    units: list[Unit] = []
    for index, item in enumerate(data.get("texts") or [], start=1):
        text = str(item.get("text") or item.get("orig") or "").strip()
        if not text:
            continue
        label = str(item.get("label") or "").lower()
        kind = "heading" if "heading" in label or "title" in label or "section_header" in label else "text"
        units.append(Unit(
            material_id=0,
            kind=kind,
            content=text,
            page=_page_no(item),
            paragraph=index,
            metadata_json=_metadata(item, label=label, source_type="text"),
        ))
    for index, item in enumerate(data.get("tables") or [], start=1):
        text = _table_text(item)
        if not text:
            continue
        units.append(Unit(
            material_id=0,
            kind="table",
            content=text,
            page=_page_no(item),
            paragraph=index,
            metadata_json=_metadata(item, label="table", source_type="table"),
        ))
    for index, item in enumerate(data.get("pictures") or [], start=1):
        caption = " ".join(
            str(c.get("text", "")) for c in item.get("captions", []) if isinstance(c, dict)
        ).strip()
        content = caption or "<图片>"
        units.append(Unit(
            material_id=0,
            kind="image",
            content=content,
            page=_page_no(item),
            paragraph=index,
            metadata_json=_metadata(item, label="picture", source_type="image"),
        ))
    return units


def _units_from_markdown(markdown: str) -> list[Unit]:
    blocks = [part.strip() for part in markdown.split("\n\n") if part.strip()]
    units: list[Unit] = []
    for index, block in enumerate(blocks, start=1):
        kind = "heading" if block.startswith("#") else "text"
        units.append(Unit(
            material_id=0,
            kind=kind,
            content=block,
            paragraph=index,
            metadata_json=json.dumps({"source_type": "markdown"}, ensure_ascii=False),
        ))
    return units


def _profile_from_docling_dict(data: dict, markdown: str, source: Path) -> dict:
    pages = data.get("pages") or {}
    page_count = len(pages) if isinstance(pages, dict) else len(pages or [])
    texts = data.get("texts") or []
    tables = data.get("tables") or []
    pictures = data.get("pictures") or []
    headings = []
    for item in texts:
        label = str(item.get("label") or "").lower()
        text = str(item.get("text") or item.get("orig") or "").strip()
        if text and ("heading" in label or "title" in label or "section_header" in label or text.startswith("#")):
            headings.append(text[:120])
    return {
        "parser": DOCLING_PARSER_VERSION,
        "file_name": source.name,
        "file_type": source.suffix.lower().lstrip("."),
        "page_count": page_count,
        "text_count": len(texts),
        "table_count": len(tables),
        "image_count": len(pictures),
        "markdown_chars": len(markdown or ""),
        "capabilities": {
            "docling_ocr_engine": settings.docling_ocr_engine,
            "multimodal_backend": "docling_builtin",
        },
        "structure": {
            "headings": headings[:30],
            "has_title": bool(headings),
            "has_toc": any("目录" in h or "contents" in h.lower() for h in headings),
        },
    }


def _metadata(item: dict, label: str, source_type: str) -> str:
    payload = {
        "source_type": source_type,
        "docling_label": label,
        "page": _page_no(item),
        "bbox": _bbox(item),
        "prov": item.get("prov") or [],
        "self_ref": item.get("self_ref") or item.get("$ref") or "",
    }
    return json.dumps(payload, ensure_ascii=False)


def _page_no(item: dict) -> int | None:
    prov = item.get("prov") or []
    if isinstance(prov, list) and prov:
        page = prov[0].get("page_no") if isinstance(prov[0], dict) else None
        try:
            return int(page) if page is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _bbox(item: dict) -> list[float]:
    prov = item.get("prov") or []
    if not isinstance(prov, list) or not prov or not isinstance(prov[0], dict):
        return []
    bbox = prov[0].get("bbox") or {}
    if isinstance(bbox, dict):
        values = [bbox.get(key) for key in ("l", "t", "r", "b")]
        try:
            return [float(v) for v in values if v is not None]
        except (TypeError, ValueError):
            return []
    if isinstance(bbox, (list, tuple)):
        try:
            return [float(v) for v in bbox]
        except (TypeError, ValueError):
            return []
    return []


def _table_text(item: dict) -> str:
    data = item.get("data")
    if isinstance(data, dict):
        grid = data.get("grid")
        if isinstance(grid, list):
            rows = []
            for row in grid:
                if isinstance(row, list):
                    values = [
                        str(cell.get("text", "") if isinstance(cell, dict) else cell).strip()
                        for cell in row
                    ]
                    if any(values):
                        rows.append(" | ".join(values))
            if rows:
                return "\n".join(rows)
        return json.dumps(data, ensure_ascii=False)
    rows = item.get("table_cells") or item.get("cells") or []
    values = []
    for row in rows:
        if isinstance(row, dict):
            text = str(row.get("text") or "").strip()
            if text:
                values.append(text)
    return " | ".join(values)
