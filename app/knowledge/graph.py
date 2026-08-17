"""轻量知识关联层:实体/事件/关系抽取与关联。

不做图谱推理、不做本体学习;支撑历史报告关联、实体/事件关联与增量分析。
"""
from app.agents.base import BaseAgent
from app.db import session_scope
from app.infrastructure.orm import Base
from sqlalchemy import select


EntityTable = Base.metadata.tables["entities"]
EventTable = Base.metadata.tables["events"]
RelationTable = Base.metadata.tables["relations"]

_SYSTEM = """你是知识抽取员。从情报材料中抽取实体、事件与关系。
严格输出 JSON,不要任何解释:
{
  "entities": [{"name": "实体名", "type": "机构/人物/地区/事件/其他"}],
  "events": [{"name": "事件名", "time": "时间或空"}],
  "relations": [{"source": "实体名", "target": "实体名", "type": "关系类型"}]
}
只抽取文本中明确出现的信息,不推断、不补充。"""


class KnowledgeAgent(BaseAgent):
    name = "knowledge"
    role = _SYSTEM

    def extract(self, texts: list[str]) -> dict:
        """抽取实体/事件/关系并入库,返回计数。"""
        if not texts:
            return {"entities": 0, "events": 0, "relations": 0}
        prompt = "材料文本:\n" + "\n\n---\n\n".join(text[:2000] for text in texts)[:15000]
        payload = self.generate_json(prompt)
        entity_count = 0
        for item in payload.get("entities", []):
            name = str(item.get("name", "")).strip()
            if name:
                save_entity(name, str(item.get("type", "")), task_id=str(getattr(self, "task_id", "")))
                entity_count += 1
        event_count = 0
        for item in payload.get("events", []):
            name = str(item.get("name", "")).strip()
            if name:
                save_event(name, str(item.get("time", "")), task_id=str(getattr(self, "task_id", "")))
                event_count += 1
        relation_count = 0
        for item in payload.get("relations", []):
            source = str(item.get("source", "")).strip()
            target = str(item.get("target", "")).strip()
            relation_type = str(item.get("type", "")).strip()
            if source and target and relation_type:
                save_relation(source, target, relation_type, task_id=str(getattr(self, "task_id", "")))
                relation_count += 1
        return {"entities": entity_count, "events": event_count, "relations": relation_count}


def save_entity(name: str, type_: str = "", task_id: str = "") -> int:
    with session_scope() as s:
        row = s.execute(
            select(EntityTable.c.id).where(
                EntityTable.c.name == name, EntityTable.c.task_id == task_id
            )
        ).mappings().first()
        if row:
            return row["id"]
        result = s.execute(
            EntityTable.insert().values(name=name, type=type_, task_id=task_id)
        )
        return int(result.inserted_primary_key[0])


def save_event(name: str, time: str = "", task_id: str = "") -> int:
    with session_scope() as s:
        row = s.execute(
            select(EventTable.c.id).where(
                EventTable.c.name == name, EventTable.c.task_id == task_id
            )
        ).mappings().first()
        if row:
            return row["id"]
        result = s.execute(
            EventTable.insert().values(name=name, time=time, task_id=task_id)
        )
        return int(result.inserted_primary_key[0])


def save_relation(source: str, target: str, relation_type: str, task_id: str = "") -> int:
    source_id = save_entity(source, task_id=task_id)
    target_id = save_entity(target, task_id=task_id)
    with session_scope() as s:
        row = s.execute(
            select(RelationTable.c.id).where(
                RelationTable.c.source_entity == source_id,
                RelationTable.c.target_entity == target_id,
                RelationTable.c.relation_type == relation_type,
                RelationTable.c.task_id == task_id,
            )
        ).mappings().first()
        if row:
            return row["id"]
        result = s.execute(
            RelationTable.insert().values(
                source_entity=source_id, target_entity=target_id,
                relation_type=relation_type, task_id=task_id,
            )
        )
        return int(result.inserted_primary_key[0])


def load_entity_names(task_id: str = "") -> list[str]:
    with session_scope() as s:
        rows = s.execute(
            select(EntityTable.c.name)
            .where(EntityTable.c.task_id == task_id)
            .order_by(EntityTable.c.id)
        ).mappings().all()
    return [r["name"] for r in rows]


def load_events(task_id: str = "") -> list[dict]:
    """全部已沉淀事件(含时间),供时间线组织。"""
    with session_scope() as s:
        rows = s.execute(
            select(EventTable)
            .where(EventTable.c.task_id == task_id)
            .order_by(EventTable.c.id)
        ).mappings().all()
    return [dict(row) for row in rows]
