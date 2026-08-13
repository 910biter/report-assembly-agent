"""事实、冲突、推断模型。"""
from dataclasses import dataclass, field


@dataclass
class Fact:
    content: str
    dimension: str = ""
    source_level: str = "MATERIAL_FACT"
    fact_type: str = "STATEMENT"  # EVENT / PERSON / LOCATION / TIME / NUMBER / STATEMENT
    evidence_ids: list[int] = field(default_factory=list)
    conflict_ids: list[int] = field(default_factory=list)
    id: int | None = None


@dataclass
class Conflict:
    fact_key: str
    entries: list[dict] = field(default_factory=list)  # [{source_file, quote, statement}]
    claim_ids: list[int] = field(default_factory=list)
    status: str = "unresolved"
    id: int | None = None


@dataclass
class Inference:
    content: str
    source_level: str  # MATERIAL_INFERENCE / EXTERNAL_INFORMATION
    based_fact_ids: list[int] = field(default_factory=list)
    reasoning_chain: str = ""
    dimension: str = ""
    analysis_type: str = ""  # TREND / IMPACT / RISK / CAUSE / PREDICTION
    id: int | None = None
