"""New-material comparison against an immutable report version.

This is deliberately separate from incremental report writing.  It discovers
and records changes, but never mutates the baseline report or starts a rewrite.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections import Counter
from typing import Any

from sqlalchemy import delete, insert, select, update

from app.db import session_scope
from app.gateway import model_gateway
from app.infrastructure.orm import (
    ORMEvidence,
    ORMFact,
    ORMInference,
    ORMMaterialComparisonItem,
    ORMMaterialComparisonRun,
    ORMShortMemory,
)
from app.llm_scheduler import invoke
from app.report_versions import get_report_version

CHANGE_TYPES = {
    "addition", "corroboration", "refinement", "update", "conflict",
    "weakening", "related", "irrelevant", "uncertain",
}

_CLASSIFY_PROMPT = """你是新增材料变化核验员。请比较“新增事实”和候选基线事实，判断它对基线报告的真实影响。
只输出 JSON：
{
  "items": [{
    "new_fact_id": 1,
    "baseline_fact_id": 2或null,
    "change_type": "addition/corroboration/refinement/update/conflict/weakening/related/irrelevant/uncertain",
    "confidence": "high/medium/low",
    "title": "简洁变化标题",
    "rationale": "一句话说明判断依据"
  }]
}

口径：
- addition：基线中没有同一断言，且与任务有关；
- corroboration：独立新来源支持已有断言，没有增加关键限定；
- refinement：为已有断言增加主体、时间、数量、条件或范围；
- update：同一事项出现更晚版本、状态或替代值；
- conflict：相同主体、事项、时间/适用范围下不能同时成立；
- weakening：不直接构成相反事实，但削弱既有判断；
- related：与既有事实或报告主题相关，但尚不足以构成新增断言、补强、细化、更新或冲突；
- irrelevant：真实但不影响当前报告目标；
- uncertain：证据不足以稳定分类。
不得仅因措辞不同判为冲突，不得猜测材料中没有的信息。

任务目标：{focus}
待比较数据：
{payload}
"""


def create_comparison_run(task_id: str, report_id: int, base_version_id: int,
                          material_ids: list[int], focus: str = "") -> dict[str, Any]:
    key = uuid.uuid4().hex
    with session_scope() as s:
        result = s.execute(insert(ORMMaterialComparisonRun).values(
            comparison_key=key,
            task_id=task_id,
            report_id=int(report_id),
            base_version_id=int(base_version_id),
            status="created",
            focus=focus.strip(),
            material_ids_json=_dump([int(value) for value in material_ids]),
            summary_json="{}",
        ))
        comparison_id = int(result.inserted_primary_key[0])
    return get_comparison(comparison_id) or {"id": comparison_id}


def list_comparisons(report_id: int) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(
            select(ORMMaterialComparisonRun)
            .where(ORMMaterialComparisonRun.c.report_id == int(report_id))
            .order_by(ORMMaterialComparisonRun.c.id.desc())
        ).mappings().all()
    return [_run_detail(row, include_items=False) for row in rows]


def get_comparison(comparison_id: int) -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.execute(select(ORMMaterialComparisonRun).where(
            ORMMaterialComparisonRun.c.id == int(comparison_id)
        )).mappings().first()
        if row is None:
            return None
        items = s.execute(select(ORMMaterialComparisonItem).where(
            ORMMaterialComparisonItem.c.comparison_id == int(comparison_id)
        ).order_by(ORMMaterialComparisonItem.c.id)).mappings().all()
    result = _run_detail(row, include_items=False)
    result_items = [_item_detail(item) for item in items]
    _attach_new_fact_evidence(result_items)
    result["items"] = result_items
    baseline = get_report_version(int(row["base_version_id"]))
    result["baseline"] = {
        "id": int(row["base_version_id"]),
        "title": str((baseline or {}).get("title") or ""),
        "version_label": str((baseline or {}).get("version_label") or (baseline or {}).get("version_no") or ""),
    }
    result["document"] = _comparison_document(baseline or {}, result_items)
    return result


def update_comparison_item(comparison_id: int, item_id: int, *, status: str,
                           user_note: str = "", change_type: str | None = None) -> dict[str, Any] | None:
    allowed_status = {"pending_review", "accepted", "ignored", "needs_verification"}
    if status not in allowed_status:
        raise ValueError("INVALID_COMPARISON_ITEM_STATUS")
    values: dict[str, Any] = {"status": status, "user_note": user_note.strip()}
    if change_type is not None:
        if change_type not in CHANGE_TYPES:
            raise ValueError("INVALID_CHANGE_TYPE")
        values["change_type"] = change_type
    with session_scope() as s:
        s.execute(update(ORMMaterialComparisonItem).where(
            ORMMaterialComparisonItem.c.id == int(item_id),
            ORMMaterialComparisonItem.c.comparison_id == int(comparison_id),
        ).values(**values))
    comparison = get_comparison(comparison_id)
    return next((item for item in (comparison or {}).get("items", []) if item["id"] == item_id), None)


def complete_comparison_task(task_id: str) -> dict[str, Any]:
    """Classify changes after the ordinary Evidence/Analysis stages finish."""
    with session_scope() as s:
        run = s.execute(select(ORMMaterialComparisonRun).where(
            ORMMaterialComparisonRun.c.task_id == task_id
        )).mappings().first()
        payload_text = s.execute(select(ORMShortMemory.c.payload).where(
            ORMShortMemory.c.task_id == task_id
        )).scalar()
    if run is None:
        raise ValueError("COMPARISON_RUN_NOT_FOUND")
    payload = _load(payload_text, {})
    baseline = get_report_version(int(run["base_version_id"]))
    if baseline is None:
        raise ValueError("REPORT_VERSION_NOT_FOUND")

    fact_ids = [int(value) for value in payload.get("fact_ids", []) if str(value).isdigit()]
    inference_ids = [int(value) for value in payload.get("inference_ids", []) if str(value).isdigit()]
    with session_scope() as s:
        new_facts = [dict(row) for row in s.execute(select(ORMFact).where(
            ORMFact.c.id.in_(fact_ids)
        )).mappings().all()] if fact_ids else []
        new_inferences = [dict(row) for row in s.execute(select(ORMInference).where(
            ORMInference.c.id.in_(inference_ids)
        )).mappings().all()] if inference_ids else []

    baseline_facts = list(baseline.get("fact_snapshot") or [])
    candidates = _candidate_sets(new_facts, baseline_facts)
    candidates = _merge_candidate_sets(
        candidates,
        _semantic_candidate_sets(
            new_facts, baseline_facts,
            str(baseline.get("task_id") or ""), str(run.get("task_id") or ""),
        ),
    )
    classified = _classify_batches(candidates, str(run["focus"] or baseline.get("user_requirements") or ""))
    baseline_by_id = {int(item["id"]): item for item in baseline_facts if item.get("id") is not None}
    sections = _baseline_sections(baseline)
    sentence_impacts = _lineage_impacts(baseline)

    records = []
    for fact in new_facts:
        fact_id = int(fact["id"])
        decision = classified.get(fact_id) or _fallback_decision(fact, candidates.get(fact_id, []))
        baseline_fact_id = decision.get("baseline_fact_id")
        baseline_fact = baseline_by_id.get(int(baseline_fact_id)) if baseline_fact_id is not None else None
        impacts = sentence_impacts.get(int(baseline_fact_id), []) if baseline_fact_id is not None else []
        if not impacts:
            impacts = _section_overlap_impacts(str(fact.get("content") or ""), sections)
        evidence = {
            "new_fact": _compact_fact(fact, include_evidence=True),
            "baseline_fact": baseline_fact,
        }
        item_key = hashlib.sha256(
            f"{fact_id}|{baseline_fact_id}|{decision.get('change_type')}".encode("utf-8")
        ).hexdigest()[:24]
        records.append({
            "comparison_id": int(run["id"]),
            "item_key": item_key,
            "change_type": decision.get("change_type", "uncertain"),
            "status": "pending_review",
            "confidence": decision.get("confidence", "medium"),
            "new_fact_id": fact_id,
            "baseline_fact_id": baseline_fact_id,
            "title": str(decision.get("title") or fact.get("content") or "")[:160],
            "rationale": str(decision.get("rationale") or ""),
            "evidence_json": _dump(evidence),
            "impact_json": _dump({"report_locations": impacts}),
        })

    # Inference impact is recorded in the summary instead of being presented as
    # a fabricated one-to-one fact relation.
    inference_summary = _inference_impact_summary(new_inferences, baseline.get("inference_snapshot") or [])
    counts = Counter(record["change_type"] for record in records)
    summary = {
        "new_material_count": len(_load(run["material_ids_json"], [])),
        "new_fact_count": len(new_facts),
        "new_inference_count": len(new_inferences),
        "change_counts": dict(counts),
        "affected_sections": sorted({
            impact.get("section", "")
            for record in records
            for impact in _load(record["impact_json"], {}).get("report_locations", [])
            if impact.get("section")
        }),
        "inference_impact": inference_summary,
        "baseline_version_id": int(run["base_version_id"]),
        "report_mutated": False,
    }
    with session_scope() as s:
        s.execute(delete(ORMMaterialComparisonItem).where(
            ORMMaterialComparisonItem.c.comparison_id == int(run["id"])
        ))
        if records:
            s.execute(insert(ORMMaterialComparisonItem), records)
        s.execute(update(ORMMaterialComparisonRun).where(
            ORMMaterialComparisonRun.c.id == int(run["id"])
        ).values(
            status="ready",
            summary_json=_dump(summary),
            error="",
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        ))
    return get_comparison(int(run["id"])) or {}


def accepted_update_handoff(comparison_id: int) -> dict[str, Any]:
    comparison = get_comparison(comparison_id)
    if comparison is None:
        raise ValueError("COMPARISON_NOT_FOUND")
    accepted = [item for item in comparison["items"] if item["status"] == "accepted"]
    if not accepted:
        raise ValueError("NO_ACCEPTED_COMPARISON_ITEMS")
    lines = [
        f"- [{item['change_type']}] {item['title']}（变化项 {item['id']}）"
        for item in accepted
    ]
    return {
        "report_id": comparison["report_id"],
        "base_version_id": comparison["base_version_id"],
        "material_ids": comparison["material_ids"],
        "comparison_id": comparison_id,
        "accepted_item_ids": [item["id"] for item in accepted],
        "update_reason": "仅依据以下已接受的新增材料变化更新报告：\n" + "\n".join(lines),
    }


def mark_comparison_failed(task_id: str, error: str) -> None:
    with session_scope() as s:
        s.execute(update(ORMMaterialComparisonRun).where(
            ORMMaterialComparisonRun.c.task_id == task_id
        ).values(status="failed", error=error[:1000], finished_at=time.strftime("%Y-%m-%d %H:%M:%S")))


def _candidate_sets(new_facts: list[dict], baseline_facts: list[dict], limit: int = 4) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {}
    for fact in new_facts:
        text = str(fact.get("content") or "")
        scored = []
        for old in baseline_facts:
            score = _text_overlap(text, str(old.get("content") or ""))
            if score > 0.04:
                scored.append((score, old))
        scored.sort(key=lambda item: item[0], reverse=True)
        result[int(fact["id"])] = [
            {"id": item.get("id"), "content": item.get("content"), "dimension": item.get("dimension"), "score": round(score, 4)}
            for score, item in scored[:limit]
        ]
    return result


def _semantic_candidate_sets(new_facts: list[dict], baseline_facts: list[dict],
                             baseline_task_id: str, new_task_id: str = "",
                             limit: int = 4) -> dict[int, list[dict]]:
    """Use Qdrant fact vectors when available; lexical retrieval remains fallback."""
    if not new_facts or not baseline_facts or not baseline_task_id:
        return {}
    try:
        import numpy as np
        from app.retrieval.embedder import embed_texts
        from app.retrieval.store import cosine, vector_store

        allowed = {int(item["id"]): item for item in baseline_facts if item.get("id") is not None}
        old_vectors = [(fact_id, vector) for fact_id, vector in vector_store.fact_vectors(baseline_task_id) if fact_id in allowed]
        if not old_vectors:
            return {}
        indexed_new_vectors = dict(vector_store.fact_vectors(new_task_id)) if new_task_id else {}
        missing = [item for item in new_facts if int(item["id"]) not in indexed_new_vectors]
        if missing:
            generated = embed_texts(
                [str(item.get("content") or "") for item in missing], query=True,
            )
            indexed_new_vectors.update(
                (int(item["id"]), vector) for item, vector in zip(missing, generated)
            )
        result: dict[int, list[dict]] = {}
        for fact in new_facts:
            vector = indexed_new_vectors.get(int(fact["id"]))
            if vector is None:
                continue
            scored = sorted(
                ((cosine(np.asarray(vector, dtype=np.float32), old_vector), fact_id) for fact_id, old_vector in old_vectors),
                reverse=True,
            )
            result[int(fact["id"])] = [{
                "id": fact_id, "content": allowed[fact_id].get("content"),
                "dimension": allowed[fact_id].get("dimension"), "score": round(float(score), 4),
                "retrieval": "semantic",
            } for score, fact_id in scored[:limit] if score > 0.2]
        return result
    except Exception:
        return {}


def _merge_candidate_sets(primary: dict[int, list[dict]], secondary: dict[int, list[dict]],
                          limit: int = 5) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {}
    for fact_id in set(primary) | set(secondary):
        merged: dict[int, dict] = {}
        for item in [*(primary.get(fact_id) or []), *(secondary.get(fact_id) or [])]:
            item_id = int(item["id"])
            existing = merged.get(item_id)
            if existing is None or float(item.get("score") or 0) > float(existing.get("score") or 0):
                merged[item_id] = item
        result[fact_id] = sorted(merged.values(), key=lambda item: float(item.get("score") or 0), reverse=True)[:limit]
    return result


def _classify_batches(candidates: dict[int, list[dict]], focus: str) -> dict[int, dict]:
    entries = [{"new_fact_id": fact_id, "candidates": items} for fact_id, items in candidates.items()]
    # Content is fetched once here so each classification is evidence-complete.
    fact_ids = list(candidates)
    with session_scope() as s:
        rows = s.execute(select(ORMFact.c.id, ORMFact.c.content, ORMFact.c.dimension).where(
            ORMFact.c.id.in_(fact_ids)
        )).mappings().all() if fact_ids else []
    text_by_id = {int(row["id"]): {"content": row["content"], "dimension": row["dimension"]} for row in rows}
    for entry in entries:
        entry["new_fact"] = text_by_id.get(entry["new_fact_id"], {})
    result: dict[int, dict] = {}
    for start in range(0, len(entries), 16):
        batch = entries[start:start + 16]
        try:
            payload = invoke(
                "material_comparison",
                model_gateway.generate_json,
                _CLASSIFY_PROMPT.replace("{focus}", focus[:1200]).replace("{payload}", _dump(batch)),
                system="你只负责证据变化分类，不改写报告。",
                think=False,
                max_tokens=3200,
            )
        except Exception:
            payload = {}
        for item in payload.get("items", []) if isinstance(payload, dict) else []:
            try:
                fact_id = int(item.get("new_fact_id"))
            except (TypeError, ValueError):
                continue
            change_type = str(item.get("change_type") or "uncertain")
            if fact_id not in candidates or change_type not in CHANGE_TYPES:
                continue
            allowed_baselines = {int(x["id"]) for x in candidates[fact_id] if x.get("id") is not None}
            baseline_id = item.get("baseline_fact_id")
            if baseline_id is not None and int(baseline_id) not in allowed_baselines:
                baseline_id = None
            result[fact_id] = {
                "baseline_fact_id": int(baseline_id) if baseline_id is not None else None,
                "change_type": change_type,
                "confidence": str(item.get("confidence") or "medium"),
                "title": str(item.get("title") or ""),
                "rationale": str(item.get("rationale") or ""),
            }
    return result


def _fallback_decision(fact: dict, candidates: list[dict]) -> dict:
    if not candidates:
        return {"baseline_fact_id": None, "change_type": "addition", "confidence": "medium",
                "title": str(fact.get("content") or "")[:100], "rationale": "基线中未检索到稳定对应事实。"}
    best = candidates[0]
    score = float(best.get("score") or 0)
    return {"baseline_fact_id": int(best["id"]),
            "change_type": "corroboration" if score >= 0.8 else "uncertain",
            "confidence": "high" if score >= 0.92 else "low",
            "title": str(fact.get("content") or "")[:100],
            "rationale": "模型分类不可用，采用保守文本对齐结果。"}


def _lineage_impacts(baseline: dict) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {}
    for sentence in baseline.get("sentence_snapshot") or []:
        refs = sentence.get("source_refs") or {}
        location = {
            "section": sentence.get("section"), "paragraph": sentence.get("paragraph"),
            "sentence_id": sentence.get("id"), "text": sentence.get("rendered_text") or sentence.get("content"),
            "reason": "lineage",
        }
        for fact_id in refs.get("fact_ids") or []:
            try:
                result.setdefault(int(fact_id), []).append(location)
            except (TypeError, ValueError):
                continue
    return result


def _baseline_sections(baseline: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for sentence in baseline.get("sentence_snapshot") or []:
        section = str(sentence.get("section") or "")
        result[section] = result.get(section, "") + "\n" + str(sentence.get("rendered_text") or sentence.get("content") or "")
    return result


def _section_overlap_impacts(text: str, sections: dict[str, str]) -> list[dict]:
    scored = sorted(
        ((section, _text_overlap(text, section + "\n" + content)) for section, content in sections.items()),
        key=lambda item: item[1], reverse=True,
    )
    return [{"section": section, "score": round(score, 4), "reason": "semantic_overlap"}
            for section, score in scored[:3] if score > 0.04]


def _inference_impact_summary(new_items: list[dict], baseline_items: list[dict]) -> dict:
    affected = 0
    for new in new_items:
        if any(_text_overlap(str(new.get("content") or ""), str(old.get("content") or "")) > 0.12 for old in baseline_items):
            affected += 1
    return {"generated": len(new_items), "related_to_baseline": affected, "requires_review": bool(new_items)}


def _compact_fact(fact: dict, *, include_evidence: bool = False) -> dict:
    result = {key: fact.get(key) for key in ("id", "content", "dimension", "source_level", "stable_key")}
    if include_evidence:
        result["evidence"] = list(fact.get("evidence") or [])
    return result


def _attach_new_fact_evidence(items: list[dict[str, Any]]) -> None:
    """Backfill provenance for comparisons created before evidence was embedded."""
    fact_ids = sorted({
        int(item["new_fact_id"]) for item in items if item.get("new_fact_id") is not None
    })
    if not fact_ids:
        return
    with session_scope() as s:
        rows = s.execute(
            select(ORMEvidence).where(ORMEvidence.c.fact_id.in_(fact_ids)).order_by(ORMEvidence.c.id)
        ).mappings().all()
    evidence_by_fact: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        evidence_by_fact.setdefault(int(row["fact_id"]), []).append({
            "evidence_id": int(row["id"]),
            "material_id": int(row["material_id"]),
            "unit_id": int(row["unit_id"]),
            "source_file": row["source_file"],
            "page": row["page"],
            "paragraph": row["paragraph"],
            "quote": row["quote"],
        })
    for item in items:
        fact = (item.get("evidence") or {}).get("new_fact")
        if not isinstance(fact, dict):
            continue
        fact["evidence"] = evidence_by_fact.get(int(item["new_fact_id"]), [])


def _comparison_document(baseline: dict, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a stable sentence-oriented view without changing the baseline report."""
    changes_by_sentence: dict[int, list[dict[str, Any]]] = {}
    mapped_item_ids: set[int] = set()
    for item in items:
        for location in (item.get("impact") or {}).get("report_locations", []):
            sentence_id = location.get("sentence_id")
            if sentence_id is None:
                continue
            try:
                sentence_key = int(sentence_id)
            except (TypeError, ValueError):
                continue
            mapped_item_ids.add(int(item["id"]))
            changes_by_sentence.setdefault(sentence_key, []).append({
                "item_id": int(item["id"]),
                "change_type": item["change_type"],
                "status": item["status"],
                "confidence": item["confidence"],
            })

    sentences = []
    for row in baseline.get("sentence_snapshot") or []:
        sentence_id = row.get("id")
        try:
            sentence_key = int(sentence_id)
        except (TypeError, ValueError):
            sentence_key = -1
        sentences.append({
            "id": sentence_id,
            "section": row.get("section") or "正文",
            "paragraph": row.get("paragraph") or 0,
            "position": row.get("position") or 0,
            "text": row.get("rendered_text") or row.get("content") or "",
            "source_refs": row.get("source_refs") or {},
            "changes": changes_by_sentence.get(sentence_key, []),
        })
    return {
        "sentences": sentences,
        "unmapped_item_ids": [int(item["id"]) for item in items if int(item["id"]) not in mapped_item_ids],
    }


def _text_overlap(left: str, right: str) -> float:
    a = re.sub(r"\s+", "", left or "")
    b = re.sub(r"\s+", "", right or "")
    if len(a) < 2 or len(b) < 2:
        return 0.0
    grams_a = {a[index:index + 2] for index in range(len(a) - 1)}
    grams_b = {b[index:index + 2] for index in range(len(b) - 1)}
    return len(grams_a & grams_b) / max(len(grams_a), 1)


def _run_detail(row, include_items: bool = False) -> dict[str, Any]:
    return {
        "id": int(row["id"]), "comparison_key": row["comparison_key"], "task_id": row["task_id"],
        "report_id": int(row["report_id"]), "base_version_id": int(row["base_version_id"]),
        "status": row["status"], "focus": row["focus"],
        "material_ids": _load(row["material_ids_json"], []), "summary": _load(row["summary_json"], {}),
        "error": row["error"], "created_at": row["created_at"], "finished_at": row["finished_at"],
    }


def _item_detail(row) -> dict[str, Any]:
    return {
        "id": int(row["id"]), "comparison_id": int(row["comparison_id"]), "item_key": row["item_key"],
        "change_type": row["change_type"], "status": row["status"], "confidence": row["confidence"],
        "new_fact_id": row["new_fact_id"], "baseline_fact_id": row["baseline_fact_id"],
        "baseline_inference_id": row["baseline_inference_id"], "title": row["title"],
        "rationale": row["rationale"], "evidence": _load(row["evidence_json"], {}),
        "impact": _load(row["impact_json"], {}), "user_note": row["user_note"],
    }


def _load(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
