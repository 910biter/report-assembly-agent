"""轻量知识关联层:实体/事件/关系抽取与关联。

不做图谱推理、不做本体学习;支撑历史报告关联、实体/事件关联与增量分析。
"""
from app.agents.base import BaseAgent
from app.db import connect

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
                save_entity(name, str(item.get("type", "")))
                entity_count += 1
        event_count = 0
        for item in payload.get("events", []):
            name = str(item.get("name", "")).strip()
            if name:
                save_event(name, str(item.get("time", "")))
                event_count += 1
        relation_count = 0
        for item in payload.get("relations", []):
            source = str(item.get("source", "")).strip()
            target = str(item.get("target", "")).strip()
            relation_type = str(item.get("type", "")).strip()
            if source and target and relation_type:
                save_relation(source, target, relation_type)
                relation_count += 1
        return {"entities": entity_count, "events": event_count, "relations": relation_count}


def save_entity(name: str, type_: str = "") -> int:
    with connect() as conn:
        row = conn.execute("SELECT id FROM entities WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO entities(name, type) VALUES(?, ?)", (name, type_))
        return cur.lastrowid


def save_event(name: str, time: str = "") -> int:
    with connect() as conn:
        row = conn.execute("SELECT id FROM events WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO events(name, time) VALUES(?, ?)", (name, time))
        return cur.lastrowid


def save_relation(source: str, target: str, relation_type: str) -> int:
    source_id = save_entity(source)
    target_id = save_entity(target)
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM relations WHERE source_entity=? AND target_entity=? AND relation_type=?",
            (source_id, target_id, relation_type),
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO relations(source_entity, target_entity, relation_type) VALUES(?, ?, ?)",
            (source_id, target_id, relation_type),
        )
        return cur.lastrowid


def load_entity_names() -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT name FROM entities ORDER BY id").fetchall()
    return [r["name"] for r in rows]


def load_events() -> list[dict]:
    """全部已沉淀事件(含时间),供时间线组织。"""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    return [dict(row) for row in rows]
