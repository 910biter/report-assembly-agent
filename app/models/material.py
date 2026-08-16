"""材料与来源绑定模型。"""
from dataclasses import dataclass


@dataclass
class Material:
    filename: str
    file_type: str
    path: str
    fingerprint: str
    id: int | None = None
    is_duplicate: int = 0
    duplicate_of: int | None = None


@dataclass
class Unit:
    material_id: int
    kind: str  # text / table / caption / image
    content: str
    page: int | None = None
    paragraph: int | None = None
    image_desc: str | None = None
    metadata_json: str = "{}"
    id: int | None = None


@dataclass
class Evidence:
    fact_id: int
    material_id: int
    unit_id: int
    source_file: str
    quote: str
    page: int | None = None
    paragraph: int | None = None
    id: int | None = None


@dataclass
class Claim:
    """材料中被提取的陈述:原文观点,经引用校验后可提升(promote)为 Fact。

    Material→Unit→Evidence→Claim→Fact 链路的 Claim 层:冲突检测在 Claim 层进行,
    Fact 是系统确认可用的信息,必须关联 Evidence。
    """

    material_id: int
    content: str
    quote: str = ""
    source: str = ""
    fact_type: str = "STATEMENT"  # EVENT / PERSON / LOCATION / TIME / NUMBER / STATEMENT
    dimension: str = ""
    status: str = "pending"  # pending / promoted / rejected
    fact_id: int | None = None
    id: int | None = None
