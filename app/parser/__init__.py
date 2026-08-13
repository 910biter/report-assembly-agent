"""本地材料解析:异构文档 → 结构化 Unit 列表。

只做文本/结构抽取;OCR 与图片描述经 Gateway 走远端模型。
"""
from pathlib import Path

from app.models import Unit
from app.parser.images import describe_image, ocr_image

PARSER_VERSION = "parser-v1"

# 文档内嵌图片解析上限:超过的跳过,防止大量配图拖慢主流程
_MAX_EMBEDDED_IMAGES = 3

from app.parser.office import parse_doc, parse_docx, parse_pptx, parse_xlsx
from app.parser.pdf import parse_pdf

_PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".doc": parse_doc,
    ".pptx": parse_pptx,
    ".xlsx": parse_xlsx,
}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}


def parse_file(path: str | Path) -> list[Unit]:
    """按扩展名解析单个文件,返回 Unit 列表(material_id 由调用方填充)。"""
    ext = Path(path).suffix.lower()
    if ext in _PARSERS:
        return _PARSERS[ext](path)
    if ext in _IMAGE_EXTENSIONS:
        image_bytes = Path(path).read_bytes()
        ocr_text = ocr_image(image_bytes)
        description = describe_image(image_bytes, ocr_text)
        return [Unit(material_id=0, kind="image", content=ocr_text, image_desc=description)]
    raise ValueError(f"UNSUPPORTED_FILE_TYPE: {ext}")


__all__ = [
    "PARSER_VERSION",
    "parse_file",
    "parse_pdf",
    "parse_docx",
    "parse_doc",
    "parse_pptx",
    "parse_xlsx",
    "ocr_image",
    "describe_image",
]
