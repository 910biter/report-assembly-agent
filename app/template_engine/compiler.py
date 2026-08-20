"""Deterministic DOCX template compiler.

The compiler intentionally separates two tracks:
- JSON schema: semantic roles, computed style, placeholders, and QA.
- Original DOCX: kept as the rendering base so complex OOXML is not re-created.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.shared import Length
from docx.oxml.ns import qn

COMPILER_VERSION = "template-compiler-1.0"

ROLE_DOCUMENT_TITLE = "document_title"
ROLE_HEADING_1 = "heading_1"
ROLE_HEADING_2 = "heading_2"
ROLE_HEADING_3 = "heading_3"
ROLE_BODY = "body"
ROLE_METADATA_LABEL = "metadata_label"
ROLE_METADATA_VALUE = "metadata_value"
ROLE_SIGNATURE = "signature"

_CJK_HEADING_1 = re.compile(r"^(?:[一二三四五六七八九十]+[、.]|第[一二三四五六七八九十\d]+[章节部分])\s*\S+")
_CJK_HEADING_2 = re.compile(r"^[（(][一二三四五六七八九十\d]+[)）]\s*\S+")
_NUM_HEADING_1 = re.compile(r"^\d+(?:\.(?!\d)|[、\s])\s*\S+")
_NUM_HEADING_2 = re.compile(r"^\d+\.\d+(?:\.(?!\d)|[、\s])\s*\S+")
_NUM_HEADING_3 = re.compile(r"^\d+\.\d+\.\d+[、\s]?\s*\S+")
_NUM_HEADING = re.compile(r"^\d+(?:\.\d+)*[.、]?\s*\S+")
_PLACEHOLDER = re.compile(r"(\{\{[^}]+\}\}|《[^》]+》|【[^】]+】|________+|_{4,})")


def compile_template(path) -> dict:
    """Compile a DOCX into a structured template schema.

    No LLM is used here. Unknown or unobservable details remain explicit instead
    of being guessed, which lets the UI and conformance checker show gaps.
    """
    source = Path(path)
    if not source.exists() or source.suffix.lower() != ".docx":
        return {}
    doc = Document(source)
    paragraphs = [p for p in doc.paragraphs if p.text.strip()]
    role_paragraphs = _detect_role_paragraphs(paragraphs)
    schema = {
        "template_version": "1.0",
        "compiler_version": COMPILER_VERSION,
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(source),
            "filename": source.name,
            "sha256": _sha256(source),
        },
        "document": _document_layout(doc),
        "structure": _document_structure(doc, role_paragraphs),
        "style": {
            "roles": {
                role: _computed_role_style(doc, role, para)
                for role, para in role_paragraphs.items()
                if para is not None
            },
            "numbering": _numbering(doc),
            "tables": _tables(doc),
            "headers": _headers(doc),
            "footers": _footers(doc),
            "objects": _objects(doc),
        },
        "components": _components(doc, role_paragraphs),
    }
    schema["quality"] = _quality(schema)
    return schema


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _document_layout(doc) -> dict:
    sections = []
    for section in doc.sections:
        sections.append({
            "page": {
                "width_cm": _cm(section.page_width),
                "height_cm": _cm(section.page_height),
                "orientation": "landscape" if section.orientation == WD_ORIENT.LANDSCAPE else "portrait",
            },
            "margins": {
                "top_cm": _cm(section.top_margin),
                "bottom_cm": _cm(section.bottom_margin),
                "left_cm": _cm(section.left_margin),
                "right_cm": _cm(section.right_margin),
                "gutter_cm": _cm(getattr(section, "gutter", None)),
                "header_distance_cm": _cm(section.header_distance),
                "footer_distance_cm": _cm(section.footer_distance),
            },
            "header_footer": {
                "different_first_page": bool(section.different_first_page_header_footer),
                "header_text": _header_footer_text(section.header),
                "footer_text": _header_footer_text(section.footer),
            },
        })
    first = sections[0] if sections else {}
    return {
        "page": first.get("page", {}),
        "margins": first.get("margins", {}),
        "sections": sections,
        "defaults": _doc_defaults(doc),
    }


def _document_structure(doc, role_paragraphs: dict) -> dict:
    headings = []
    for para in doc.paragraphs:
        for item in _heading_items(para):
            headings.append(item)
    return {
        "roles_detected": sorted(k for k, v in role_paragraphs.items() if v is not None),
        "heading_patterns": headings[:20],
        "heading_tree": _heading_tree(headings),
        "template_instructions": _template_instructions(doc),
        "metadata_fields": _metadata_fields(doc),
        "signature_fields": _signature_fields(doc),
        "placeholders": _placeholder_registry(doc, role_paragraphs),
    }


def _detect_role_paragraphs(paragraphs: list) -> dict:
    roles = {
        ROLE_DOCUMENT_TITLE: None,
        ROLE_HEADING_1: None,
        ROLE_HEADING_2: None,
        ROLE_HEADING_3: None,
        ROLE_BODY: None,
        ROLE_SIGNATURE: None,
    }
    if paragraphs:
        roles[ROLE_DOCUMENT_TITLE] = paragraphs[0]
    for para in paragraphs[1:]:
        role = _paragraph_role(para)
        if role in {ROLE_HEADING_1, ROLE_HEADING_2, ROLE_HEADING_3} and roles[role] is None:
            roles[role] = para
    for para in paragraphs[1:]:
        text = para.text.strip()
        if (
            roles[ROLE_BODY] is None
            and _paragraph_role(para) == ROLE_BODY
            and _looks_like_body_sample(para)
        ):
            roles[ROLE_BODY] = para
        if roles[ROLE_SIGNATURE] is None and re.search(r"(单位|日期|年\s*月\s*日|盖章|署名)", text):
            roles[ROLE_SIGNATURE] = para
    if roles[ROLE_BODY] is None and len(paragraphs) > 1:
        roles[ROLE_BODY] = next((p for p in paragraphs[1:] if _paragraph_role(p) == ROLE_BODY), paragraphs[1])
    return roles


def _paragraph_role(para) -> str:
    text = para.text.strip()
    style = (para.style.name or "").lower() if para.style is not None else ""
    if _CJK_HEADING_1.match(text) or _NUM_HEADING_1.match(text) or style.startswith(("heading 1", "标题 1")) or "一级标题" in style:
        return ROLE_HEADING_1
    if _CJK_HEADING_2.match(text) or _NUM_HEADING_2.match(text) or style.startswith(("heading 2", "标题 2")) or "二级标题" in style:
        return ROLE_HEADING_2
    if _NUM_HEADING_3.match(text) or style.startswith(("heading 3", "标题 3")) or "三级标题" in style:
        return ROLE_HEADING_3
    return ROLE_BODY


def _computed_role_style(doc, role: str, para) -> dict:
    run = next((r for r in para.runs if r.text.strip()), para.runs[0] if para.runs else None)
    return {
        "paragraph": _paragraph_style(doc, para),
        "run": _run_style(doc, para, run),
        "sample_text": _role_sample_text(role, para.text),
        "samples": _role_samples(role, para.text),
        "source_style": para.style.name if para.style is not None else "",
        "computed_from": ["style_definition", "paragraph_direct_formatting", "run_direct_formatting"],
    }


def _paragraph_style(doc, para) -> dict:
    pf = para.paragraph_format
    style_pf = para.style.paragraph_format if para.style is not None else None
    normal_pf = doc.styles["Normal"].paragraph_format
    return {
        "alignment": _alignment(para.alignment, _pf_value(style_pf, "alignment"), normal_pf.alignment),
        "left_indent_cm": _length_value(pf.left_indent, _pf_value(style_pf, "left_indent"), normal_pf.left_indent),
        "right_indent_cm": _length_value(pf.right_indent, _pf_value(style_pf, "right_indent"), normal_pf.right_indent),
        "first_line_indent_cm": _length_value(pf.first_line_indent, _pf_value(style_pf, "first_line_indent"), normal_pf.first_line_indent),
        "space_before_pt": _pt_value(pf.space_before, _pf_value(style_pf, "space_before"), normal_pf.space_before),
        "space_after_pt": _pt_value(pf.space_after, _pf_value(style_pf, "space_after"), normal_pf.space_after),
        "line_spacing": _line_spacing(pf.line_spacing, _pf_value(style_pf, "line_spacing"), normal_pf.line_spacing),
        "keep_with_next": _bool_value(pf.keep_with_next, _pf_value(style_pf, "keep_with_next"), normal_pf.keep_with_next),
        "keep_together": _bool_value(pf.keep_together, _pf_value(style_pf, "keep_together"), normal_pf.keep_together),
        "page_break_before": _bool_value(pf.page_break_before, _pf_value(style_pf, "page_break_before"), normal_pf.page_break_before),
        "widow_control": _widow_control(para),
        "contextual_spacing": _contextual_spacing(para),
        "tabs": _tabs(pf),
        "outline_level": _outline_level(para),
        "shading": _shading(para._p),
        "borders": _computed_borders(para),
    }


def _run_style(doc, para, run) -> dict:
    style_font = para.style.font if para.style is not None else None
    normal_font = doc.styles["Normal"].font
    font = run.font if run is not None else None
    xmlfont = _rpr_font_values(para.style)
    return {
        "font_east_asia": _font_name(font, style_font, normal_font, "eastAsia", xmlfont),
        "font_ascii": _font_name(font, style_font, normal_font, "ascii", xmlfont),
        "font_hansi": _font_name(font, style_font, normal_font, "hAnsi", xmlfont),
        "font_cs": _font_name(font, style_font, normal_font, "cs", xmlfont),
        "font_size_pt": _font_size(font, style_font, normal_font, xmlfont),
        "bold": _bool_value(getattr(font, "bold", None), getattr(style_font, "bold", None), getattr(normal_font, "bold", None)),
        "italic": _bool_value(getattr(font, "italic", None), getattr(style_font, "italic", None), getattr(normal_font, "italic", None)),
        "underline": _bool_value(getattr(font, "underline", None), getattr(style_font, "underline", None), getattr(normal_font, "underline", None)),
        "strike": _bool_value(getattr(font, "strike", None), getattr(style_font, "strike", None), getattr(normal_font, "strike", None)),
        "font_color": _font_color(font, style_font, normal_font),
        "highlight": str(getattr(font, "highlight_color", "") or "") if font is not None else "unknown",
        "character_spacing": _run_prop(run, "spacing"),
        "scaling": _run_prop(run, "w"),
        "position": _run_prop(run, "position"),
        "superscript": _bool_value(getattr(font, "superscript", None), None, None),
        "subscript": _bool_value(getattr(font, "subscript", None), None, None),
        "small_caps": _bool_value(getattr(font, "small_caps", None), None, None),
        "all_caps": _bool_value(getattr(font, "all_caps", None), None, None),
        "language": _run_language(run),
    }


def _doc_defaults(doc) -> dict:
    normal = doc.styles["Normal"]
    return {
        "run": {
            "font_east_asia": _font_name(None, normal.font, None, "eastAsia"),
            "font_ascii": _font_name(None, normal.font, None, "ascii"),
            "font_size_pt": _font_size(None, normal.font, None),
        },
        "paragraph": {
            "line_spacing": _line_spacing(normal.paragraph_format.line_spacing),
            "first_line_indent_cm": _length_value(normal.paragraph_format.first_line_indent),
        },
    }


def _numbering(doc) -> dict:
    patterns = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        pattern = ""
        if _CJK_HEADING_1.match(text):
            pattern = "cjk_level_1"
        elif _CJK_HEADING_2.match(text):
            pattern = "cjk_level_2"
        elif _NUM_HEADING_3.match(text):
            pattern = "arabic_level_3"
        elif _NUM_HEADING_2.match(text):
            pattern = "arabic_level_2"
        elif _NUM_HEADING_1.match(text):
            pattern = "arabic_level_1"
        if pattern and pattern not in [p["format"] for p in patterns]:
            patterns.append({"format": pattern, "sample": _text_pattern(text)})
    return {"patterns": patterns}


def _tables(doc) -> list[dict]:
    tables = []
    for idx, table in enumerate(doc.tables[:10]):
        tables.append({
            "index": idx,
            "rows": len(table.rows),
            "columns": len(table.columns),
            "style_name": table.style.name if table.style is not None else "",
            "column_widths_cm": [_cm(cell.width) for cell in table.rows[0].cells] if table.rows else [],
            "cells": _table_cells(table),
        })
    return tables


def _table_cells(table) -> list[dict]:
    cells = []
    for r_idx, row in enumerate(table.rows[:5]):
        for c_idx, cell in enumerate(row.cells[:8]):
            text = "\n".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
            cells.append({
                "row": r_idx,
                "col": c_idx,
                "text_pattern": _text_pattern(text),
                "vertical_alignment": str(cell.vertical_alignment) if cell.vertical_alignment else "",
                "paragraph": _paragraph_style_from_any(cell.paragraphs[0]) if cell.paragraphs else {},
                "borders": _tc_borders(cell),
                "shading": _shading(cell._tc),
            })
    return cells


def _headers(doc) -> list[dict]:
    return [{"section": idx, "text": _header_footer_text(section.header), "paragraph_count": len(section.header.paragraphs)}
            for idx, section in enumerate(doc.sections)]


def _footers(doc) -> list[dict]:
    return [{"section": idx, "text": _header_footer_text(section.footer), "paragraph_count": len(section.footer.paragraphs)}
            for idx, section in enumerate(doc.sections)]


def _objects(doc) -> dict:
    return {
        "inline_images": len(getattr(doc, "inline_shapes", [])),
        "hyperlinks": _count_xpath(doc.element, ".//w:hyperlink"),
        "bookmarks": _count_xpath(doc.element, ".//w:bookmarkStart"),
        "fields": _count_xpath(doc.element, ".//w:fldChar"),
        "textboxes": _count_xpath(doc.element, ".//w:txbxContent"),
    }


def _components(doc, role_paragraphs: dict) -> list[dict]:
    components = []
    if role_paragraphs.get(ROLE_DOCUMENT_TITLE) is not None:
        components.append({"type": "TITLE_BLOCK", "role": ROLE_DOCUMENT_TITLE, "placeholder": "report_title"})
    metadata = _metadata_fields(doc)
    if metadata:
        components.append({"type": "METADATA_BLOCK", "fields": metadata})
    if doc.tables:
        components.append({"type": "TABLE", "count": len(doc.tables)})
    if any(_header_footer_text(s.header) for s in doc.sections):
        components.append({"type": "HEADER"})
    if any(_header_footer_text(s.footer) for s in doc.sections):
        components.append({"type": "FOOTER"})
    if role_paragraphs.get(ROLE_SIGNATURE) is not None:
        components.append({"type": "SIGNATURE_BLOCK", "role": ROLE_SIGNATURE, "fields": _signature_fields(doc)})
    components.append({"type": "BODY_SECTION", "role": ROLE_BODY, "placeholder": "report_body"})
    return components


def _quality(schema: dict) -> dict:
    roles = schema.get("style", {}).get("roles", {})
    required = {
        "主标题": ROLE_DOCUMENT_TITLE,
        "正文": ROLE_BODY,
        "一级标题": ROLE_HEADING_1,
    }
    checks = []
    for label, role in required.items():
        role_style = roles.get(role, {})
        checks.append({"item": f"识别{label}", "status": "ok" if role_style else "unknown"})
        checks.append({"item": f"{label}字体", "status": "ok" if role_style.get("run", {}).get("font_east_asia") not in ("", "unknown", None) else "unknown"})
        checks.append({"item": f"{label}字号", "status": "ok" if role_style.get("run", {}).get("font_size_pt") not in ("", "unknown", None) else "unknown"})
    body_para = roles.get(ROLE_BODY, {}).get("paragraph", {})
    for field in ("first_line_indent_cm", "line_spacing", "space_after_pt"):
        checks.append({"item": f"正文{field}", "status": "ok" if body_para.get(field) not in ("unknown", None, "") else "unknown"})
    checks.append({"item": "页眉页脚", "status": "ok" if schema.get("style", {}).get("headers") or schema.get("style", {}).get("footers") else "unknown"})
    checks.append({"item": "表格", "status": "ok" if schema.get("style", {}).get("tables") else "unknown"})
    unknown = [c["item"] for c in checks if c["status"] == "unknown"]
    return {
        "checks": checks,
        "unknown_items": unknown,
        "completeness": round((len(checks) - len(unknown)) / max(len(checks), 1), 2),
        "recommendation": "可用于正式导出" if not unknown else "部分模板属性需人工复核",
    }


def _heading_items(para_or_text) -> list[dict]:
    """识别标题项:优先用 Word 样式(中英文兼容),否则用增强正则兜底。
    兼容传 para 或纯文本两种调用。"""
    items = []
    if hasattr(para_or_text, "text"):
        para = para_or_text
        text = para.text or ""
        style_name = ((para.style.name or "").lower() if para.style is not None else "")
    else:
        para = None
        text = para_or_text or ""
        style_name = ""
    role = ""
    if para is not None:
        if style_name.startswith(("heading 1", "标题 1")) or "一级标题" in style_name:
            role = ROLE_HEADING_1
        elif style_name.startswith(("heading 2", "标题 2")) or "二级标题" in style_name:
            role = ROLE_HEADING_2
        elif style_name.startswith(("heading 3", "标题 3")) or "三级标题" in style_name:
            role = ROLE_HEADING_3
    for line in _logical_lines(text):
        r = role
        if not r:
            if _CJK_HEADING_1.match(line) or _NUM_HEADING_1.match(line):
                r = ROLE_HEADING_1
            elif _CJK_HEADING_2.match(line) or _NUM_HEADING_2.match(line):
                r = ROLE_HEADING_2
            elif _NUM_HEADING_3.match(line):
                r = ROLE_HEADING_3
        if r:
            items.append({"role": r, "level": _heading_level(r), "text_pattern": _text_pattern(line)})
    return items


def _heading_level(role: str) -> int:
    return {ROLE_HEADING_1: 1, ROLE_HEADING_2: 2, ROLE_HEADING_3: 3}.get(role, 9)


def _heading_tree(headings: list[dict]) -> list[dict]:
    """Build a heading hierarchy for UI and export policy inspection."""
    roots: list[dict] = []
    stack: list[dict] = []
    for item in headings:
        level = int(item.get("level") or _heading_level(item.get("role", "")))
        node = {
            "role": item.get("role", ""),
            "level": level,
            "text_pattern": item.get("text_pattern", ""),
            "children": [],
        }
        while stack and int(stack[-1].get("level") or 9) >= level:
            stack.pop()
        if stack:
            stack[-1].setdefault("children", []).append(node)
        else:
            roots.append(node)
        stack.append(node)
    return roots[:30]


def _looks_like_body_sample(para) -> bool:
    text = para.text.strip()
    style = para.style.name if para.style is not None else ""
    if not text or _heading_items(text):
        return False
    if _is_template_instruction(text):
        return "正文样式" in style
    if any(marker in text for marker in ("摘", "关键词", "中图法分类号")) and ("[" in text or "：" in text):
        return False
    if len(text) < 12 and "正文" not in style:
        return False
    return True


def _is_template_instruction(text: str) -> bool:
    stripped = text.strip()
    return bool(
        (stripped.startswith("[") and stripped.endswith("]"))
        or ("填写" in stripped and ("[" in stripped or "。" in stripped))
        or "示例" in stripped
        or "说明" in stripped
    )


def _template_instructions(doc) -> list[dict]:
    instructions = []
    for para in doc.paragraphs:
        chunks = _split_heading_instruction_chunks(para.text)
        for chunk in chunks:
            if chunk["role"] == "TEMPLATE_INSTRUCTION":
                instructions.append({
                    "role": "TEMPLATE_INSTRUCTION",
                    "text_pattern": _text_pattern(chunk["text"]),
                    "render": False,
                })
    return instructions[:50]


def _role_sample_text(role: str, text: str) -> str:
    samples = _role_samples(role, text)
    if samples:
        return samples[0][:120]
    return text.strip()[:120]


def _role_samples(role: str, text: str) -> list[str]:
    if role in {ROLE_HEADING_1, ROLE_HEADING_2, ROLE_HEADING_3}:
        return [item["text_pattern"] for item in _heading_items(text) if item["role"] == role] or [text.strip()[:120]]
    if role == ROLE_BODY:
        return [
            chunk["text"][:120]
            for chunk in _split_heading_instruction_chunks(text)
            if chunk["role"] == "TEMPLATE_INSTRUCTION" or not _heading_items(chunk["text"])
        ][:3] or [text.strip()[:120]]
    return [text.strip()[:120]]


def _split_heading_instruction_chunks(text: str) -> list[dict]:
    lines = _logical_lines(text)
    chunks = []
    current_heading = None
    for line in lines:
        heading = _heading_items(line)
        if heading:
            current_heading = heading[0]["role"]
            chunks.append({"role": current_heading, "text": line})
        else:
            chunks.append({"role": "TEMPLATE_INSTRUCTION" if current_heading else ROLE_BODY, "text": line})
    return chunks


def _logical_lines(text: str) -> list[str]:
    lines = []
    for raw in re.split(r"[\r\n]+", text or ""):
        line = raw.strip()
        if line:
            lines.append(line)
    return lines


def _metadata_fields(doc) -> list[dict]:
    fields = []
    labels = ("报告单位", "报告时间", "报告主题", "报告编号")
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                for label in labels:
                    if label in text:
                        fields.append({
                            "label": label,
                            "placeholder": _placeholder_name(label, "metadata"),
                            "component": "METADATA_BLOCK",
                            "text_pattern": _text_pattern(text),
                        })
                        break
    deduped = []
    seen = set()
    for field in fields:
        key = (field["label"], field["text_pattern"])
        if key not in seen:
            seen.add(key)
            deduped.append(field)
    return deduped[:20]


def _signature_fields(doc) -> list[dict]:
    fields = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if re.search(r"(×××单位|盖章|单位)", text) and re.search(r"(×年×月×日|年\s*月\s*日|日期)", text):
            fields.append({
                "placeholder": "signature_unit",
                "component": "SIGNATURE_BLOCK",
                "text_pattern": _text_pattern(text),
            })
            fields.append({
                "placeholder": "signature_date",
                "component": "SIGNATURE_BLOCK",
                "text_pattern": _text_pattern(text),
            })
        elif re.search(r"(×××单位|盖章|单位)", text):
            fields.append({"placeholder": "signature_unit", "component": "SIGNATURE_BLOCK", "text_pattern": _text_pattern(text)})
        elif re.search(r"(×年×月×日|年\s*月\s*日|日期)", text):
            fields.append({"placeholder": "signature_date", "component": "SIGNATURE_BLOCK", "text_pattern": _text_pattern(text)})
    deduped = []
    seen = set()
    for field in fields:
        key = (field["placeholder"], field["text_pattern"])
        if key not in seen:
            seen.add(key)
            deduped.append(field)
    return deduped[:8]


def _physical_placeholders(doc) -> list[dict]:
    found = []
    for para in doc.paragraphs:
        for match in _PLACEHOLDER.findall(para.text):
            found.append({"text": match, "location": "paragraph"})
    for t_idx, table in enumerate(doc.tables):
        for r_idx, row in enumerate(table.rows):
            for c_idx, cell in enumerate(row.cells):
                for match in _PLACEHOLDER.findall(cell.text):
                    found.append({"text": match, "location": "table", "table": t_idx, "row": r_idx, "col": c_idx})
    return found[:50]


def _placeholder_registry(doc, role_paragraphs: dict) -> dict:
    registry = {}
    _register_placeholder(registry, "report_title", "text", "TITLE_BLOCK", True)
    _register_placeholder(registry, "report_body", "dynamic_content", "BODY_SECTION", True)
    for field in _metadata_fields(doc):
        _register_placeholder(registry, field["placeholder"], "text", "METADATA_BLOCK", False, label=field.get("label", ""))
    for field in _signature_fields(doc):
        _register_placeholder(registry, field["placeholder"], "text", "SIGNATURE_BLOCK", False)
    physical = _physical_placeholders(doc)
    for item in physical:
        key = _normalize_placeholder(item["text"])
        _register_placeholder(registry, key, "text", "UNKNOWN", False)
        registry[key]["physical_placeholder"] = item
    return registry


def _register_placeholder(registry: dict, key: str, typ: str, component: str, required: bool, label: str = "") -> None:
    if key not in registry:
        registry[key] = {
            "type": typ,
            "component": component,
            "required": required,
            "bindings": [{"component": component, "required": required}],
        }
        if label:
            registry[key]["label"] = label
        return
    item = registry[key]
    item["required"] = bool(item.get("required")) or required
    if item.get("component") in ("UNKNOWN", "METADATA_BLOCK") and component in ("TITLE_BLOCK", "BODY_SECTION"):
        item["component"] = component
    bindings = item.setdefault("bindings", [])
    if not any(b.get("component") == component for b in bindings):
        bindings.append({"component": component, "required": required})
    if label and not item.get("label"):
        item["label"] = label


def _placeholder_name(label: str, component: str = "metadata") -> str:
    mapping = {"报告单位": "report_unit", "报告时间": "report_date", "报告主题": "report_title", "报告编号": "report_no", "单位": "report_unit", "时间": "report_date", "日期": "report_date"}
    if component == "signature":
        mapping = {"单位": "signature_unit", "时间": "signature_date", "日期": "signature_date"}
    return mapping.get(label, label)


def _normalize_placeholder(text: str) -> str:
    value = text.strip("{}《》【】_ ").strip()
    mapping = {"报告标题": "report_title", "报告主题": "report_title", "报告单位": "report_unit", "报告时间": "report_date", "报告编号": "report_no", "正文": "report_body"}
    return mapping.get(value, value or "unnamed_placeholder")


def _header_footer_text(part) -> str:
    return "\n".join(p.text.strip() for p in part.paragraphs if p.text.strip())[:500]


def _paragraph_style_from_any(para) -> dict:
    pf = para.paragraph_format
    return {
        "alignment": _alignment(para.alignment, pf.alignment),
        "first_line_indent_cm": _length_value(pf.first_line_indent),
        "line_spacing": _line_spacing(pf.line_spacing),
        "space_after_pt": _pt_value(pf.space_after),
    }


def _computed_borders(para) -> dict:
    direct = _p_borders(para._p)
    if any(v is not None for v in direct.values()):
        return direct
    if para.style is not None:
        style_borders = _p_borders(para.style.element)
        if any(v is not None for v in style_borders.values()):
            return style_borders
    return {"top": None, "bottom": None, "left": None, "right": None}


def _p_borders(element) -> dict:
    result = {"top": None, "bottom": None, "left": None, "right": None}
    try:
        ppr = element.pPr if hasattr(element, "pPr") else element.find(qn("w:pPr"))
        pbdr = ppr.find(qn("w:pBdr")) if ppr is not None else None
        if pbdr is None:
            return result
        for name in result:
            node = pbdr.find(qn(f"w:{name}"))
            if node is not None:
                result[name] = _border_node(node)
    except Exception:
        return result
    return result


def _tc_borders(cell) -> dict:
    result = {"top": None, "bottom": None, "left": None, "right": None}
    try:
        tcpr = cell._tc.tcPr
        borders = tcpr.find(qn("w:tcBorders")) if tcpr is not None else None
        if borders is None:
            return result
        for name in result:
            node = borders.find(qn(f"w:{name}"))
            if node is not None:
                result[name] = _border_node(node)
    except Exception:
        return result
    return result


def _border_node(node) -> dict:
    return {
        "style": node.get(qn("w:val"), ""),
        "width": node.get(qn("w:sz"), ""),
        "color": node.get(qn("w:color"), ""),
        "space": node.get(qn("w:space"), ""),
    }


def _shading(element) -> dict | str:
    try:
        target = element.pPr if hasattr(element, "pPr") else getattr(element, "tcPr", None)
        shd = target.find(qn("w:shd")) if target is not None else None
        if shd is None:
            return "none"
        return {"fill": shd.get(qn("w:fill"), ""), "color": shd.get(qn("w:color"), ""), "val": shd.get(qn("w:val"), "")}
    except Exception:
        return "unknown"


def _outline_level(para) -> str:
    try:
        ppr = para._p.pPr
        node = ppr.find(qn("w:outlineLvl")) if ppr is not None else None
        return node.get(qn("w:val"), "") if node is not None else ""
    except Exception:
        return ""


def _widow_control(para) -> str | bool:
    try:
        ppr = para._p.pPr
        node = ppr.find(qn("w:widowControl")) if ppr is not None else None
        if node is None:
            return "unknown"
        return node.get(qn("w:val"), "1") != "0"
    except Exception:
        return "unknown"


def _contextual_spacing(para) -> str | bool:
    try:
        ppr = para._p.pPr
        node = ppr.find(qn("w:contextualSpacing")) if ppr is not None else None
        return bool(node is not None)
    except Exception:
        return "unknown"


def _tabs(pf) -> list[dict]:
    try:
        return [{"position_cm": _cm(stop.position), "alignment": str(stop.alignment), "leader": str(stop.leader)}
                for stop in pf.tab_stops]
    except Exception:
        return []


def _run_prop(run, prop: str) -> str:
    try:
        rpr = run._r.rPr if run is not None else None
        node = rpr.find(qn(f"w:{prop}")) if rpr is not None else None
        return node.get(qn("w:val"), "") if node is not None else "unknown"
    except Exception:
        return "unknown"


def _run_language(run) -> str:
    try:
        rpr = run._r.rPr if run is not None else None
        node = rpr.find(qn("w:lang")) if rpr is not None else None
        if node is None:
            return "unknown"
        return node.get(qn("w:eastAsia")) or node.get(qn("w:val")) or "unknown"
    except Exception:
        return "unknown"


def _font_name(font, style_font, normal_font, key: str, xmlfont=None) -> str:
    for candidate in (font, style_font, normal_font):
        if candidate is None:
            continue
        value = _rfont(candidate, key)
        if value:
            return value
        if key in {"ascii", "hAnsi"} and getattr(candidate, "name", None):
            return candidate.name
    # 兜底:python-docx 缓存读不到时,从样式 XML rPr 的 rFonts 解析(如 Normal 的宋体)
    if xmlfont:
        v = xmlfont.get(key) or xmlfont.get("eastAsia")
        if v:
            return v
    return "unknown"


def _rfont(font, key: str) -> str:
    try:
        element = font._element
        rfonts = element.rPr.rFonts if hasattr(element, "rPr") and element.rPr is not None else None
        if rfonts is None:
            return ""
        return rfonts.get(qn(f"w:{key}")) or ""
    except Exception:
        return ""


def _font_size(font, style_font, normal_font, xmlfont=None):
    for candidate in (font, style_font, normal_font):
        if candidate is None:
            continue
        size = getattr(candidate, "size", None)
        if size is not None:
            return round(size.pt, 1)
    if xmlfont and xmlfont.get("size_pt"):
        return xmlfont["size_pt"]
    return "unknown"


def _rpr_font_values(style):
    """从样式 XML rPr 解析实际字体(rFonts/sz)。python-docx 的 .font 缓存常读不到
    rPr 里的 rFonts/sz(如 Normal 样式定义的宋体/12pt),这里兜底,实现"忠实记录模板呈现"。"""
    if style is None:
        return None
    try:
        rPr = style.element.find(qn("w:rPr"))
        if rPr is None:
            return None
        rf = rPr.find(qn("w:rFonts"))
        out = {"ascii": None, "hAnsi": None, "eastAsia": None, "cs": None, "size_pt": None}
        if rf is not None:
            for k in ("ascii", "hAnsi", "eastAsia", "cs"):
                out[k] = rf.get(qn("w:" + k))
        sz = rPr.find(qn("w:sz"))
        if sz is not None:
            try:
                out["size_pt"] = round(int(sz.get(qn("w:val")) or 0) / 2, 1)
            except Exception:
                pass
        return out
    except Exception:
        return None


def _font_color(font, style_font, normal_font) -> str:
    for candidate in (font, style_font, normal_font):
        try:
            color = getattr(getattr(candidate, "color", None), "rgb", None)
            if color:
                return str(color)
        except Exception:
            continue
    return "000000"


def _alignment(*values) -> str:
    for value in values:
        if value is not None:
            text = str(value)
            if "CENTER" in text:
                return "center"
            if "RIGHT" in text:
                return "right"
            if "JUSTIFY" in text:
                return "justify"
            return "left"
    return "unknown"


def _length_value(*values):
    for value in values:
        if value is not None:
            return _cm(value)
    return "unknown"


def _pt_value(*values):
    for value in values:
        if value is not None:
            try:
                return round(value.pt, 1)
            except Exception:
                return "unknown"
    return "unknown"


def _line_spacing(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, Length):
            return {"type": "exact_pt", "value": round(value.pt, 1)}
        if isinstance(value, (int, float)):
            return {"type": "multiple", "value": round(float(value), 2)}
        return {"type": "raw", "value": str(value)}
    return "unknown"


def _bool_value(*values):
    for value in values:
        if value is not None:
            return bool(value)
    return "unknown"


def _pf_value(pf, name: str):
    return getattr(pf, name, None) if pf is not None else None


def _cm(value):
    if value is None:
        return "unknown"
    try:
        return round(value.cm, 2)
    except Exception:
        return "unknown"


def _count_xpath(element, xpath: str) -> int:
    try:
        return len(element.xpath(xpath))
    except Exception:
        return 0


def _text_pattern(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    text = re.sub(r"\d{4}年\d{1,2}月\d{1,2}日", "{{date}}", text)
    text = re.sub(r"\d+", "{{number}}", text)
    text = _PLACEHOLDER.sub("{{placeholder}}", text)
    return text[:120]
