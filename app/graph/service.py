"""Task graph construction, Graph RAG and Neo4j projection.

The module deliberately has no domain vocabulary. It works with entities,
assertions and Fact IDs only; semantic interpretation stays with the model.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, delete, select, update

from app.agents.base import BaseAgent
from app.config import settings
from app.db import session_scope
from app.infrastructure.orm import (
    ORMGraphOutbox,
    ORMKGAssertion,
    ORMKGAssertionFact,
    ORMKGChangeSet,
    ORMKGEntity,
    ORMKGEntityAlias,
    ORMKGTaskMembership,
)


_GRAPH_SYSTEM = """你是证据约束的关系抽取员。只根据给定事实抽取实体和关系，不推断、不补全。
严格输出 JSON：
{
  "entities": [{"name":"实体原文","type":"人物/机构/地点/项目/技术/事件/其他","fact_ids":[1]}],
  "assertions": [{
    "subject":"实体原文", "predicate":"简短关系", "object":"实体原文或明确值",
    "object_kind":"entity/value", "event_name":"可选事件", "valid_from":"可选时间",
    "valid_to":"可选时间", "fact_ids":[1], "confidence":"high/medium/low"
  }]
}
约束：
1. fact_ids 必须来自输入且直接支撑该实体或关系。
2. 不要把抽象判断、预测、建议、标题当成实体关系。
3. 关系必须是输入事实明确表达的主体-谓词-客体，不要把同段文字强行串联。
4. object_kind 只能为 entity 或 value；不确定时使用 value。
5. 同一事实可有多个关系，但只保留有分析价值且可解释的关系。"""


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[\s\-—_·,，。；;:：()（）\[\]【】]", "", value)


def _stable_key(*parts: object) -> str:
    raw = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


@dataclass
class GraphBuildResult:
    status: str
    entities: int = 0
    assertions: int = 0
    changes: int = 0
    projected: int = 0
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "entity_count": self.entities,
            "assertion_count": self.assertions,
            "changeset_count": self.changes,
            "projected_events": self.projected,
            "error": self.error,
        }


class GraphExtractionAgent(BaseAgent):
    name = "graph_extract"
    role = _GRAPH_SYSTEM

    def extract(self, facts: list[dict]) -> dict:
        lines = []
        for fact in facts:
            fact_id = int(fact.get("id") or 0)
            content = str(fact.get("content") or "").strip()
            if fact_id and content:
                lines.append(f"Fact {fact_id}: {content}")
        if not lines:
            return {"entities": [], "assertions": []}
        return self.generate_json("已验证事实：\n" + "\n".join(lines))


class Neo4jProjector:
    """Small idempotent projection adapter; unavailable Neo4j never raises upstream."""

    def available(self) -> bool:
        return bool(settings.neo4j_uri and settings.neo4j_password)

    def _driver(self):
        from neo4j import GraphDatabase
        return GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            connection_timeout=3.0,
        )

    def project_task(self, task_id: str) -> int:
        if not self.available():
            return 0
        records = _projection_records(task_id)
        if not records["assertions"]:
            return 0
        with self._driver() as driver:
            driver.verify_connectivity()
            with driver.session(database=settings.neo4j_database) as session:
                session.run(
                    "CREATE CONSTRAINT kg_entity_key IF NOT EXISTS "
                    "FOR (n:KGEntity) REQUIRE n.entity_key IS UNIQUE"
                ).consume()
                for entity in records["entities"]:
                    session.run(
                        "MERGE (n:KGEntity {entity_key:$key}) "
                        "SET n.name=$name,n.entity_type=$entity_type,n.workspace_id=$workspace_id,"
                        "n.aliases=$aliases,n.status=$status",
                        **entity,
                    ).consume()
                for assertion in records["assertions"]:
                    session.run(
                        "MATCH (s:KGEntity {entity_key:$subject_key}) "
                        "MERGE (s)-[r:ASSERTS {assertion_key:$assertion_key}]->"
                        "(target:KGEntity {entity_key:$target_key}) "
                        "ON CREATE SET target.name=$target_name,"
                        "target.entity_type=CASE WHEN $object_key IS NULL THEN 'value' ELSE 'other' END "
                        "SET r.predicate=$predicate,r.status=$status,r.confidence=$confidence,"
                        "r.fact_ids=reduce(ids=coalesce(r.fact_ids,[]), fid IN $fact_ids | CASE WHEN fid IN ids THEN ids ELSE ids + fid END),"
                        "r.task_ids=reduce(ids=coalesce(r.task_ids,[]), tid IN $task_ids | CASE WHEN tid IN ids THEN ids ELSE ids + tid END),"
                        "r.workspace_id=$workspace_id,"
                        "r.valid_from=$valid_from,r.valid_to=$valid_to,r.event_name=$event_name",
                        **assertion,
                    ).consume()
        return len(records["assertions"])

    def neighborhood_for_fact_ids(self, task_id: str, fact_ids: list[int], limit: int = 12) -> list[dict]:
        if not self.available() or not fact_ids:
            return []
        try:
            with self._driver() as driver:
                with driver.session(database=settings.neo4j_database) as session:
                    rows = session.run(
                        "MATCH (s:KGEntity)-[seed:ASSERTS]-(m:KGEntity) "
                        "WHERE $task_id IN seed.task_ids "
                        "AND seed.status IN ['validated','confirmed'] "
                        "AND any(fid IN seed.fact_ids WHERE fid IN $fact_ids) "
                        "OPTIONAL MATCH (m)-[next:ASSERTS]-(n:KGEntity) "
                        "WHERE $task_id IN next.task_ids AND next.status IN ['validated','confirmed'] "
                        "RETURN s.name AS subject,seed.predicate AS predicate,m.name AS object,"
                        "seed.fact_ids AS fact_ids,seed.confidence AS confidence,seed.status AS status,"
                        "next.predicate AS next_predicate,n.name AS next_object,next.fact_ids AS next_fact_ids,"
                        "next.confidence AS next_confidence,next.status AS next_status "
                        "LIMIT $limit",
                        task_id=task_id, fact_ids=[int(v) for v in fact_ids], limit=limit,
                    )
                    result: list[dict] = []
                    for row in rows:
                        item = dict(row)
                        result.append({key: item.get(key) for key in ("subject", "predicate", "object", "fact_ids", "confidence", "status")})
                        if item.get("next_predicate") and item.get("next_object"):
                            result.append({
                                "subject": item.get("object"), "predicate": item.get("next_predicate"),
                                "object": item.get("next_object"), "fact_ids": item.get("next_fact_ids") or [],
                                "confidence": item.get("next_confidence"), "status": item.get("next_status"),
                            })
                    return result[:limit]
        except Exception:
            return []


def _projection_records(task_id: str) -> dict:
    with session_scope() as s:
        memberships = s.execute(
            select(ORMKGTaskMembership.c.entity_id, ORMKGTaskMembership.c.assertion_id)
            .where(ORMKGTaskMembership.c.task_id == task_id, ORMKGTaskMembership.c.status == "active")
        ).mappings().all()
        assertion_ids = {int(row["assertion_id"]) for row in memberships if row["assertion_id"] is not None}
        entity_ids = {int(row["entity_id"]) for row in memberships if row["entity_id"] is not None}
        assertions = s.execute(select(ORMKGAssertion).where(ORMKGAssertion.c.id.in_(assertion_ids))).mappings().all() if assertion_ids else []
        for item in assertions:
            entity_ids.add(int(item["subject_entity_id"]))
            if item["object_entity_id"] is not None:
                entity_ids.add(int(item["object_entity_id"]))
        entities = s.execute(select(ORMKGEntity).where(ORMKGEntity.c.id.in_(entity_ids))).mappings().all() if entity_ids else []
        facts = s.execute(select(ORMKGAssertionFact).where(ORMKGAssertionFact.c.assertion_id.in_(assertion_ids))).mappings().all() if assertion_ids else []
    by_id = {int(row["id"]): row for row in entities}
    fact_map: dict[int, list[int]] = {}
    for row in facts:
        fact_map.setdefault(int(row["assertion_id"]), []).append(int(row["fact_id"]))
    return {
        "entities": [{
            "key": row["entity_key"], "name": row["canonical_name"], "entity_type": row["entity_type"],
            "workspace_id": row["workspace_id"], "aliases": _loads(row["aliases_json"], []), "status": row["status"],
        } for row in entities],
        "assertions": [{
            "assertion_key": row["assertion_key"], "subject_key": by_id[int(row["subject_entity_id"])]["entity_key"],
            "subject_name": by_id[int(row["subject_entity_id"])]["canonical_name"],
            "object_key": by_id.get(int(row["object_entity_id"]))["entity_key"] if row["object_entity_id"] is not None and by_id.get(int(row["object_entity_id"])) else None,
            "object_name": by_id.get(int(row["object_entity_id"]))["canonical_name"] if row["object_entity_id"] is not None and by_id.get(int(row["object_entity_id"])) else "",
            "object_value": row["object_value"],
            "target_key": by_id.get(int(row["object_entity_id"]))["entity_key"] if row["object_entity_id"] is not None and by_id.get(int(row["object_entity_id"])) else "value-" + _stable_key(row["workspace_id"], row["object_value"]),
            "target_name": by_id.get(int(row["object_entity_id"]))["canonical_name"] if row["object_entity_id"] is not None and by_id.get(int(row["object_entity_id"])) else row["object_value"],
            "predicate": row["predicate"], "status": row["status"], "confidence": row["confidence"],
            "fact_ids": fact_map.get(int(row["id"]), []), "task_ids": [task_id], "workspace_id": row["workspace_id"],
            "valid_from": row["valid_from"], "valid_to": row["valid_to"], "event_name": row["event_name"],
        } for row in assertions],
    }


class GraphService:
    def __init__(self) -> None:
        self.extractor = GraphExtractionAgent()
        self.projector = Neo4jProjector()

    @property
    def mode(self) -> str:
        return str(settings.graph_mode or "off").strip().lower()

    def build_task_graph(self, task_id: str, facts: list[dict], workspace_id: str = "",
                         active_fact_ids: set[int] | None = None) -> GraphBuildResult:
        if self.mode == "off" or not facts:
            return GraphBuildResult(status="skipped")
        workspace_id = workspace_id or settings.graph_workspace_id
        started = time.time()
        try:
            payloads = [self.extractor.extract(batch) for batch in _fact_batches(facts)]
            entity_count = assertion_count = changes = 0
            with session_scope() as s:
                valid_fact_ids = {int(item["id"]) for item in facts if item.get("id") is not None}
                for payload in payloads:
                    entities = self._upsert_entities(s, task_id, workspace_id, payload.get("entities") or [], valid_fact_ids)
                    entity_count += len(entities)
                    assertion_count += self._upsert_assertions(
                        s, task_id, workspace_id, payload.get("assertions") or [], entities, valid_fact_ids
                    )
                self._reconcile_task_assertions(
                    s, task_id, workspace_id,
                    active_fact_ids if active_fact_ids is not None else valid_fact_ids,
                )
                changes = self._enqueue_projection(s, task_id, workspace_id)
            projected = self.project_pending(limit=1) if self.mode == "active" else 0
            return GraphBuildResult("ready", entity_count, assertion_count, changes, projected)
        except Exception as exc:
            return GraphBuildResult("degraded", error=str(exc)[:300])
        finally:
            _ = started

    def _upsert_entities(self, s, task_id: str, workspace_id: str, payload: list[dict], valid_fact_ids: set[int]) -> dict[str, int]:
        resolved: dict[str, int] = {}
        for item in payload:
            name = str(item.get("name") or "").strip()
            fact_ids = _int_ids(item.get("fact_ids"), valid_fact_ids)
            if not name or not fact_ids:
                continue
            normalized = _normalized(name)
            row = s.execute(
                select(ORMKGEntity.c.id).where(
                    ORMKGEntity.c.workspace_id == workspace_id,
                    ORMKGEntity.c.canonical_name == name,
                )
            ).mappings().first()
            if row is None:
                alias_row = s.execute(
                    select(ORMKGEntityAlias.c.entity_id)
                    .join(ORMKGEntity, ORMKGEntity.c.id == ORMKGEntityAlias.c.entity_id)
                    .where(
                        ORMKGEntityAlias.c.normalized_alias == normalized,
                        ORMKGEntity.c.workspace_id == workspace_id,
                    )
                ).mappings().first()
                row = {"id": alias_row["entity_id"]} if alias_row else None
            if row is None:
                key = "ent-" + _stable_key(workspace_id, normalized)
                result = s.execute(ORMKGEntity.insert().values(
                    entity_key=key, workspace_id=workspace_id, canonical_name=name,
                    entity_type=str(item.get("type") or "other")[:64], aliases_json=json.dumps([name], ensure_ascii=False),
                ))
                entity_id = int(result.inserted_primary_key[0])
            else:
                entity_id = int(row["id"])
            alias_exists = s.execute(select(ORMKGEntityAlias.c.id).where(
                ORMKGEntityAlias.c.entity_id == entity_id,
                ORMKGEntityAlias.c.normalized_alias == normalized,
            )).mappings().first()
            if not alias_exists:
                s.execute(ORMKGEntityAlias.insert().values(
                    entity_id=entity_id, alias=name, normalized_alias=normalized, source_task_id=task_id,
                ))
            self._membership(s, task_id, entity_id=entity_id)
            resolved[_normalized(name)] = entity_id
        return resolved

    def _upsert_assertions(self, s, task_id: str, workspace_id: str, payload: list[dict], entities: dict[str, int], valid_fact_ids: set[int]) -> int:
        count = 0
        for item in payload:
            subject = _normalized(item.get("subject"))
            object_text = str(item.get("object") or "").strip()
            predicate = str(item.get("predicate") or "").strip()[:120]
            fact_ids = _int_ids(item.get("fact_ids"), valid_fact_ids)
            if not subject or not predicate or not object_text or not fact_ids or subject not in entities:
                continue
            object_kind = "entity" if str(item.get("object_kind") or "").lower() == "entity" else "value"
            object_id = entities.get(_normalized(object_text)) if object_kind == "entity" else None
            if object_kind == "entity" and object_id is None:
                continue
            object_key = str(object_id or _normalized(object_text))
            assertion_key = "ast-" + _stable_key(workspace_id, entities[subject], predicate, object_key, item.get("valid_from"), item.get("valid_to"))
            row = s.execute(select(ORMKGAssertion.c.id).where(ORMKGAssertion.c.assertion_key == assertion_key)).mappings().first()
            if row is None:
                result = s.execute(ORMKGAssertion.insert().values(
                    assertion_key=assertion_key, workspace_id=workspace_id, task_id=task_id,
                    subject_entity_id=entities[subject], predicate=predicate, object_entity_id=object_id,
                    object_value="" if object_id else object_text, object_kind=object_kind,
                    event_name=str(item.get("event_name") or "")[:256], valid_from=str(item.get("valid_from") or "")[:64],
                    valid_to=str(item.get("valid_to") or "")[:64], confidence=_confidence(item.get("confidence")),
                    status="validated", extraction_method="llm",
                ))
                assertion_id = int(result.inserted_primary_key[0])
                self._changeset(s, task_id, workspace_id, "ADD_ASSERTION", {"assertion_id": assertion_id, "fact_ids": fact_ids})
                count += 1
            else:
                assertion_id = int(row["id"])
            for fact_id in fact_ids:
                fact_link = s.execute(select(ORMKGAssertionFact.c.assertion_id).where(
                    ORMKGAssertionFact.c.assertion_id == assertion_id,
                    ORMKGAssertionFact.c.fact_id == fact_id,
                )).mappings().first()
                if not fact_link:
                    s.execute(ORMKGAssertionFact.insert().values(assertion_id=assertion_id, fact_id=fact_id))
            self._membership(s, task_id, assertion_id=assertion_id)
        return count

    def _membership(self, s, task_id: str, entity_id: int | None = None, assertion_id: int | None = None) -> None:
        if entity_id is not None:
            exists = s.execute(select(ORMKGTaskMembership.c.id).where(
                ORMKGTaskMembership.c.task_id == task_id, ORMKGTaskMembership.c.entity_id == entity_id,
                ORMKGTaskMembership.c.role == "observed",
            )).mappings().first()
            if not exists:
                s.execute(ORMKGTaskMembership.insert().values(task_id=task_id, entity_id=entity_id, role="observed"))
        if assertion_id is not None:
            exists = s.execute(select(ORMKGTaskMembership.c.id).where(
                ORMKGTaskMembership.c.task_id == task_id, ORMKGTaskMembership.c.assertion_id == assertion_id,
                ORMKGTaskMembership.c.role == "observed",
            )).mappings().first()
            if not exists:
                s.execute(ORMKGTaskMembership.insert().values(task_id=task_id, assertion_id=assertion_id, role="observed"))

    def _reconcile_task_assertions(self, s, task_id: str, workspace_id: str,
                                   active_fact_ids: set[int]) -> None:
        """Mark stale task memberships without deleting historical assertions."""
        rows = s.execute(
            select(ORMKGTaskMembership.c.id, ORMKGTaskMembership.c.assertion_id)
            .where(
                ORMKGTaskMembership.c.task_id == task_id,
                ORMKGTaskMembership.c.assertion_id.is_not(None),
                ORMKGTaskMembership.c.status == "active",
            )
        ).mappings().all()
        assertion_ids = {int(row["assertion_id"]) for row in rows}
        if not assertion_ids:
            return
        fact_rows = s.execute(select(ORMKGAssertionFact).where(
            ORMKGAssertionFact.c.assertion_id.in_(assertion_ids)
        )).mappings().all()
        by_assertion: dict[int, set[int]] = {}
        for row in fact_rows:
            by_assertion.setdefault(int(row["assertion_id"]), set()).add(int(row["fact_id"]))
        for row in rows:
            assertion_id = int(row["assertion_id"])
            if by_assertion.get(assertion_id, set()) & active_fact_ids:
                continue
            s.execute(update(ORMKGTaskMembership).where(
                ORMKGTaskMembership.c.id == row["id"]
            ).values(status="superseded"))
            self._changeset(s, task_id, workspace_id, "REMOVE_ASSERTION_MEMBERSHIP", {
                "assertion_id": assertion_id,
                "reason": "no_active_supporting_fact",
            })

    def _changeset(self, s, task_id: str, workspace_id: str, change_type: str, payload: dict,
                   report_version_id: int | None = None) -> None:
        key = "chg-" + _stable_key(task_id, change_type, json.dumps(payload, sort_keys=True))
        if not s.execute(select(ORMKGChangeSet.c.id).where(ORMKGChangeSet.c.changeset_key == key)).mappings().first():
            s.execute(ORMKGChangeSet.insert().values(
                changeset_key=key, workspace_id=workspace_id, task_id=task_id,
                report_version_id=report_version_id, change_type=change_type,
                payload_json=json.dumps(payload, ensure_ascii=False),
            ))

    def _enqueue_projection(self, s, task_id: str, workspace_id: str) -> int:
        key = "outbox-" + _stable_key("sync_task", task_id, workspace_id)
        row = s.execute(select(ORMGraphOutbox.c.id).where(ORMGraphOutbox.c.event_key == key)).mappings().first()
        if row:
            s.execute(update(ORMGraphOutbox).where(ORMGraphOutbox.c.id == row["id"]).values(status="pending", last_error=""))
        else:
            s.execute(ORMGraphOutbox.insert().values(
                event_key=key, event_type="sync_task", payload_json=json.dumps({"task_id": task_id, "workspace_id": workspace_id}),
            ))
        return 1

    def project_pending(self, limit: int = 20) -> int:
        if not self.projector.available():
            return 0
        with session_scope() as s:
            rows = s.execute(select(ORMGraphOutbox).where(ORMGraphOutbox.c.status == "pending").order_by(ORMGraphOutbox.c.id).limit(limit)).mappings().all()
        done = 0
        for row in rows:
            payload = _loads(row["payload_json"], {})
            try:
                self.projector.project_task(str(payload.get("task_id") or ""))
                with session_scope() as s:
                    s.execute(update(ORMGraphOutbox).where(ORMGraphOutbox.c.id == row["id"]).values(status="projected", projected_at=time.strftime("%Y-%m-%d %H:%M:%S"), attempts=int(row["attempts"] or 0) + 1))
                done += 1
            except Exception as exc:
                with session_scope() as s:
                    s.execute(update(ORMGraphOutbox).where(ORMGraphOutbox.c.id == row["id"]).values(status="pending", attempts=int(row["attempts"] or 0) + 1, last_error=str(exc)[:300]))
        return done

    def promote_task_graph(self, task_id: str, report_version_id: int | None = None) -> int:
        with session_scope() as s:
            assertion_ids = [int(row["assertion_id"]) for row in s.execute(select(ORMKGTaskMembership.c.assertion_id).where(
                ORMKGTaskMembership.c.task_id == task_id, ORMKGTaskMembership.c.assertion_id.is_not(None), ORMKGTaskMembership.c.status == "active",
            )).mappings().all()]
            if not assertion_ids:
                return 0
            s.execute(update(ORMKGAssertion).where(ORMKGAssertion.c.id.in_(assertion_ids), ORMKGAssertion.c.status == "validated").values(status="confirmed"))
            workspace = settings.graph_workspace_id
            self._changeset(
                s, task_id, workspace, "PUBLISH_TASK_GRAPH",
                {"assertion_ids": assertion_ids, "report_version_id": report_version_id},
                report_version_id=report_version_id,
            )
            self._enqueue_projection(s, task_id, workspace)
        return len(assertion_ids)

    def context_for_analysis(self, task_id: str, facts: list[dict], limit: int = 12) -> str:
        if self.mode != "active":
            return ""
        fact_ids = [int(item["id"]) for item in facts if item.get("id") is not None]
        rows = self.projector.neighborhood_for_fact_ids(task_id, fact_ids, limit=limit)
        if not rows:
            rows = self._postgres_neighborhood(task_id, fact_ids, limit)
        if not rows:
            return ""
        lines = []
        for row in rows:
            fact_refs = "、".join(str(v) for v in row.get("fact_ids") or [])
            lines.append(f"- {row.get('subject','')} —{row.get('predicate','相关')}→ {row.get('object','')}（依据 Fact:{fact_refs}；{row.get('confidence','medium')}）")
        return "关系图谱（仅作交叉印证；必须以 Fact 与 Evidence 为准）：\n" + "\n".join(lines)

    def _postgres_neighborhood(self, task_id: str, fact_ids: list[int], limit: int) -> list[dict]:
        if not fact_ids:
            return []
        with session_scope() as s:
            rows = s.execute(
                select(ORMKGAssertion, ORMKGAssertionFact.c.fact_id, ORMKGEntity.c.canonical_name.label("subject"))
                .join(ORMKGAssertionFact, ORMKGAssertionFact.c.assertion_id == ORMKGAssertion.c.id)
                .join(ORMKGTaskMembership, ORMKGTaskMembership.c.assertion_id == ORMKGAssertion.c.id)
                .join(ORMKGEntity, ORMKGEntity.c.id == ORMKGAssertion.c.subject_entity_id)
                .where(ORMKGTaskMembership.c.task_id == task_id, ORMKGAssertionFact.c.fact_id.in_(fact_ids), ORMKGAssertion.c.status.in_(["validated", "confirmed"]))
                .limit(limit)
            ).mappings().all()
            object_ids = {int(row["object_entity_id"]) for row in rows if row["object_entity_id"] is not None}
            objects = s.execute(select(ORMKGEntity.c.id, ORMKGEntity.c.canonical_name).where(ORMKGEntity.c.id.in_(object_ids))).mappings().all() if object_ids else []
        names = {int(row["id"]): row["canonical_name"] for row in objects}
        grouped: dict[int, dict] = {}
        for row in rows:
            item = grouped.setdefault(int(row["id"]), {
                "subject": row["subject"], "predicate": row["predicate"],
                "object": names.get(int(row["object_entity_id"])) if row["object_entity_id"] is not None else row["object_value"],
                "fact_ids": [], "confidence": row["confidence"],
            })
            item["fact_ids"].append(int(row["fact_id"]))
        return list(grouped.values())

    def task_graph(self, task_id: str) -> dict:
        records = _projection_records(task_id)
        return {
            "mode": self.mode,
            "neo4j_configured": self.projector.available(),
            "nodes": records["entities"],
            "edges": records["assertions"],
            "stats": {"entity_count": len(records["entities"]), "assertion_count": len(records["assertions"])},
        }

    def has_task_graph(self, task_id: str) -> bool:
        with session_scope() as s:
            row = s.execute(select(ORMKGTaskMembership.c.id).where(
                ORMKGTaskMembership.c.task_id == task_id,
                ORMKGTaskMembership.c.assertion_id.is_not(None),
                ORMKGTaskMembership.c.status == "active",
            ).limit(1)).mappings().first()
        return row is not None

    def changesets(self, task_id: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(ORMKGChangeSet).where(ORMKGChangeSet.c.task_id == task_id).order_by(ORMKGChangeSet.c.id.desc())).mappings().all()
        return [{**dict(row), "payload": _loads(row["payload_json"], {})} for row in rows]


def _fact_batches(facts: list[dict]) -> list[list[dict]]:
    """Partition complete facts by the model's physical prompt budget, never trim text."""
    max_chars = max(8_000, int((settings.model_context_window_tokens - settings.generation_reserve_tokens - settings.prompt_overhead_tokens) * 3.2))
    batches: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for fact in facts:
        text_size = len(str(fact.get("content") or "")) + 32
        if current and size + text_size > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(fact)
        size += text_size
    if current:
        batches.append(current)
    return batches


def _int_ids(values: Any, allowed: set[int]) -> list[int]:
    result = []
    for value in values or []:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed in allowed:
            result.append(parsed)
    return list(dict.fromkeys(result))


def _confidence(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"high", "medium", "low"} else "medium"


graph_service = GraphService()
