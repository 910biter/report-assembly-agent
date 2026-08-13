"""PDF 解析:文本块(带页码/段落序号)+ 内嵌图片(OCR+描述,有数量上限)。

表格内容以文本形式保留在文本块中(顺序自然),MVP 阶段不做行列还原。
"""
from pathlib import Path

import fitz

from app.models import Unit
from app.parser import _MAX_EMBEDDED_IMAGES
from app.parser.images import describe_image, ocr_image

_MIN_IMAGE_SIZE = 40  # 过滤小图标/装饰图


def parse_pdf(path: str | Path) -> list[Unit]:
    units: list[Unit] = []
    seen_xrefs: set[int] = set()
    image_count = 0
    doc = fitz.open(path)
    try:
        for page_index, page in enumerate(doc, start=1):
            for block in page.get_text("blocks", sort=True):
                text = block[4].strip()
                if not text:
                    continue
                units.append(Unit(
                    material_id=0,
                    kind="text",
                    content=text,
                    page=page_index,
                    paragraph=block[5],
                ))
            for image_info in page.get_images(full=True):
                xref = image_info[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                image_count += 1
                if image_count > _MAX_EMBEDDED_IMAGES:
                    continue  # 超过上限的图片跳过,不阻塞主流程
                try:
                    pix = fitz.Pixmap(doc, xref)
                except Exception:
                    continue
                if pix.width < _MIN_IMAGE_SIZE or pix.height < _MIN_IMAGE_SIZE:
                    continue
                if pix.n > 4:  # CMYK → RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                image_bytes = pix.tobytes("png")
                try:
                    ocr_text = ocr_image(image_bytes)
                    description = describe_image(image_bytes, ocr_text)
                except Exception:
                    ocr_text, description = "", ""
                units.append(Unit(
                    material_id=0,
                    kind="image",
                    content=ocr_text,
                    page=page_index,
                    image_desc=description,
                ))
    finally:
        doc.close()
    return units
