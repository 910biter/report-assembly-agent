"""Fact Consolidation:把散乱 Fact 整理成情报(FactCluster / 来源关系 / 时序 / 冲突)。

- FactCluster:多个独立来源印证同一事实的聚合(多源 corroboration 是情报价值,不简单删重)
- FactRelation:corroborates / contradicts / updates / supersedes / related(关系语义由模型判断,程序存 Schema)
- 冲突、时序、信息缺口统一进入 Intelligence Ledger
"""
from __future__ import annotations

import json

from app.db import session_scope
from app.infrastructure.orm import ORMCluster, ORMRelation
from sqlalchemy import select

_RELATION_TYPES = (
    "corroborates", "contradicts", "updates", "supersedes",
    "explains", "causes", "precedes", "related",
)


class FactCluster:
    """同一事实主题的多源聚合。"""

    def __init__(self, key: str, fact_ids: list[int], sources: list[str],
                 status: str = "confirmed", note: str = ""):
        self.key = key
        self.fact_ids = fact_ids
        self.sources = sources
        self.status = status  # confirmed / conflicted / uncertain
        self.note = note

    def to_dict(self) -> dict:
        return {
            "key": self.key, "fact_ids": self.fact_ids,
            "sources": self.sources, "status": self.status, "note": self.note,
        }


def cluster_facts(facts: list[dict]) -> list[FactCluster]:
    """按内容归一化聚簇:语义相同/印证的 Fact 归入同一 Cluster。

    facts: [{"id": int, "content": str, "sources": [str], "dimension": str}]
    归并依据:归一化文本近似(2-gram 相似,复用检索层相似度语义;
    高相似即视为同主题陈述,多源合并为 corroboration)。
    """
    from app.context import _ngram_similarity

    clusters: list[FactCluster] = []
    for fact in facts:
        fid = fact["id"]
        placed = False
        for cluster in clusters:
            sample = next((f for f in facts if f["id"] in cluster.fact_ids), None)
            if sample and _ngram_similarity(sample["content"], fact["content"]) >= 0.45:
                cluster.fact_ids.append(fid)
                for s in fact.get("sources", []):
                    if s and s not in cluster.sources:
                        cluster.sources.append(s)
                placed = True
                break
        if not placed:
            clusters.append(FactCluster(
                key=fact["content"][:60],
                fact_ids=[fid],
                sources=list(fact.get("sources", [])),
            ))
    # 状态由 Consolidation 产生(非默认 confirmed):
    # 多独立来源 → corroborated;单来源 → single_source(冲突由 resolve_relations 标记 conflicted)
    for cluster in clusters:
        cluster.status = "corroborated" if len(set(cluster.sources)) > 1 else "single_source"
    return clusters


def save_cluster(cluster: FactCluster, task_id: str) -> int:
    """FactCluster 落库(intelligence 层事实真源)。"""
    with session_scope() as s:
        result = s.execute(
            ORMCluster.insert().values(
                task_id=task_id, cluster_key=cluster.key,
                fact_ids=json.dumps(cluster.fact_ids, ensure_ascii=False),
                sources=json.dumps(cluster.sources, ensure_ascii=False),
                status=cluster.status, note=cluster.note,
            )
        )
        return int(result.inserted_primary_key[0])


def save_fact_relation(source_id: int, target_id: int, relation_type: str,
                       task_id: str, evidence_quote: str = "") -> int | None:
    """FactRelation 落库:事实间关系(模型判断语义,程序存 Schema)。"""
    if relation_type not in _RELATION_TYPES:
        return None
    with session_scope() as s:
        result = s.execute(
            ORMRelation.insert().values(
                task_id=task_id, source_id=source_id, target_id=target_id,
                relation_type=relation_type, evidence_quote=evidence_quote,
            )
        )
        return int(result.inserted_primary_key[0])


def list_clusters(task_id: str) -> list[dict]:
    with session_scope() as s:
        rows = s.execute(
            select(ORMCluster)
            .where(ORMCluster.c.task_id == task_id)
            .order_by(ORMCluster.c.id)
        ).mappings().all()
        return [dict(r) for r in rows]


def list_relations(task_id: str) -> list[dict]:
    with session_scope() as s:
        rows = s.execute(
            select(ORMRelation)
            .where(ORMRelation.c.task_id == task_id)
            .order_by(ORMRelation.c.id)
        ).mappings().all()
        return [dict(r) for r in rows]


def resolve_relations(clusters: list[FactCluster], facts: list[dict],
                      task_id: str = "") -> list[dict]:
    """簇内关系判定(确定性部分):内容归一后仍存在数字/日期口径差异 → contradicts。

    关系语义的语义部分(corroborates/updates 等)由模型判断,程序只处理
    可确定的矛盾(同主题不同数值)——这是 Schema 校验,非业务语义决策。
    """
    import re as _re

    relations: list[dict] = []
    fact_by_id = {int(f["id"]): f for f in facts}
    for cluster in clusters:
        ids = [int(fid) for fid in cluster.fact_ids]
        if len(ids) < 2:
            continue
        numbers: dict[int, set[str]] = {}
        for fid in ids:
            content = str(fact_by_id.get(fid, {}).get("content", ""))
            nums = {m.group(0) for m in _re.finditer(r"\d+(?:\.\d+)?", content)}
            numbers[fid] = nums
        # 同主题但数值集合不同(如截止时间 6/15 vs 6/20)→ 冲突
        base = numbers[ids[0]]
        for fid in ids[1:]:
            if base and numbers[fid] and base != numbers[fid] and base & numbers[fid]:
                relations.append({
                    "source_id": ids[0], "target_id": fid,
                    "relation_type": "contradicts",
                    "evidence": "同主题不同数值口径",
                })
                cluster.status = "conflicted"
    for rel in relations:
        save_fact_relation(rel["source_id"], rel["target_id"], rel["relation_type"], task_id)
    return relations

