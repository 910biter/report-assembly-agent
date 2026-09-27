"""Intelligence Ledger:情报底稿(Evidence → 情报的聚合视图)。

Analysis 以后读 Ledger 而非散乱 Fact。包含:
- Evidence Needs 覆盖状态(supported/partial/missing/conflicted/unknown)
- Confirmed Facts + FactClusters(多源印证)
- FactRelations(来源关系/冲突/时序)
- Information Gaps(迭代补检后仍缺的信息——接受 gap,不编造)
"""
from __future__ import annotations

import json

from app.db import session_scope
from app.infrastructure.orm import ORMTaskArtifact
from sqlalchemy import select
from app.intelligence.consolidate import list_clusters, list_relations


def build_ledger(task_id: str, needs: list[dict], facts: list[dict],
                 gaps: list[dict] | None = None, *, coverage: dict | None = None) -> dict:
    """构建情报底稿(快照视图,不落库——由调用方决定持久化)。

    facts: [{"id", "content", "dimension", "sources": [str]}]
    """
    clusters = list_clusters(task_id)
    relations = list_relations(task_id)
    coverage = coverage or {"status": "unknown"}
    uncovered_ids = {int(value) for value in coverage.get("uncovered_need_ids") or []}

    # Need 覆盖状态:按维度/need 内容匹配事实
    need_coverage: list[dict] = []
    for need_index, need in enumerate(needs, start=1):
        need_index = int(need.get("need_id") or need_index)
        need_text = str(need.get("need", ""))
        matched = [f for f in facts if int(f.get("need_id") or 0) == need_index]
        if coverage.get("status") != "complete":
            status = "unknown"
        elif need_index in uncovered_ids:
            status = "partial" if matched else "missing"
        else:
            status = "supported" if matched else "unknown"
        need_coverage.append({
            "need_id": need_index,
            "need": need_text,
            "dimension": need.get("dimension", ""),
            "priority": need.get("priority", "medium"),
            "status": status,
            "supported_fact_ids": [f["id"] for f in matched],
            "supported_fact_count": len(matched),
        })

    # 信息缺口:迭代补检后仍未证实的需求(接受 gap,不编造)
    info_gaps: list[str] = []
    for need in needs:
        status = next((n["status"] for n in need_coverage if n["need"] == need.get("need", "")), "missing")
        if status in ("missing", "partial"):
            info_gaps.append(str(need.get("need", "")))
    for gap in gaps or []:
        if str(gap.get("need", "")) not in info_gaps:
            info_gaps.append(str(gap.get("need", "")))

    return {
        "task_id": task_id,
        "need_coverage": need_coverage,
        "confirmed_facts": [{"id": f["id"], "content": f["content"],
                             "dimension": f.get("dimension", ""),
                             "sources": f.get("sources", [])} for f in facts],
        "fact_clusters": [dict(c) for c in clusters],
        "fact_relations": [dict(r) for r in relations],
        "information_gaps": info_gaps,
        "unverified_needs": [item["need"] for item in need_coverage if item["status"] == "unknown"],
        "coverage_audit": coverage,
        "confidence": _confidence(need_coverage),
    }





def _confidence(need_coverage: list[dict]) -> float:
    """任务置信度:已证实需求占比(只读统计,不参与业务决策)。"""
    if not need_coverage:
        return 0.0
    supported = sum(1 for n in need_coverage if n["status"] == "supported")
    return round(supported / len(need_coverage), 2)


def persist_ledger(task_id: str, ledger: dict) -> None:
    """Ledger 快照落库(可审计)。"""
    with session_scope() as s:
        exists = s.execute(
            select(ORMTaskArtifact.c.id).where(
                ORMTaskArtifact.c.task_id == task_id, ORMTaskArtifact.c.stage == "ledger"
            )
        ).first()
        values = dict(task_id=task_id, stage="ledger",
                      payload=json.dumps(ledger, ensure_ascii=False))
        if exists:
            s.execute(
                ORMTaskArtifact.update().where(
                    ORMTaskArtifact.c.task_id == task_id, ORMTaskArtifact.c.stage == "ledger"
                ).values(payload=values["payload"])
            )
        else:
            s.execute(ORMTaskArtifact.insert().values(**values))
