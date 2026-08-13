"""轻量知识关联层模型(实体/事件/关系)。"""
from dataclasses import dataclass, field


@dataclass
class Entity:
    name: str
    type: str = ""
    aliases: list[str] = field(default_factory=list)
    id: int | None = None


@dataclass
class Event:
    name: str
    time: str = ""
    entity_ids: list[int] = field(default_factory=list)
    fact_ids: list[int] = field(default_factory=list)
    id: int | None = None


@dataclass
class Relation:
    source_entity: int
    target_entity: int
    relation_type: str
    fact_ids: list[int] = field(default_factory=list)
    id: int | None = None
