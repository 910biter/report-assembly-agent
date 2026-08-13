"""图片处理:OCR(远端 glm-ocr)+ 简短描述(远端生成模型)。

单张图片失败时降级为空串,不让整个文档解析中断。
小图标/logo(尺寸过小)直接跳过,不调用模型(懒加载过滤)。
"""
import io

from app.gateway import model_gateway

try:
    from PIL import Image
except ImportError:
    Image = None  # 未装 pillow 时跳过尺寸过滤(不阻塞)

_MIN_SIZE = 40  # 小于该尺寸视为图标/logo/装饰图,跳过处理


def _is_trivial_image(image_bytes: bytes) -> bool:
    """小图/logo 过滤:解码失败或尺寸过小 → 跳过(不调模型、不阻塞)。"""
    if Image is None:
        return False
    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            return im.width < _MIN_SIZE or im.height < _MIN_SIZE
    except Exception:
        return False  # 解码失败交给上层容错


def ocr_image(image_bytes: bytes) -> str:
    if _is_trivial_image(image_bytes):
        return ""
    return model_gateway.ocr(image_bytes)


def describe_image(image_bytes: bytes, ocr_text: str = "") -> str:
    """生成一句话图片描述。

    有 OCR 文本时基于文本概括(不需要视觉能力);纯图片时尝试视觉描述,
    模型不支持则降级为空串。
    """
    if _is_trivial_image(image_bytes):
        return ""
    if ocr_text.strip():
        prompt = f"根据以下 OCR 文本,用一句话概括这张图片的内容:\n{ocr_text[:500]}"
        try:
            return model_gateway.generate(prompt)
        except Exception:
            return ""
    try:
        return model_gateway.describe_image(
            image_bytes, "用一句话描述这张图片的内容(主体、场景、关键信息),不要解释。"
        )
    except Exception:
        return ""
