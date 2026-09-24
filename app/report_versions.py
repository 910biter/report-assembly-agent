"""Report version snapshots and incremental-update deltas.

ReportVersion is an immutable audit asset. It captures what the report looked
like at a point in time and which evidence objects supported it. Incremental
updates should compare against a version snapshot instead of mutating history.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, insert, select, update

from app.db import session_scope
from app.infrastructure.orm import (
    ORMConflict,
    ORMEvidence,
    ORMFact,
    ORMInference,
    ORMMaterial,
    ORMPlan,
    ORMReport,
    ORMReportChangeDecision,
    ORMReportVersion,
    ORMReportVersionDelta,
    ORMSentence,
    ORMSentenceFact,
    ORMSentenceInference,
    ORMShortMemory,
    ORMTaskArtifact,
)


@dataclass(frozen=True)
class VersionSnapshot:
    version_id: int
    version_no: int
    version_label: str


def ensure_report_version(report_id: int, task_id: str = "", status: str = "snapshot",
                          change_summary: str = "", kind: str = "major", _session=None) -> VersionSnapshot:
    """Create a new immutable version unless the latest snapshot is identical.

    ``version_no`` is a monotonic database sequence. Human-facing major/minor
    components are stored separately, so v2.10 never collapses into v2.1.
    """
    manager = session_scope() if _session is None else nullcontext(_session)
    with manager as s:
        report = s.execute(
            select(ORMReport.c.id, ORMReport.c.task_id)
            .where(ORMReport.c.id == report_id)
            .with_for_update()
        ).mappings().first()
        if report is None:
            raise ValueError("REPORT_NOT_FOUND")
        owner = str(report.get("task_id") or "")
        if owner and task_id and owner != task_id:
            raise ValueError("REPORT_TASK_MISMATCH")
        if task_id and not owner:
            s.execute(
                update(ORMReport)
                .where(ORMReport.c.id == report_id)
                .values(task_id=task_id)
            )
            owner = task_id
        snapshot = build_report_snapshot(report_id, task_id=owner or task_id, _session=s)
        latest = s.execute(
            select(ORMReportVersion)
            .where(ORMReportVersion.c.report_id == report_id)
            .order_by(ORMReportVersion.c.version_no.desc())
        ).mappings().first()
        latest_hash = _stored_version_hash(latest) if latest is not None else ""
        if latest is not None and latest_hash == snapshot["metadata"]["snapshot_hash"]:
            major, minor = _version_components(latest)
            return VersionSnapshot(int(latest["id"]), int(latest["version_no"]), _version_label(major, minor))
        if latest is None:
            version_no, major, minor = 1, 1, 0
        elif kind == "major":
            old_major, _old_minor = _version_components(latest)
            version_no, major, minor = int(latest["version_no"]) + 1, old_major + 1, 0
        else:
            old_major, old_minor = _version_components(latest)
            version_no, major, minor = int(latest["version_no"]) + 1, old_major, old_minor + 1
        based_on = int(latest["id"]) if latest is not None else None
        cur = s.execute(
            insert(ORMReportVersion).values(
                report_id=report_id,
                version_no=version_no,
                version_major=major,
                version_minor=minor,
                based_on_version_id=based_on,
                task_id=snapshot["task_id"],
                run_id=str(snapshot["metadata"].get("run_id") or ""),
                status=status,
                title=snapshot["title"],
                user_requirements=snapshot["user_requirements"],
                template_id=snapshot["template_id"],
                material_fingerprints=_dump(snapshot["material_fingerprints"]),
                report_plan_snapshot=_dump(snapshot["report_plan_snapshot"]),
                narrative_plan_snapshot=_dump(snapshot["narrative_plan_snapshot"]),
                scale_plan_snapshot=_dump(snapshot["scale_plan_snapshot"]),
                fact_snapshot=_dump(snapshot["fact_snapshot"]),
                inference_snapshot=_dump(snapshot["inference_snapshot"]),
                conflict_snapshot=_dump(snapshot["conflict_snapshot"]),
                sentence_snapshot=_dump(snapshot["sentence_snapshot"]),
                metadata_json=_dump(snapshot["metadata"]),
                change_summary=change_summary,
            )
        )
        version_id = int(cur.inserted_primary_key[0])
        if status == "final":
            s.execute(
                update(ORMReport).where(ORMReport.c.id == report_id).values(status="final")
            )
    return VersionSnapshot(version_id, version_no, _version_label(major, minor))


def latest_report_version(report_id: int) -> dict[str, Any] | None:
    """Return the latest report version detail, or None when no snapshot exists."""
    with session_scope() as s:
        row = s.execute(
            select(ORMReportVersion)
            .where(ORMReportVersion.c.report_id == report_id)
            .order_by(ORMReportVersion.c.version_no.desc())
        ).mappings().first()
    return _version_detail(row) if row is not None else None


def build_report_snapshot(report_id: int, task_id: str = "", *, _session=None) -> dict[str, Any]:
    """Read the current mutable report state and convert it into a snapshot."""
    manager = session_scope() if _session is None else nullcontext(_session)
    with manager as s:
        report = s.execute(select(ORMReport).where(ORMReport.c.id == report_id)).mappings().first()
        if report is None:
            raise ValueError("REPORT_NOT_FOUND")
        plan = s.execute(select(ORMPlan).where(ORMPlan.c.id == report["plan_id"])).mappings().first()
        owner = str(report.get("task_id") or "")
        if owner and task_id and owner != task_id:
            raise ValueError("REPORT_TASK_MISMATCH")
        task_id = owner or task_id or ""
        task_payload = _task_payload_in_session(s, task_id)
        material_ids = [int(mid) for mid in (task_payload.get("material_ids") or []) if str(mid).isdigit()]
        fact_ids = [int(fid) for fid in (task_payload.get("fact_ids") or []) if str(fid).isdigit()]
        inference_ids = [
            int(iid)
            for iid in (task_payload.get("inference_ids") or []) + (task_payload.get("external_ids") or [])
            if str(iid).isdigit()
        ]
        conflict_ids = [int(cid) for cid in (task_payload.get("conflict_ids") or []) if str(cid).isdigit()]
        materials = _materials_in_session(s, material_ids)
        facts = _facts_in_session(s, fact_ids)
        inferences = _inferences_in_session(s, inference_ids)
        conflicts = _conflicts_in_session(s, conflict_ids)
        sentences = _sentences_in_session(s, report_id)
        narrative = _narrative_artifacts_in_session(s, task_id, str(task_payload.get("run_id") or ""))
    plan_snapshot = _plan_snapshot(plan)
    metadata = {
        "report_id": report_id,
        "plan_id": report["plan_id"],
        "task_id": task_id,
        "run_revision": int(task_payload.get("run_revision") or 1),
        "run_id": str(task_payload.get("run_id") or ""),
        "run_mode": str(task_payload.get("run_mode") or "initial"),
        "sentence_count": len(sentences),
        "material_count": len(materials),
        "fact_count": len(facts),
        "inference_count": len(inferences),
        "conflict_count": len(conflicts),
    }
    metadata["snapshot_hash"] = _content_snapshot_hash({
        "title": report["title"],
        "material_fingerprints": materials,
        "report_plan_snapshot": plan_snapshot,
        "narrative_plan_snapshot": narrative,
        "scale_plan_snapshot": _json_load(plan["budget"] if plan is not None else "{}", {}),
        "fact_snapshot": facts,
        "inference_snapshot": inferences,
        "conflict_snapshot": conflicts,
        "sentence_snapshot": sentences,
    })
    metadata["snapshot_hash_version"] = _SNAPSHOT_HASH_VERSION
    return {
        "task_id": task_id,
        "title": report["title"],
        "user_requirements": plan["user_requirements"] if plan is not None else "",
        "template_id": report["style_profile_id"],
        "material_fingerprints": materials,
        "report_plan_snapshot": plan_snapshot,
        "narrative_plan_snapshot": narrative,
        "scale_plan_snapshot": _json_load(plan["budget"] if plan is not None else "{}", {}),
        "fact_snapshot": facts,
        "inference_snapshot": inferences,
        "conflict_snapshot": conflicts,
        "sentence_snapshot": sentences,
        "metadata": metadata,
    }


def list_report_versions(report_id: int) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(
            select(ORMReportVersion)
            .where(ORMReportVersion.c.report_id == report_id)
            .order_by(ORMReportVersion.c.version_no.desc())
        ).mappings().all()
    return [_version_summary(row) for row in rows]


def get_report_version(version_id: int, *, _session=None) -> dict[str, Any] | None:
    manager = session_scope() if _session is None else nullcontext(_session)
    with manager as s:
        row = s.execute(
            select(ORMReportVersion).where(ORMReportVersion.c.id == version_id)
        ).mappings().first()
    if row is None:
        return None
    return _version_detail(row)


def create_incremental_delta(report_id: int, added_material_ids: list[int],
                             update_reason: str = "", run_id: str = "",
                             _session=None) -> dict[str, Any]:
    """Create a conservative delta preview for future incremental update runs.

    This does not rewrite the report. It records deterministic material changes
    and compares the latest snapshot with current task-level artifacts, giving
    the planner a safe handoff object for the next implementation step.
    """
    added_material_ids = [int(mid) for mid in added_material_ids if str(mid).isdigit()]
    manager = session_scope() if _session is None else nullcontext(_session)
    with manager as s:
        latest = s.execute(
            select(ORMReportVersion)
            .where(ORMReportVersion.c.report_id == report_id)
            .order_by(ORMReportVersion.c.version_no.desc())
        ).mappings().first()
        if latest is None:
            raise ValueError("REPORT_VERSION_REQUIRED")
        current = build_report_snapshot(report_id, task_id=latest["task_id"], _session=s)
        old_materials = _json_load(latest["material_fingerprints"], [])
        old_facts = _json_load(latest["fact_snapshot"], [])
        old_inferences = _json_load(latest["inference_snapshot"], [])
        old_conflicts = _json_load(latest["conflict_snapshot"], [])
        new_materials = _materials_in_session(s, added_material_ids)
        old_hashes = {m.get("file_hash") or m.get("fingerprint") for m in old_materials}
        added_materials = [m for m in new_materials if (m.get("file_hash") or m.get("fingerprint")) not in old_hashes]
        duplicate_materials = [m for m in new_materials if (m.get("file_hash") or m.get("fingerprint")) in old_hashes]
        fact_delta = _object_delta(old_facts, current["fact_snapshot"])
        inference_delta = _object_delta(old_inferences, current["inference_snapshot"])
        conflict_delta = _object_delta(old_conflicts, current["conflict_snapshot"])
        affected = _affected_chapters(current["sentence_snapshot"], fact_delta["added"], inference_delta["added"])
        cur = s.execute(
            insert(ORMReportVersionDelta).values(
                report_id=report_id,
                run_id=run_id,
                from_version_id=latest["id"],
                status="preview",
                update_reason=update_reason,
                added_materials=_dump(added_materials),
                duplicate_materials=_dump(duplicate_materials),
                added_facts=_dump(fact_delta["added"]),
                modified_facts=_dump(fact_delta["modified"]),
                deprecated_facts=_dump(fact_delta["deprecated"]),
                added_inferences=_dump(inference_delta["added"]),
                modified_inferences=_dump(inference_delta["modified"]),
                deprecated_inferences=_dump(inference_delta["deprecated"]),
                new_conflicts=_dump(conflict_delta["added"]),
                resolved_conflicts=_dump(conflict_delta["deprecated"]),
                affected_chapters=_dump(affected),
                structure_changes=_dump([]),
                evidence_changes=_dump({"mode": "preview", "requires_impact_analysis": bool(affected or added_materials)}),
                metadata_json=_dump({
                    "latest_version_no": latest["version_no"],
                    "added_material_count": len(added_materials),
                    "duplicate_material_count": len(duplicate_materials),
                }),
            )
        )
        delta_id = int(cur.inserted_primary_key[0])
    if _session is None:
        return get_report_delta(delta_id) or {"id": delta_id}
    row = s.execute(
        select(ORMReportVersionDelta).where(ORMReportVersionDelta.c.id == delta_id)
    ).mappings().first()
    return _delta_detail(row) if row is not None else {"id": delta_id}


def attach_delta_version(delta_id: int, version_id: int, status: str = "applied") -> None:
    """Link an incremental delta to the version produced from it."""
    with session_scope() as s:
        s.execute(
            update(ORMReportVersionDelta)
            .where(ORMReportVersionDelta.c.id == int(delta_id))
            .values(to_version_id=int(version_id), status=status)
        )


def get_report_delta(delta_id: int) -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.execute(
            select(ORMReportVersionDelta).where(ORMReportVersionDelta.c.id == delta_id)
        ).mappings().first()
    if row is None:
        return None
    return _delta_detail(row)


def refresh_incremental_delta(report_id: int, delta_id: int, task_id: str = "") -> dict[str, Any] | None:
    """Refresh a delta after an incremental task has produced new artifacts."""
    with session_scope() as s:
        delta = s.execute(
            select(ORMReportVersionDelta).where(ORMReportVersionDelta.c.id == int(delta_id))
        ).mappings().first()
        if delta is None:
            return None
        base = s.execute(
            select(ORMReportVersion).where(ORMReportVersion.c.id == delta["from_version_id"])
        ).mappings().first()
        if base is None:
            return None
    impact = build_incremental_impact(report_id, delta_id, task_id=task_id)
    fact_delta = impact["fact_delta"]
    inference_delta = impact["inference_delta"]
    conflict_delta = impact["conflict_delta"]
    affected = impact["affected_chapters"]
    structure_changes = impact["structure_changes"]
    cross_version_conflicts = impact["cross_version_conflicts"]
    with session_scope() as s:
        s.execute(
            update(ORMReportVersionDelta)
            .where(ORMReportVersionDelta.c.id == int(delta_id))
            .values(
                status="draft_ready",
                added_facts=_dump(fact_delta["added"]),
                modified_facts=_dump(fact_delta["modified"]),
                deprecated_facts=_dump(fact_delta["deprecated"]),
                added_inferences=_dump(inference_delta["added"]),
                modified_inferences=_dump(inference_delta["modified"]),
                deprecated_inferences=_dump(inference_delta["deprecated"]),
                new_conflicts=_dump(conflict_delta["added"]),
                resolved_conflicts=_dump(conflict_delta["deprecated"]),
                affected_chapters=_dump(affected),
                structure_changes=_dump(structure_changes),
                evidence_changes=_dump({
                    "mode": "incremental_execution",
                    "added_fact_count": len(fact_delta["added"]),
                    "added_inference_count": len(inference_delta["added"]),
                    "affected_chapter_count": len(affected),
                    "structure_change_count": len(structure_changes),
                    "cross_version_conflict_count": len(cross_version_conflicts),
                    "cross_version_conflicts": cross_version_conflicts,
                }),
            )
        )
    return get_report_delta(delta_id)


def reconcile_incremental_lifecycle(inherited_fact_ids: list[int], new_fact_ids: list[int],
                                    inherited_inference_ids: list[int]) -> dict[str, Any]:
    """Mark same-source fact revisions and dependent inferences explicitly.

    This deliberately avoids semantic guessing. A fact is superseded only when
    the extractor produced the same deterministic source-location key with
    different content. Other new facts remain additions and conflicts remain a
    separate review concern.
    """
    inherited_fact_ids = [int(value) for value in inherited_fact_ids]
    new_fact_ids = [int(value) for value in new_fact_ids]
    if not inherited_fact_ids or not new_fact_ids:
        return {"superseded_fact_ids": [], "replacement_fact_ids": [], "review_inference_ids": []}
    with session_scope() as s:
        old_rows = s.execute(select(
            ORMFact.c.id, ORMFact.c.content, ORMFact.c.stable_key,
        ).where(ORMFact.c.id.in_(inherited_fact_ids))).mappings().all()
        new_rows = s.execute(select(
            ORMFact.c.id, ORMFact.c.content, ORMFact.c.stable_key,
        ).where(ORMFact.c.id.in_(new_fact_ids))).mappings().all()
        old_by_key = {row["stable_key"]: row for row in old_rows if row["stable_key"]}
        replacements: list[tuple[int, int]] = []
        for new_row in new_rows:
            old_row = old_by_key.get(new_row["stable_key"])
            if old_row is not None and old_row["content"] != new_row["content"]:
                replacements.append((int(old_row["id"]), int(new_row["id"])))
        superseded_ids = [old_id for old_id, _new_id in replacements]
        for old_id, new_id in replacements:
            s.execute(update(ORMFact).where(ORMFact.c.id == old_id).values(
                lifecycle_status="superseded", superseded_by_fact_id=new_id,
            ))
        review_inference_ids: list[int] = []
        if superseded_ids and inherited_inference_ids:
            inference_rows = s.execute(select(
                ORMInference.c.id, ORMInference.c.based_fact_ids,
            ).where(ORMInference.c.id.in_([int(value) for value in inherited_inference_ids]))).mappings().all()
            superseded_set = set(superseded_ids)
            for inference in inference_rows:
                based_ids = {int(value) for value in _json_load(inference["based_fact_ids"], [])}
                if based_ids & superseded_set:
                    review_inference_ids.append(int(inference["id"]))
            if review_inference_ids:
                s.execute(update(ORMInference).where(
                    ORMInference.c.id.in_(review_inference_ids)
                ).values(lifecycle_status="review_required"))
    return {
        "superseded_fact_ids": [old_id for old_id, _new_id in replacements],
        "replacement_fact_ids": [new_id for _old_id, new_id in replacements],
        "review_inference_ids": review_inference_ids,
    }


def _materials_in_session(s, material_ids: list[int]) -> list[dict[str, Any]]:
    if not material_ids:
        return []
    rows = s.execute(
        select(ORMMaterial).where(ORMMaterial.c.id.in_(material_ids)).order_by(ORMMaterial.c.id)
    ).mappings().all()
    return [{
        "material_id": row["id"],
        "filename": row["filename"],
        "file_type": row["file_type"],
        "fingerprint": row["fingerprint"],
        "file_hash": row["file_hash"],
        "parser_version": row["parser_version"],
        "parsed_at": row["parsed_at"],
        "is_duplicate": row["is_duplicate"],
        "duplicate_of": row["duplicate_of"],
    } for row in rows]


def _facts_in_session(s, fact_ids: list[int]) -> list[dict[str, Any]]:
    if not fact_ids:
        return []
    evidence_rows = s.execute(
        select(ORMEvidence).where(ORMEvidence.c.fact_id.in_(fact_ids)).order_by(ORMEvidence.c.id)
    ).mappings().all()
    evidence_by_fact: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for ev in evidence_rows:
        evidence_by_fact[int(ev["fact_id"])].append({
            "evidence_id": ev["id"],
            "material_id": ev["material_id"],
            "unit_id": ev["unit_id"],
            "source_file": ev["source_file"],
            "page": ev["page"],
            "paragraph": ev["paragraph"],
            "quote": ev["quote"],
        })
    rows = s.execute(
        select(ORMFact).where(ORMFact.c.id.in_(fact_ids)).order_by(ORMFact.c.id)
    ).mappings().all()
    return [{
        "id": row["id"],
        "content": row["content"],
        "dimension": row["dimension"],
        "need_id": row["need_id"],
        "source_level": row["source_level"],
        "evidence_ids": _json_load(row["evidence_ids"], []),
        "conflict_ids": _json_load(row["conflict_ids"], []),
        "task_id": row["task_id"],
        "origin_call_id": row["origin_call_id"],
        "disposition": row["disposition"],
        "stable_key": row.get("stable_key", ""),
        "lifecycle_status": row.get("lifecycle_status", "active"),
        "introduced_run_id": row.get("introduced_run_id", ""),
        "superseded_by_fact_id": row.get("superseded_by_fact_id"),
        "evidence": evidence_by_fact.get(int(row["id"]), []),
    } for row in rows]


def _inferences_in_session(s, inference_ids: list[int]) -> list[dict[str, Any]]:
    if not inference_ids:
        return []
    rows = s.execute(
        select(ORMInference).where(ORMInference.c.id.in_(inference_ids)).order_by(ORMInference.c.id)
    ).mappings().all()
    return [{
        "id": row["id"],
        "content": row["content"],
        "source_level": row["source_level"],
        "based_fact_ids": _json_load(row["based_fact_ids"], []),
        "reasoning_chain": row["reasoning_chain"],
        "dimension": row["dimension"],
        "analysis_type": row["analysis_type"] if "analysis_type" in row.keys() else "",
        "confidence_level": row["confidence_level"] if "confidence_level" in row.keys() else "medium",
        "confidence_reason": row["confidence_reason"] if "confidence_reason" in row.keys() else "",
        "uncertainty": row["uncertainty"] if "uncertainty" in row.keys() else "",
        "origin_call_id": row["origin_call_id"],
        "stable_key": row.get("stable_key", ""),
        "lifecycle_status": row.get("lifecycle_status", "active"),
        "introduced_run_id": row.get("introduced_run_id", ""),
        "superseded_by_inference_id": row.get("superseded_by_inference_id"),
    } for row in rows]


def _conflicts_in_session(s, conflict_ids: list[int]) -> list[dict[str, Any]]:
    if not conflict_ids:
        return []
    rows = s.execute(
        select(ORMConflict).where(ORMConflict.c.id.in_(conflict_ids)).order_by(ORMConflict.c.id)
    ).mappings().all()
    return [{
        "id": row["id"],
        "fact_key": row["fact_key"],
        "entries": _json_load(row["entries"], []),
        "status": row["status"],
        "task_id": row["task_id"],
        "origin_call_id": row["origin_call_id"],
    } for row in rows]


def _sentences_in_session(s, report_id: int) -> list[dict[str, Any]]:
    rows = s.execute(
        select(ORMSentence).where(ORMSentence.c.report_id == report_id)
        .order_by(ORMSentence.c.position, ORMSentence.c.id)
    ).mappings().all()
    return [{
        "id": row["id"],
        "lineage_id": row.get("lineage_id", ""),
        "parent_sentence_id": row.get("parent_sentence_id"),
        "section": row["section"],
        "paragraph": row["paragraph"],
        "position": row["position"],
        "content": row["content"],
        "user_edit": row["user_edit"],
        "rendered_text": row["user_edit"] or row["content"],
        "source_level": row["source_level"],
        "source_refs": _json_load(row["source_refs"], {}),
        "selected": row["selected"],
        "origin_call_id": row["origin_call_id"],
    } for row in rows]


def _narrative_artifacts_in_session(s, task_id: str, run_id: str = "") -> dict[str, Any]:
    if not task_id:
        return {}
    query = select(ORMTaskArtifact.c.stage, ORMTaskArtifact.c.payload).where(
        ORMTaskArtifact.c.task_id == task_id, ORMTaskArtifact.c.stage.like("narrative_plan:%")
    )
    if run_id:
        query = query.where(ORMTaskArtifact.c.run_id == run_id)
    rows = s.execute(query.order_by(ORMTaskArtifact.c.id)).mappings().all()
    return {row["stage"]: _json_load(row["payload"], {}) for row in rows}


def _plan_snapshot(plan) -> dict[str, Any]:
    if plan is None:
        return {}
    return {
        "id": plan["id"],
        "title": plan["title"],
        "structure": _json_load(plan["structure"], []),
        "dimensions": _json_load(plan["dimensions"], []),
        "objective": plan["objective"],
        "audience": plan["audience"],
        "report_type": plan["report_type"],
        "core_question": plan["core_question"],
        "core_judgment": plan["core_judgment"],
        "narrative_logic": plan["narrative_logic"],
        "chapter_plans": _json_load(plan["chapter_plans"], []),
        "budget": _json_load(plan["budget"], {}),
        "required_facts": _json_load(plan["required_facts"], []),
        "evidence_needs": _json_load(plan["evidence_needs"], []),
        "plan_stage": plan["plan_stage"],
        "plan_version": plan["plan_version"],
        "analysis_plan_json": _json_load(plan["analysis_plan_json"], {}),
        "final_plan_json": _json_load(plan["final_plan_json"], {}),
        "finalized_at": plan["finalized_at"],
    }


def _restore_plan_values(
    plan_snapshot: dict[str, Any],
    scale_snapshot: dict[str, Any] | None = None,
    fallback_title: str = "",
    fallback_requirements: str = "",
) -> dict[str, Any]:
    """Build one consistent set of mutable plan fields for version restore."""
    snapshot = dict(plan_snapshot or {})
    final_plan = snapshot.get("final_plan_json")
    return {
        "title": snapshot.get("title") or fallback_title,
        "structure": _dump(snapshot.get("structure") or []),
        "dimensions": _dump(snapshot.get("dimensions") or []),
        "objective": snapshot.get("objective") or "",
        "audience": snapshot.get("audience") or "",
        "report_type": snapshot.get("report_type") or "",
        "core_question": snapshot.get("core_question") or "",
        "core_judgment": snapshot.get("core_judgment") or "",
        "narrative_logic": snapshot.get("narrative_logic") or "",
        "chapter_plans": _dump(snapshot.get("chapter_plans") or []),
        "budget": _dump(snapshot.get("budget") or scale_snapshot or {}),
        "required_facts": _dump(snapshot.get("required_facts") or []),
        "evidence_needs": _dump(snapshot.get("evidence_needs") or []),
        "user_requirements": snapshot.get("user_requirements") or fallback_requirements,
        "plan_stage": snapshot.get("plan_stage") or "final",
        "plan_version": int(snapshot.get("plan_version") or 1),
        "analysis_plan_json": _dump(snapshot.get("analysis_plan_json") or {}),
        "final_plan_json": _dump(final_plan or {}),
        "finalized_at": snapshot.get("finalized_at"),
    }


def _object_delta(old_items: list[dict[str, Any]], new_items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    def identity(item: dict[str, Any]) -> str:
        item_id = item.get("id")
        if item_id is None:
            raise ValueError("VERSION_ARTIFACT_ID_REQUIRED")
        return f"id:{item_id}"

    old_by_key = {identity(item): item for item in old_items}
    new_by_key = {identity(item): item for item in new_items}
    added = [item for k, item in new_by_key.items() if k not in old_by_key]
    deprecated = [item for k, item in old_by_key.items() if k not in new_by_key]
    modified = []
    for k, item in new_by_key.items():
        if k in old_by_key and _stable_json_hash(item) != _stable_json_hash(old_by_key[k]):
            modified.append({"previous": old_by_key[k], "current": item})
    return {"added": added, "modified": modified, "deprecated": deprecated}


def _affected_chapters(sentences: list[dict[str, Any]], added_facts: list[dict[str, Any]],
                       added_inferences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fact_ids = {int(item["id"]) for item in added_facts if item.get("id") is not None}
    inference_ids = {int(item["id"]) for item in added_inferences if item.get("id") is not None}
    chapters: dict[str, dict[str, Any]] = {}
    for sentence in sentences:
        refs = sentence.get("source_refs") or {}
        matched_facts = fact_ids & {int(i) for i in refs.get("fact_ids") or [] if str(i).isdigit()}
        matched_infs = inference_ids & {int(i) for i in refs.get("inference_ids") or [] if str(i).isdigit()}
        if not matched_facts and not matched_infs:
            continue
        section = sentence["section"]
        item = chapters.setdefault(section, {"section": section, "fact_ids": set(), "inference_ids": set(), "severity": "minor"})
        item["fact_ids"].update(matched_facts)
        item["inference_ids"].update(matched_infs)
    result = []
    for item in chapters.values():
        result.append({
            "section": item["section"],
            "fact_ids": sorted(item["fact_ids"]),
            "inference_ids": sorted(item["inference_ids"]),
            "severity": item["severity"],
        })
    return result




def restore_report_version(version_id: int) -> dict[str, Any] | None:
    """Restore a version snapshot into the current mutable report.

    This is a whole-report rollback. Fine-grained acceptance remains in the
    editor layer through sentence selection/editing.
    """
    version = get_report_version(version_id)
    if version is None:
        return None
    report_id = int(version["report_id"])
    # Preserve the current working tree before any destructive rollback. This
    # makes whole-version restore itself reversible.
    ensure_report_version(
        report_id, task_id=str(version.get("task_id") or ""), status="snapshot",
        change_summary=f"恢复 v{version.get('version_label') or version_id} 前自动快照", kind="minor",
    )
    sentences = version.get("sentence_snapshot") or []
    with session_scope() as s:
        current_rows = s.execute(
            select(ORMSentence.c.id).where(ORMSentence.c.report_id == report_id)
        ).mappings().all()
        sentence_ids = [int(r["id"]) for r in current_rows]
        if sentence_ids:
            s.execute(ORMSentenceFact.delete().where(ORMSentenceFact.c.sentence_id.in_(sentence_ids)))
            s.execute(ORMSentenceInference.delete().where(ORMSentenceInference.c.sentence_id.in_(sentence_ids)))
            s.execute(ORMSentence.delete().where(ORMSentence.c.id.in_(sentence_ids)))
        for row in sentences:
            refs = row.get("source_refs") or {}
            cur = s.execute(
                ORMSentence.insert().values(
                    report_id=report_id,
                    lineage_id=row.get("lineage_id") or f"restored-{version_id}-{row.get('id')}",
                    parent_sentence_id=row.get("parent_sentence_id"),
                    section=row.get("section", ""),
                    paragraph=int(row.get("paragraph") or 1),
                    position=int(row.get("position") or 0),
                    content=row.get("content", ""),
                    user_edit=row.get("user_edit", ""),
                    source_level=row.get("source_level", ""),
                    source_refs=_dump(refs),
                    selected=int(row.get("selected") if row.get("selected") is not None else 1),
                    origin_call_id=row.get("origin_call_id", ""),
                )
            )
            sid = int(cur.inserted_primary_key[0])
            for fid in refs.get("fact_ids") or []:
                if str(fid).isdigit():
                    s.execute(ORMSentenceFact.insert().values(sentence_id=sid, fact_id=int(fid)))
            for iid in refs.get("inference_ids") or []:
                if str(iid).isdigit():
                    s.execute(ORMSentenceInference.insert().values(sentence_id=sid, inference_id=int(iid)))
        report_row = s.execute(
            select(ORMReport.c.plan_id).where(ORMReport.c.id == report_id)
        ).mappings().first()
        s.execute(update(ORMReport).where(ORMReport.c.id == report_id).values(
            title=version.get("title", ""), status="draft",
            style_profile_id=version.get("template_id"),
        ))
        plan_snapshot = version.get("report_plan_snapshot") or {}
        if report_row is not None and plan_snapshot:
            plan_values = _restore_plan_values(
                plan_snapshot,
                version.get("scale_plan_snapshot") or {},
                version.get("title", ""),
                version.get("user_requirements", ""),
            )
            s.execute(update(ORMPlan).where(ORMPlan.c.id == report_row["plan_id"]).values(
                **plan_values,
            ))
    return {"report_id": report_id, "restored_version_id": version_id, "sentence_count": len(sentences)}

def _section_text(paragraphs: dict[int, list[dict[str, Any]]]) -> str:
    return "".join(str(item.get("text") or "") for items in paragraphs.values() for item in items)

def _normalize_section_title(title: str) -> str:
    value = re.sub(r"^\s*(?:第\s*)?[\d０-９]+(?:\.[\d０-９]+)*\s*[章节篇]?\s*", "", str(title or ""))
    value = re.sub(r"^\s*[一二三四五六七八九十百千万]+\s*[、.．:：]\s*", "", value)
    return re.sub(r"[\s\-—_·,，。；;:：()（）\[\]【】]", "", value).casefold()

def _pair_sections(old_sections: dict[str, dict[int, list[dict[str, Any]]]],
                   new_sections: dict[str, dict[int, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """Pair logical sections without using display titles as identity."""
    old_titles, new_titles = list(old_sections), list(new_sections)
    old_pos = {title: index for index, title in enumerate(old_titles)}
    new_pos = {title: index for index, title in enumerate(new_titles)}
    unused_old = set(old_titles)
    matches: dict[str, str] = {}
    match_methods: dict[str, str] = {}
    for new_title in new_titles:
        candidates = [title for title in unused_old
                      if _normalize_section_title(title) == _normalize_section_title(new_title)]
        if candidates:
            old_title = min(candidates, key=lambda title: abs(old_pos[title] - new_pos[new_title]))
            matches[new_title] = old_title
            match_methods[new_title] = "title"
            unused_old.remove(old_title)
    for new_title in new_titles:
        if new_title in matches:
            continue
        candidates = []
        for old_title in unused_old:
            score = _sentence_similarity(_section_text(old_sections[old_title]), _section_text(new_sections[new_title]))
            if score >= 0.18:
                candidates.append((score, abs(old_pos[old_title] - new_pos[new_title]), old_title))
        if candidates:
            _score, _distance, old_title = max(candidates, key=lambda item: (item[0], -item[1]))
            matches[new_title] = old_title
            match_methods[new_title] = "content"
            unused_old.remove(old_title)
    remaining_new = [title for title in new_titles if title not in matches]
    remaining_old = [title for title in old_titles if title in unused_old]
    if len(remaining_new) == len(remaining_old):
        for new_title, old_title in zip(remaining_new, remaining_old):
            matches[new_title] = old_title
            match_methods[new_title] = "position"
            unused_old.remove(old_title)
    result = []
    for new_index, new_title in enumerate(new_titles):
        old_title = matches.get(new_title)
        old_paragraphs = old_sections.get(old_title, {}) if old_title else {}
        new_paragraphs = new_sections[new_title]
        old_text, new_text = _section_text(old_paragraphs), _section_text(new_paragraphs)
        title_changed = bool(old_title and old_title != new_title)
        content_changed = old_text != new_text
        old_index = old_pos.get(old_title) if old_title else None
        order_changed = old_index is not None and old_index != new_index
        if old_title is None:
            change_type = "added"
        elif title_changed and not content_changed:
            change_type = "renamed"
        elif content_changed:
            change_type = "rewritten" if title_changed else "modified"
        elif order_changed:
            change_type = "reordered"
        else:
            change_type = "unchanged"
        result.append({
            "section": old_title or new_title, "old_title": old_title or "", "new_title": new_title,
            "old_index": old_index, "new_index": new_index, "title_changed": title_changed,
            "content_changed": content_changed, "order_changed": order_changed,
            "match_method": match_methods.get(new_title, "unmatched"),
            "match_confidence": (
                "high" if match_methods.get(new_title) == "title"
                else "medium" if match_methods.get(new_title) == "content"
                else "low" if match_methods.get(new_title) == "position"
                else "none"
            ),
            "change_type": change_type,
            "change_kinds": [kind for kind, active in (("rename", title_changed), ("rewrite", content_changed), ("reorder", order_changed)) if active],
            "old_paragraphs": old_paragraphs, "new_paragraphs": new_paragraphs,
        })
    for old_title in unused_old:
        result.append({
            "section": old_title, "old_title": old_title, "new_title": "",
            "old_index": old_pos[old_title], "new_index": None, "title_changed": False,
            "content_changed": True, "order_changed": False, "match_method": "unmatched",
            "match_confidence": "none",
            "change_type": "removed", "change_kinds": ["remove"],
            "old_paragraphs": old_sections[old_title], "new_paragraphs": {},
        })
    return sorted(
        result,
        key=lambda item: (
            item.get("new_index") is None,
            item.get("new_index") if item.get("new_index") is not None else item.get("old_index") or 0,
        ),
    )


def restore_report_version_scope(version_id: int, scope: dict[str, Any], *, _session=None) -> dict[str, Any] | None:
    """Restore selected sentence/paragraph/section content from a version commit."""
    version = get_report_version(version_id)
    if version is None:
        return None
    report_id = int(version["report_id"])
    section = str(scope.get("base_section") or scope.get("section") or "")
    current_section = str(scope.get("current_section") or scope.get("section") or section)
    paragraph = scope.get("old_paragraph", scope.get("paragraph"))
    current_paragraph = scope.get("new_paragraph", scope.get("paragraph"))
    level = str(scope.get("level") or ("sentence" if scope.get("old_sentence_id") is not None else "paragraph" if paragraph is not None else "section"))
    old_sentence_id = scope.get("old_sentence_id")
    current_sentence_id = scope.get("current_sentence_id")
    if not section:
        return {"report_id": report_id, "restored": 0, "reason": "section_required"}
    old_groups = _sentence_groups(version.get("sentence_snapshot") or [])
    if section not in old_groups:
        return {"report_id": report_id, "restored": 0, "reason": "section_not_in_version"}
    targets: list[dict[str, Any]] = []
    if old_sentence_id is not None:
        for items in old_groups[section].values():
            targets.extend(item for item in items if str(item.get("id")) == str(old_sentence_id))
    elif level == "section":
        for items in old_groups[section].values():
            targets.extend(items)
    elif level == "paragraph":
        targets.extend(old_groups[section].get(int(paragraph), []))
    if not targets:
        return {"report_id": report_id, "restored": 0, "reason": "no_target"}
    manager = session_scope() if _session is None else nullcontext(_session)
    with manager as s:
        sentence_ids: list[int] = []
        if current_sentence_id is not None:
            sentence_ids = [int(current_sentence_id)] if str(current_sentence_id).isdigit() else []
        elif level == "section":
            rows = s.execute(
                select(ORMSentence.c.id).where(ORMSentence.c.report_id == report_id, ORMSentence.c.section == current_section)
            ).mappings().all()
            sentence_ids = [int(r["id"]) for r in rows]
        elif level == "paragraph":
            if current_paragraph is not None:
                rows = s.execute(
                    select(ORMSentence.c.id).where(
                        ORMSentence.c.report_id == report_id,
                        ORMSentence.c.section == current_section,
                        ORMSentence.c.paragraph == int(current_paragraph),
                    ).order_by(ORMSentence.c.position, ORMSentence.c.id)
                ).mappings().all()
                sentence_ids = [int(r["id"]) for r in rows]
        else:
            # A historical sentence that has no explicit current match is an
            # insertion, not a positional replacement.
            sentence_ids = []
        if sentence_ids:
            s.execute(ORMSentenceFact.delete().where(ORMSentenceFact.c.sentence_id.in_(sentence_ids)))
            s.execute(ORMSentenceInference.delete().where(ORMSentenceInference.c.sentence_id.in_(sentence_ids)))
            s.execute(ORMSentence.delete().where(ORMSentence.c.id.in_(sentence_ids)))
        for offset, item in enumerate(targets, start=1):
            refs = item.get("source_refs") or {}
            position = int(item.get("position") or 0) or offset
            cur = s.execute(
                ORMSentence.insert().values(
                    report_id=report_id,
                    lineage_id=item.get("lineage_id") or f"restored-{version_id}-{item.get('id')}",
                    parent_sentence_id=item.get("parent_sentence_id"),
                    section=current_section if level != "section" else section,
                    paragraph=int(current_paragraph or item.get("paragraph") or paragraph or 1),
                    position=position,
                    content=item.get("content", ""),
                    user_edit=item.get("user_edit", ""),
                    source_level=item.get("source_level", ""),
                    source_refs=_dump(refs),
                    selected=int(item.get("selected") if item.get("selected") is not None else 1),
                    origin_call_id=item.get("origin_call_id", ""),
                )
            )
            sid = int(cur.inserted_primary_key[0])
            for fid in refs.get("fact_ids") or []:
                if str(fid).isdigit():
                    s.execute(ORMSentenceFact.insert().values(sentence_id=sid, fact_id=int(fid)))
            for iid in refs.get("inference_ids") or []:
                if str(iid).isdigit():
                    s.execute(ORMSentenceInference.insert().values(sentence_id=sid, inference_id=int(iid)))
    return {
        "report_id": report_id,
        "restored": len(targets),
        "section": section,
        "base_section": section,
        "current_section": current_section,
        "paragraph": paragraph,
        "current_paragraph": current_paragraph,
        "old_sentence_id": old_sentence_id,
        "current_sentence_id": current_sentence_id,
    }


def save_report_change_decision(version_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Stage one review decision without mutating the candidate report."""
    version = get_report_version(version_id)
    if version is None:
        raise ValueError("REPORT_VERSION_NOT_FOUND")
    report_id = int(version["report_id"])
    current = build_report_snapshot(report_id, task_id=str(version.get("task_id") or ""))
    candidate_hash = str(payload.get("candidate_hash") or "")
    if not candidate_hash or candidate_hash != current["metadata"]["snapshot_hash"]:
        raise ValueError("CANDIDATE_CHANGED")
    change_key = str(payload.get("change_key") or "").strip()
    decision = str(payload.get("decision") or "").strip()
    if not change_key or decision not in {"keep_current", "use_base"}:
        raise ValueError("INVALID_DECISION")
    scope = dict(payload.get("scope") or {})
    scope["change_type"] = str(payload.get("change_type") or scope.get("change_type") or "")
    with session_scope() as s:
        pending = s.execute(select(ORMReportChangeDecision).where(
            ORMReportChangeDecision.c.base_version_id == version_id,
            ORMReportChangeDecision.c.candidate_hash == candidate_hash,
            ORMReportChangeDecision.c.status == "pending",
        )).mappings().all()
        conflicting_ids = []
        for row in pending:
            existing_scope = _json_load(row["scope_json"], {})
            if row["change_key"] != change_key and _review_scopes_overlap(scope, existing_scope):
                conflicting_ids.append(int(row["id"]))
        if conflicting_ids:
            s.execute(delete(ORMReportChangeDecision).where(
                ORMReportChangeDecision.c.id.in_(conflicting_ids)
            ))
        existing = s.execute(select(ORMReportChangeDecision.c.id).where(
            ORMReportChangeDecision.c.base_version_id == version_id,
            ORMReportChangeDecision.c.candidate_hash == candidate_hash,
            ORMReportChangeDecision.c.change_key == change_key,
        )).scalar()
        values = dict(
            report_id=report_id, base_version_id=version_id,
            candidate_hash=candidate_hash, change_key=change_key,
            scope_json=_dump(scope), decision=decision, status="pending",
        )
        if existing is None:
            cur = s.execute(insert(ORMReportChangeDecision).values(**values))
            decision_id = int(cur.inserted_primary_key[0])
        else:
            decision_id = int(existing)
            s.execute(update(ORMReportChangeDecision).where(
                ORMReportChangeDecision.c.id == decision_id
            ).values(**values))
    return {
        "id": decision_id, "change_key": change_key, "decision": decision,
        "status": "pending", "scope": scope, "cleared_decision_ids": conflicting_ids,
    }


def list_report_change_decisions(version_id: int, candidate_hash: str = "") -> list[dict[str, Any]]:
    with session_scope() as s:
        query = select(ORMReportChangeDecision).where(
            ORMReportChangeDecision.c.base_version_id == version_id
        )
        if candidate_hash:
            query = query.where(ORMReportChangeDecision.c.candidate_hash == candidate_hash)
        rows = s.execute(query.order_by(ORMReportChangeDecision.c.id)).mappings().all()
    return [{**dict(row), "scope": _json_load(row["scope_json"], {})} for row in rows]


def apply_report_change_decisions(version_id: int, candidate_hash: str) -> dict[str, Any]:
    """Apply staged decisions only if the reviewed candidate is unchanged."""
    version = get_report_version(version_id)
    if version is None:
        raise ValueError("REPORT_VERSION_NOT_FOUND")
    report_id = int(version["report_id"])
    current = build_report_snapshot(report_id, task_id=str(version.get("task_id") or ""))
    if candidate_hash != current["metadata"]["snapshot_hash"]:
        raise ValueError("CANDIDATE_CHANGED")
    decisions = [d for d in list_report_change_decisions(version_id, candidate_hash) if d["status"] == "pending"]
    if not decisions:
        return {"report_id": report_id, "applied": 0}
    ensure_report_version(
        report_id, task_id=str(version.get("task_id") or ""), status="snapshot",
        change_summary="应用差异决策前自动快照", kind="minor",
    )
    applied_ids: list[int] = []
    # Keep report mutations and review state in one transaction.
    with session_scope() as s:
        for decision in decisions:
            if decision["decision"] == "use_base":
                scope = dict(decision.get("scope") or {})
                if scope.get("change_type") == "added":
                    _delete_current_scope(report_id, scope, _session=s)
                else:
                    restore_report_version_scope(version_id, scope, _session=s)
            applied_ids.append(int(decision["id"]))
        if applied_ids:
            s.execute(update(ORMReportChangeDecision).where(
                ORMReportChangeDecision.c.id.in_(applied_ids)
            ).values(status="applied", applied_at=func.current_timestamp()))
        _normalize_sentence_positions(report_id, _session=s)
        result_version = ensure_report_version(
            report_id, task_id=str(version.get("task_id") or ""), status="draft",
            change_summary="应用逐句版本审阅决策", kind="minor", _session=s,
        )
    return {"report_id": report_id, "applied": len(applied_ids), "version_id": result_version.version_id}


def _delete_current_sentence(report_id: int, sentence_id: int, *, _session=None) -> None:
    manager = session_scope() if _session is None else nullcontext(_session)
    with manager as s:
        exists = s.execute(select(ORMSentence.c.id).where(
            ORMSentence.c.id == sentence_id, ORMSentence.c.report_id == report_id,
        )).scalar()
        if exists is None:
            return
        s.execute(ORMSentenceFact.delete().where(ORMSentenceFact.c.sentence_id == sentence_id))
        s.execute(ORMSentenceInference.delete().where(ORMSentenceInference.c.sentence_id == sentence_id))
        s.execute(ORMSentence.delete().where(ORMSentence.c.id == sentence_id))


def _delete_current_scope(report_id: int, scope: dict[str, Any], *, _session=None) -> None:
    """Delete a candidate-only sentence, paragraph, or section with lineage rows."""
    section = str(scope.get("current_section") or scope.get("section") or "")
    level = str(scope.get("level") or "sentence")
    manager = session_scope() if _session is None else nullcontext(_session)
    with manager as s:
        query = select(ORMSentence.c.id).where(ORMSentence.c.report_id == report_id)
        if level == "section":
            query = query.where(ORMSentence.c.section == section)
        elif level == "paragraph":
            query = query.where(
                ORMSentence.c.section == section,
                ORMSentence.c.paragraph == int(scope.get("new_paragraph", scope.get("paragraph")) or 0),
            )
        else:
            sentence_id = scope.get("current_sentence_id")
            if not str(sentence_id or "").isdigit():
                return
            query = query.where(ORMSentence.c.id == int(sentence_id))
        rows = s.execute(query).mappings().all()
        sentence_ids = [int(row["id"]) for row in rows]
        if not sentence_ids:
            return
        s.execute(ORMSentenceFact.delete().where(ORMSentenceFact.c.sentence_id.in_(sentence_ids)))
        s.execute(ORMSentenceInference.delete().where(ORMSentenceInference.c.sentence_id.in_(sentence_ids)))
        s.execute(ORMSentence.delete().where(ORMSentence.c.id.in_(sentence_ids)))


def _review_scope_level(scope: dict[str, Any]) -> str:
    level = str(scope.get("level") or "")
    if level in {"section", "paragraph", "sentence"}:
        return level
    if scope.get("old_sentence_id") is not None or scope.get("current_sentence_id") is not None:
        return "sentence"
    if scope.get("paragraph") is not None or scope.get("old_paragraph") is not None:
        return "paragraph"
    return "section"


def _review_scopes_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """A granularity switch replaces parent/child choices in the same scope."""
    left_sections = {
        str(left.get("base_section") or left.get("section") or ""),
        str(left.get("current_section") or left.get("section") or ""),
    }
    right_sections = {
        str(right.get("base_section") or right.get("section") or ""),
        str(right.get("current_section") or right.get("section") or ""),
    }
    if not (left_sections & right_sections):
        return False
    left_level, right_level = _review_scope_level(left), _review_scope_level(right)
    if "section" in {left_level, right_level}:
        return True
    left_paras = {
        str(value) for value in (left.get("paragraph"), left.get("old_paragraph"), left.get("new_paragraph"))
        if value is not None
    }
    right_paras = {
        str(value) for value in (right.get("paragraph"), right.get("old_paragraph"), right.get("new_paragraph"))
        if value is not None
    }
    if not (left_paras & right_paras):
        return False
    return "paragraph" in {left_level, right_level}


def _normalize_sentence_positions(report_id: int, *, _session=None) -> None:
    manager = session_scope() if _session is None else nullcontext(_session)
    with manager as s:
        rows = s.execute(select(ORMSentence.c.id).where(
            ORMSentence.c.report_id == report_id
        ).order_by(ORMSentence.c.position, ORMSentence.c.id)).mappings().all()
        for position, row in enumerate(rows, start=1):
            s.execute(update(ORMSentence).where(ORMSentence.c.id == row["id"]).values(position=position))


def _sentence_groups(sentences: list[dict[str, Any]]) -> dict[str, dict[int, list[dict[str, Any]]]]:
    groups: dict[str, dict[int, list[dict[str, Any]]]] = {}
    ordered = sorted(sentences, key=lambda item: (int(item.get("position") or 0), int(item.get("id") or 0)))
    for item in ordered:
        section = str(item.get("section") or "")
        if not section:
            continue
        paragraph = int(item.get("paragraph") or 1)
        text = str(item.get("rendered_text") or item.get("user_edit") or item.get("content") or "")
        groups.setdefault(section, {}).setdefault(paragraph, []).append({**item, "text": text})
    return groups


def _align_paragraphs(old_paras: dict[int, list[dict]], new_paras: dict[int, list[dict]]) -> list[tuple[int | None, int | None]]:
    """Sequence-align paragraphs so one insertion does not shift the whole diff."""
    old_keys = sorted(old_paras)
    new_keys = sorted(new_paras)
    n, m = len(old_keys), len(new_keys)
    scores = [[_paragraph_similarity(old_paras[ok], new_paras[nk]) for nk in new_keys] for ok in old_keys]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    take = [[False] * m for _ in range(n)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            match = scores[i][j] if scores[i][j] >= 0.28 else 0.0
            best = max(dp[i + 1][j], dp[i][j + 1])
            if match and match + dp[i + 1][j + 1] >= best:
                best = match + dp[i + 1][j + 1]
                take[i][j] = True
            dp[i][j] = best
    result: list[tuple[int | None, int | None]] = []
    i = j = 0
    while i < n or j < m:
        if i < n and j < m and take[i][j]:
            result.append((old_keys[i], new_keys[j]))
            i += 1
            j += 1
        elif j < m and (i >= n or dp[i][j + 1] > dp[i + 1][j]):
            result.append((None, new_keys[j]))
            j += 1
        else:
            result.append((old_keys[i], None))
            i += 1
    return result


def _paragraph_similarity(old_items: list[dict], new_items: list[dict]) -> float:
    old_lineages = {str(item.get("lineage_id")) for item in old_items if item.get("lineage_id")}
    new_lineages = {str(item.get("lineage_id")) for item in new_items if item.get("lineage_id")}
    if old_lineages and new_lineages and old_lineages & new_lineages:
        return 1.2
    old_text = "".join(str(item.get("text") or "") for item in old_items)
    new_text = "".join(str(item.get("text") or "") for item in new_items)
    return _sentence_similarity(old_text, new_text)


def _sequence_diff(old_items: list[dict[str, Any]], new_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Similarity-aware LCS diff for report sentences.

    Position-only diff breaks when a sentence is inserted or deleted. This keeps
    stable/similar sentences aligned and marks the rest as added/removed.
    """
    pairs = _lcs_pairs(old_items, new_items)
    result: list[dict[str, Any]] = []
    old_cursor = 0
    new_cursor = 0
    for old_idx, new_idx, score in pairs:
        while old_cursor < old_idx:
            old = old_items[old_cursor]
            result.append(_diff_item(old_cursor, None, old, None, "removed", 0.0))
            old_cursor += 1
        while new_cursor < new_idx:
            new = new_items[new_cursor]
            result.append(_diff_item(None, new_cursor, None, new, "added", 0.0))
            new_cursor += 1
        old = old_items[old_idx]
        new = new_items[new_idx]
        old_text = old.get("text", "")
        new_text = new.get("text", "")
        change = "unchanged" if old_text == new_text and _source_signature(old) == _source_signature(new) else "provenance_changed" if old_text == new_text else "modified"
        result.append(_diff_item(old_idx, new_idx, old, new, change, score))
        old_cursor = old_idx + 1
        new_cursor = new_idx + 1
    while old_cursor < len(old_items):
        old = old_items[old_cursor]
        result.append(_diff_item(old_cursor, None, old, None, "removed", 0.0))
        old_cursor += 1
    while new_cursor < len(new_items):
        new = new_items[new_cursor]
        result.append(_diff_item(None, new_cursor, None, new, "added", 0.0))
        new_cursor += 1
    return result


def _lcs_pairs(old_items: list[dict[str, Any]], new_items: list[dict[str, Any]]) -> list[tuple[int, int, float]]:
    n, m = len(old_items), len(new_items)
    scores = [[
        1.2 if old_items[i].get("lineage_id") and old_items[i].get("lineage_id") == new_items[j].get("lineage_id")
        else _sentence_similarity(old_items[i].get("text", ""), new_items[j].get("text", ""))
        for j in range(m)
    ] for i in range(n)]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    take = [[False] * m for _ in range(n)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            match = scores[i][j] if scores[i][j] >= _match_threshold(old_items[i].get("text", ""), new_items[j].get("text", "")) else 0.0
            best = dp[i + 1][j]
            if dp[i][j + 1] > best:
                best = dp[i][j + 1]
            if match and match + dp[i + 1][j + 1] >= best:
                best = match + dp[i + 1][j + 1]
                take[i][j] = True
            dp[i][j] = best
    pairs = []
    i = j = 0
    while i < n and j < m:
        if take[i][j]:
            pairs.append((i, j, scores[i][j]))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _diff_item(old_index, new_index, old, new, change: str, score: float) -> dict[str, Any]:
    lineage = (new or {}).get("lineage_id") or (old or {}).get("lineage_id")
    change_key = f"lineage:{lineage}" if lineage else "content:" + _stable_json_hash({
        "old": (old or {}).get("text", ""), "new": (new or {}).get("text", ""),
        "old_id": (old or {}).get("id"), "new_id": (new or {}).get("id"),
    })
    return {
        "index": new_index if new_index is not None else old_index,
        "old_index": old_index,
        "new_index": new_index,
        "change_type": change,
        "change_key": change_key,
        "similarity": round(float(score or 0), 4),
        "old_text": old.get("text", "") if old else "",
        "new_text": new.get("text", "") if new else "",
        "old_sentence_id": old.get("id") if old else None,
        "current_sentence_id": new.get("id") if new else None,
        "old_sentence": old,
        "new_sentence": new,
        "inline_diff": _inline_text_diff(
            old.get("text", "") if old else "",
            new.get("text", "") if new else "",
        ),
    }


def _inline_text_diff(old_text: str, new_text: str) -> dict[str, list[dict[str, str]]]:
    """Character-level spans make Chinese prose changes readable in context."""
    from difflib import SequenceMatcher

    old_spans: list[dict[str, str]] = []
    new_spans: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old_text or "", new_text or "").get_opcodes():
        if tag in {"equal", "delete", "replace"} and i1 != i2:
            old_spans.append({"type": "unchanged" if tag == "equal" else "removed", "text": old_text[i1:i2]})
        if tag in {"equal", "insert", "replace"} and j1 != j2:
            new_spans.append({"type": "unchanged" if tag == "equal" else "added", "text": new_text[j1:j2]})
    return {"old": old_spans, "new": new_spans}


def _sentence_similarity(left: str, right: str) -> float:
    if left == right and left:
        return 1.0
    from difflib import SequenceMatcher

    overlap = _text_overlap(left, right)
    seq = SequenceMatcher(None, left or "", right or "").ratio()
    len_ratio = min(len(left or ""), len(right or "")) / max(len(left or ""), len(right or ""), 1)
    return max(overlap * 0.75 + len_ratio * 0.25, seq * 0.85 + len_ratio * 0.15)


def _source_signature(item: dict[str, Any]) -> tuple:
    refs = item.get("source_refs") or {}
    if isinstance(refs, str):
        refs = _json_load(refs, {})
    if not isinstance(refs, dict):
        refs = {}
    return (
        tuple(sorted(str(value) for value in refs.get("fact_ids") or [])),
        tuple(sorted(str(value) for value in refs.get("inference_ids") or [])),
        str(item.get("source_level") or ""),
    )

def _match_threshold(left: str, right: str) -> float:
    shortest = min(len(left or ""), len(right or ""))
    if shortest <= 10:
        return 0.86
    if shortest <= 20:
        return 0.72
    if shortest <= 60:
        return 0.62
    return 0.48


def _fine_diff_summary(sections: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"sections_changed": 0, "paragraphs_changed": 0, "sentences_added": 0, "sentences_modified": 0, "sentences_removed": 0,
              "sentences_provenance_changed": 0}
    for section in sections:
        if section.get("change_type") != "unchanged":
            counts["sections_changed"] += 1
        for para in section.get("paragraphs") or []:
            if para.get("change_type") != "unchanged":
                counts["paragraphs_changed"] += 1
            for sent in para.get("sentences") or []:
                key = f"sentences_{sent.get('change_type')}"
                if key in counts:
                    counts[key] += 1
    return counts

def build_incremental_impact(report_id: int, delta_id: int, task_id: str = "") -> dict[str, Any]:
    """Compute an incremental impact plan from immutable base version to current task state.

    The plan is deterministic and domain-neutral. It does not decide what the
    report should say; it only identifies which old sections are likely affected
    and whether structure changed enough to require human confirmation.
    """
    with session_scope() as s:
        delta = s.execute(
            select(ORMReportVersionDelta).where(ORMReportVersionDelta.c.id == int(delta_id))
        ).mappings().first()
        if delta is None:
            raise ValueError("REPORT_DELTA_NOT_FOUND")
        base = s.execute(
            select(ORMReportVersion).where(ORMReportVersion.c.id == delta["from_version_id"])
        ).mappings().first()
        if base is None:
            raise ValueError("REPORT_VERSION_NOT_FOUND")
    current = build_report_snapshot(report_id, task_id=task_id)
    old_facts = _json_load(base["fact_snapshot"], [])
    old_inferences = _json_load(base["inference_snapshot"], [])
    old_conflicts = _json_load(base["conflict_snapshot"], [])
    fact_delta = _object_delta(old_facts, current["fact_snapshot"])
    inference_delta = _object_delta(old_inferences, current["inference_snapshot"])
    superseded_fact_ids = {
        int(item["id"]) for item in current["fact_snapshot"]
        if item.get("id") is not None and item.get("lifecycle_status") == "superseded"
    }
    if superseded_fact_ids:
        old_by_id = {int(item["id"]): item for item in old_facts if item.get("id") is not None}
        fact_delta["deprecated"] = list(fact_delta["deprecated"]) + [
            old_by_id[fact_id] for fact_id in sorted(superseded_fact_ids) if fact_id in old_by_id
        ]
        fact_delta["modified"] = [
            item for item in fact_delta["modified"]
            if int((item.get("current") or {}).get("id") or -1) not in superseded_fact_ids
        ]
    conflict_delta = _object_delta(old_conflicts, current["conflict_snapshot"])
    base_plan = _json_load(base["report_plan_snapshot"], {})
    current_plan = current.get("report_plan_snapshot") or {}
    structure_changes = _structure_changes(base_plan.get("structure") or [], current_plan.get("structure") or [])
    added_facts = fact_delta["added"] + fact_delta["deprecated"] + [item.get("current", {}) for item in fact_delta["modified"]]
    added_inferences = inference_delta["added"] + [item.get("current", {}) for item in inference_delta["modified"]]
    affected = _affected_chapters_by_similarity(
        _json_load(base["sentence_snapshot"], []),
        added_facts,
        added_inferences,
    )
    if not affected and added_facts:
        affected = [{"section": title, "fact_ids": [], "inference_ids": [], "severity": "major", "reason": "new_evidence_unmapped"}
                    for title in (current_plan.get("structure") or base_plan.get("structure") or [])]
    cross_version_conflicts = _cross_version_conflicts(old_facts, added_facts)
    requires_structure_review = bool(structure_changes and _structure_review_required(structure_changes))
    return {
        "report_id": report_id,
        "delta_id": delta_id,
        "fact_delta": fact_delta,
        "inference_delta": inference_delta,
        "conflict_delta": conflict_delta,
        "affected_chapters": affected,
        "structure_changes": structure_changes,
        "cross_version_conflicts": cross_version_conflicts,
        "requires_structure_review": requires_structure_review,
        "rewrite_sections": [item["section"] for item in affected if item.get("section")],
    }


def _structure_changes(old_structure: list, new_structure: list) -> list[dict[str, Any]]:
    old = [str(x).strip() for x in old_structure if str(x).strip()]
    new = [str(x).strip() for x in new_structure if str(x).strip()]
    changes: list[dict[str, Any]] = []
    old_set, new_set = set(old), set(new)
    for title in new:
        if title not in old_set:
            changes.append({"type": "added_section", "section": title, "severity": "major"})
    for title in old:
        if title not in new_set:
            changes.append({"type": "removed_section", "section": title, "severity": "major"})
    for title in new_set & old_set:
        if old.index(title) != new.index(title):
            changes.append({"type": "reordered_section", "section": title, "severity": "minor"})
    return changes


def _structure_review_required(changes: list[dict[str, Any]]) -> bool:
    return any(item.get("severity") == "major" for item in changes)


def _affected_chapters_by_similarity(sentences: list[dict[str, Any]], facts: list[dict[str, Any]],
                                     inferences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence_items = []
    for item in facts:
        evidence_items.append({"id": item.get("id"), "kind": "fact", "text": item.get("content", "")})
    for item in inferences:
        evidence_items.append({"id": item.get("id"), "kind": "inference", "text": item.get("content", "")})
    if not evidence_items:
        return []
    section_text: dict[str, str] = {}
    for sentence in sentences:
        section = str(sentence.get("section") or "")
        if not section:
            continue
        section_text[section] = section_text.get(section, "") + "\n" + str(sentence.get("rendered_text") or sentence.get("content") or "")
    scored: dict[str, dict[str, Any]] = {}
    for section, text in section_text.items():
        item = scored.setdefault(section, {"section": section, "score": 0.0, "fact_ids": set(), "inference_ids": set(), "severity": "minor"})
        for ev in evidence_items:
            score = _text_overlap(ev["text"], section + "\n" + text)
            if score <= 0:
                continue
            item["score"] += score
            if ev["kind"] == "fact" and ev.get("id") is not None:
                item["fact_ids"].add(int(ev["id"]))
            if ev["kind"] == "inference" and ev.get("id") is not None:
                item["inference_ids"].add(int(ev["id"]))
    positives = [item for item in scored.values() if item["score"] > 0]
    if not positives:
        return []
    avg = sum(item["score"] for item in positives) / len(positives)
    result = []
    for item in positives:
        if item["score"] >= avg:
            result.append({
                "section": item["section"],
                "fact_ids": sorted(item["fact_ids"]),
                "inference_ids": sorted(item["inference_ids"]),
                "severity": "major" if item["score"] > avg else "minor",
                "reason": "semantic_overlap",
                "score": round(float(item["score"]), 4),
            })
    return result


def _cross_version_conflicts(old_facts: list[dict[str, Any]], new_facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for old in old_facts:
        old_text = str(old.get("content") or "")
        if not old_text:
            continue
        for new in new_facts:
            new_text = str(new.get("content") or "")
            if not new_text:
                continue
            signals = _conflict_signals(old_text, new_text)
            if not signals:
                continue
            overlap = _text_overlap(old_text, new_text)
            if overlap <= 0:
                continue
            candidates.append({
                "old_fact_id": old.get("id"),
                "new_fact_id": new.get("id"),
                "signals": signals,
                "overlap": round(overlap, 4),
                "old_content": old_text,
                "new_content": new_text,
                "status": "needs_review",
            })
    return sorted(candidates, key=lambda item: item["overlap"], reverse=True)[:30]


def _conflict_signals(left: str, right: str) -> list[str]:
    import re as _re
    signals: list[str] = []
    number_re = _re.compile(r"\d+(?:\.\d+)?%?")
    date_re = _re.compile(r"(?:\d{4}年|\d{1,2}月\d{1,2}日|\d{1,2}日前)")
    nums_l, nums_r = set(number_re.findall(left)), set(number_re.findall(right))
    dates_l, dates_r = set(date_re.findall(left)), set(date_re.findall(right))
    if nums_l and nums_r and nums_l != nums_r:
        signals.append("number_mismatch")
    if dates_l and dates_r and dates_l != dates_r:
        signals.append("date_mismatch")
    groups = (("必须", "应当", "需", "不得", "禁止", "可", "可以", "允许", "自愿"),
              ("已", "已完成", "未", "未完成", "尚未", "无", "有"))
    for words in groups:
        l_hits = {w for w in words if w in left}
        r_hits = {w for w in words if w in right}
        if l_hits and r_hits and l_hits != r_hits:
            signals.append("modality_or_status_mismatch")
            break
    return signals


def _text_overlap(left: str, right: str) -> float:
    import re as _re
    a = _re.sub(r"\s+", "", left or "")
    b = _re.sub(r"\s+", "", right or "")
    if len(a) < 2 or len(b) < 2:
        return 0.0
    grams_a = {a[i:i + 2] for i in range(len(a) - 1)}
    grams_b = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(grams_a & grams_b) / max(len(grams_a), 1)

def _task_payload_in_session(s, task_id: str) -> dict[str, Any]:
    if not task_id:
        return {}
    payload = s.execute(
        select(ORMShortMemory.c.payload).where(ORMShortMemory.c.task_id == task_id)
    ).scalar()
    return _json_load(payload, {})


def _version_summary(row) -> dict[str, Any]:
    meta = _json_load(row["metadata_json"], {})
    version_no = int(row["version_no"])
    major, minor = _version_components(row)
    label = _version_label(major, minor)
    return {
        "id": row["id"],
        "report_id": row["report_id"],
        "version_no": version_no,
        "version_label": label,
        "version_major": major,
        "version_minor": minor,
        "based_on_version_id": row["based_on_version_id"],
        "task_id": row["task_id"],
        "run_id": row.get("run_id", ""),
        "status": row["status"],
        "title": row["title"],
        "user_requirements": row.get("user_requirements", ""),
        "template_id": row.get("template_id"),
        "change_summary": row["change_summary"],
        "created_at": row["created_at"],
        "metadata": meta,
    }


def _version_components(row) -> tuple[int, int]:
    major = int(row.get("version_major") or row.get("version_no") or 1)
    minor = int(row.get("version_minor") or 0)
    return major, minor


def _version_label(major: int, minor: int) -> str:
    return str(major) if minor <= 0 else f"{major}.{minor}"


def _version_detail(row) -> dict[str, Any]:
    detail = _version_summary(row)
    detail.update({
        "material_fingerprints": _json_load(row["material_fingerprints"], []),
        "report_plan_snapshot": _json_load(row["report_plan_snapshot"], {}),
        "narrative_plan_snapshot": _json_load(row["narrative_plan_snapshot"], {}),
        "scale_plan_snapshot": _json_load(row["scale_plan_snapshot"], {}),
        "fact_snapshot": _json_load(row["fact_snapshot"], []),
        "inference_snapshot": _json_load(row["inference_snapshot"], []),
        "conflict_snapshot": _json_load(row["conflict_snapshot"], []),
        "sentence_snapshot": _json_load(row["sentence_snapshot"], []),
    })
    return detail


def _delta_detail(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "report_id": row["report_id"],
        "from_version_id": row["from_version_id"],
        "to_version_id": row["to_version_id"],
        "status": row["status"],
        "update_reason": row["update_reason"],
        "added_materials": _json_load(row["added_materials"], []),
        "duplicate_materials": _json_load(row["duplicate_materials"], []),
        "added_facts": _json_load(row["added_facts"], []),
        "modified_facts": _json_load(row["modified_facts"], []),
        "deprecated_facts": _json_load(row["deprecated_facts"], []),
        "added_inferences": _json_load(row["added_inferences"], []),
        "modified_inferences": _json_load(row["modified_inferences"], []),
        "deprecated_inferences": _json_load(row["deprecated_inferences"], []),
        "new_conflicts": _json_load(row["new_conflicts"], []),
        "resolved_conflicts": _json_load(row["resolved_conflicts"], []),
        "affected_chapters": _json_load(row["affected_chapters"], []),
        "structure_changes": _json_load(row["structure_changes"], []),
        "evidence_changes": _json_load(row["evidence_changes"], {}),
        "metadata": _json_load(row["metadata_json"], {}),
        "created_at": row["created_at"],
    }


def _json_load(text, default):
    try:
        return json.loads(text or "")
    except (TypeError, ValueError):
        return default


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _stable_json_hash(value: Any) -> str:
    from hashlib import sha256

    return sha256(_dump(value).encode("utf-8")).hexdigest()


_SNAPSHOT_HASH_VERSION = 2
_VOLATILE_HASH_KEYS = {
    "id",
    "material_id",
    "unit_id",
    "fact_id",
    "inference_id",
    "evidence_id",
    "conflict_id",
    "sentence_id",
    "task_id",
    "run_id",
    "origin_call_id",
    "lineage_id",
    "parent_sentence_id",
    "introduced_run_id",
    "superseded_by_fact_id",
    "superseded_by_inference_id",
    "created_at",
    "updated_at",
    "finalized_at",
}


def _hash_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _canonical_hash_value(value: Any, key: str = "") -> Any:
    """Remove storage/runtime identity from nested snapshot values."""
    if isinstance(value, dict):
        return {
            str(name): _canonical_hash_value(child, str(name))
            for name, child in sorted(value.items(), key=lambda item: str(item[0]))
            if str(name) not in _VOLATILE_HASH_KEYS
        }
    if isinstance(value, list):
        return [_canonical_hash_value(item, key) for item in value]
    if isinstance(value, str):
        return _hash_text(value)
    return value


def _artifact_key(item: dict[str, Any], prefix: str) -> str:
    stable = _hash_text(item.get("stable_key"))
    if stable:
        return stable
    content = _hash_text(item.get("content"))
    dimension = _hash_text(item.get("dimension"))
    if content or dimension:
        return f"{dimension}:{content}"
    return f"{prefix}:{item.get('id', '')}"


def _content_hash_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build a content-only projection for snapshot de-duplication.

    Version identity belongs to the report-version table. It must not be
    derived from database row ids, model call ids, or sentence lineage ids;
    those values may change when an unchanged draft is rebuilt.
    """
    facts = list(snapshot.get("fact_snapshot") or snapshot.get("facts") or [])
    inferences = list(snapshot.get("inference_snapshot") or snapshot.get("inferences") or [])
    fact_keys = {str(item.get("id")): _artifact_key(item, "fact") for item in facts}
    inference_keys = {str(item.get("id")): _artifact_key(item, "inference") for item in inferences}

    stable_facts = []
    for item in facts:
        evidence = []
        for entry in item.get("evidence") or []:
            evidence.append({
                "source_file": _hash_text(entry.get("source_file")),
                "page": entry.get("page"),
                "paragraph": entry.get("paragraph"),
                "quote": _hash_text(entry.get("quote")),
            })
        stable_facts.append({
            "key": fact_keys.get(str(item.get("id")), _artifact_key(item, "fact")),
            "content": _hash_text(item.get("content")),
            "dimension": _hash_text(item.get("dimension")),
            "need_id": item.get("need_id"),
            "source_level": _hash_text(item.get("source_level")),
            "evidence": sorted(evidence, key=lambda value: _dump(value)),
            "lifecycle_status": _hash_text(item.get("lifecycle_status")),
        })

    stable_inferences = []
    for item in inferences:
        stable_inferences.append({
            "key": inference_keys.get(str(item.get("id")), _artifact_key(item, "inference")),
            "content": _hash_text(item.get("content")),
            "source_level": _hash_text(item.get("source_level")),
            "based_fact_keys": sorted(
                fact_keys.get(str(fact_id), f"fact:{fact_id}")
                for fact_id in (item.get("based_fact_ids") or [])
            ),
            "reasoning_chain": _hash_text(item.get("reasoning_chain")),
            "dimension": _hash_text(item.get("dimension")),
            "analysis_type": _hash_text(item.get("analysis_type")),
            "confidence_level": _hash_text(item.get("confidence_level")),
            "confidence_reason": _hash_text(item.get("confidence_reason")),
            "uncertainty": _hash_text(item.get("uncertainty")),
            "lifecycle_status": _hash_text(item.get("lifecycle_status")),
        })

    stable_sentences = []
    for item in snapshot.get("sentence_snapshot") or snapshot.get("sentences") or []:
        refs = item.get("source_refs") or {}
        stable_sentences.append({
            "section": _hash_text(item.get("section")),
            "paragraph": item.get("paragraph"),
            "position": item.get("position"),
            "content": _hash_text(item.get("user_edit") or item.get("content")),
            "source_level": _hash_text(item.get("source_level")),
            "fact_keys": sorted(
                fact_keys.get(str(fact_id), f"fact:{fact_id}")
                for fact_id in (refs.get("fact_ids") or [])
            ),
            "inference_keys": sorted(
                inference_keys.get(str(inference_id), f"inference:{inference_id}")
                for inference_id in (refs.get("inference_ids") or [])
            ),
            "selected": item.get("selected", 1),
        })

    materials = [
        _canonical_hash_value(item)
        for item in (snapshot.get("material_fingerprints") or snapshot.get("materials") or [])
    ]
    return {
        "title": _hash_text(snapshot.get("title")),
        "materials": sorted(materials, key=lambda value: _dump(value)),
        "facts": sorted(stable_facts, key=lambda value: value["key"]),
        "inferences": sorted(stable_inferences, key=lambda value: value["key"]),
        "conflicts": _canonical_hash_value(snapshot.get("conflict_snapshot") or snapshot.get("conflicts") or []),
        "sentences": stable_sentences,
        "plan": _canonical_hash_value(snapshot.get("report_plan_snapshot") or snapshot.get("plan") or {}),
        "narrative_plan": _canonical_hash_value(snapshot.get("narrative_plan_snapshot") or snapshot.get("narrative_plan") or {}),
        "scale_plan": _canonical_hash_value(snapshot.get("scale_plan_snapshot") or snapshot.get("scale_plan") or {}),
    }


def _content_snapshot_hash(snapshot: dict[str, Any]) -> str:
    return _stable_json_hash(_content_hash_payload(snapshot))


def _stored_version_hash(row) -> str:
    """Recompute a stable hash for both new and legacy version rows."""
    snapshot = {
        "title": row.get("title", ""),
        "material_fingerprints": _json_load(row.get("material_fingerprints"), []),
        "report_plan_snapshot": _json_load(row.get("report_plan_snapshot"), {}),
        "narrative_plan_snapshot": _json_load(row.get("narrative_plan_snapshot"), {}),
        "scale_plan_snapshot": _json_load(row.get("scale_plan_snapshot"), {}),
        "fact_snapshot": _json_load(row.get("fact_snapshot"), []),
        "inference_snapshot": _json_load(row.get("inference_snapshot"), []),
        "conflict_snapshot": _json_load(row.get("conflict_snapshot"), []),
        "sentence_snapshot": _json_load(row.get("sentence_snapshot"), []),
    }
    return _content_snapshot_hash(snapshot)


def _build_sentence_diff(base: dict[str, Any], target: dict[str, Any], *,
                         base_version_id: int, target_version_id: int | None,
                         comparison_mode: str, can_apply: bool) -> dict[str, Any]:
    old_sections = _sentence_groups(base.get("sentence_snapshot") or [])
    new_sections = _sentence_groups(target.get("sentence_snapshot") or [])
    sections: list[dict[str, Any]] = []
    for pair in _pair_sections(old_sections, new_sections):
        old_title = pair.get("old_title") or ""
        new_title = pair.get("new_title") or ""
        old_paras = pair.get("old_paragraphs") or {}
        new_paras = pair.get("new_paragraphs") or {}
        paragraphs: list[dict[str, Any]] = []
        for old_para_no, new_para_no in _align_paragraphs(old_paras, new_paras):
            old_items = old_paras.get(old_para_no, []) if old_para_no is not None else []
            new_items = new_paras.get(new_para_no, []) if new_para_no is not None else []
            sentence_diffs = _sequence_diff(old_items, new_items)
            if old_items and not new_items:
                paragraph_type = "removed"
            elif new_items and not old_items:
                paragraph_type = "added"
            else:
                changed = [item["change_type"] for item in sentence_diffs if item["change_type"] != "unchanged"]
                if not changed:
                    paragraph_type = "unchanged"
                elif set(changed) == {"provenance_changed"}:
                    paragraph_type = "provenance_changed"
                else:
                    paragraph_type = "modified"
            paragraphs.append({
                "paragraph": new_para_no if new_para_no is not None else old_para_no,
                "old_paragraph": old_para_no,
                "new_paragraph": new_para_no,
                "change_key": "paragraph:" + _stable_json_hash({
                    "base_section": old_title,
                    "current_section": new_title,
                    "old": old_para_no,
                    "new": new_para_no,
                }),
                "change_type": paragraph_type,
                "old_text": "".join(item["text"] for item in old_items),
                "new_text": "".join(item["text"] for item in new_items),
                "sentences": sentence_diffs,
            })
        paragraph_changes = [item["change_type"] for item in paragraphs if item["change_type"] != "unchanged"]
        section_type = pair.get("change_type") or "unchanged"
        if section_type == "unchanged" and paragraph_changes:
            section_type = "provenance_changed" if set(paragraph_changes) == {"provenance_changed"} else "modified"
        sections.append({
            "section": old_title or new_title,
            "base_section": old_title,
            "current_section": new_title,
            "old_title": old_title,
            "new_title": new_title,
            "old_index": pair.get("old_index"),
            "new_index": pair.get("new_index"),
            "title_changed": bool(pair.get("title_changed")),
            "content_changed": bool(pair.get("content_changed")),
            "order_changed": bool(pair.get("order_changed")),
            "match_method": pair.get("match_method"),
            "match_confidence": pair.get("match_confidence", "none"),
            "change_type": section_type,
            "change_kinds": pair.get("change_kinds") or [],
            "change_key": "section:" + _stable_json_hash({
                "base_section": old_title,
                "current_section": new_title,
                "old_index": pair.get("old_index"),
                "new_index": pair.get("new_index"),
            }),
            "paragraphs": paragraphs,
        })
    return {
        "base_version_id": base_version_id,
        "target_version_id": target_version_id,
        "comparison_mode": comparison_mode,
        "can_apply": can_apply,
        "report_id": base["report_id"],
        "old_title": base.get("title", ""),
        "new_title": target.get("title", ""),
        "candidate_hash": target.get("metadata", {}).get("snapshot_hash", ""),
        "sections": sections,
        "summary": _fine_diff_summary(sections),
    }


def compare_report_versions(base_version_id: int, target_version_id: int | None = None,
                            task_id: str = "") -> dict[str, Any] | None:
    """Compare two immutable versions, or a version with the current draft."""
    base = get_report_version(base_version_id)
    if base is None:
        return None
    report_id = int(base["report_id"])
    if target_version_id is None:
        target = build_report_snapshot(report_id, task_id=task_id or base.get("task_id") or "")
        return _build_sentence_diff(
            base, target, base_version_id=base_version_id, target_version_id=None,
            comparison_mode="current_draft", can_apply=True,
        )
    target = get_report_version(target_version_id)
    if target is None or int(target["report_id"]) != report_id:
        return None
    return _build_sentence_diff(
        base, target, base_version_id=base_version_id, target_version_id=target_version_id,
        comparison_mode="version", can_apply=False,
    )


def _paired_structure_changes(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose structural changes without splitting a logical rename into add/remove."""
    changes: list[dict[str, Any]] = []
    for section in sections:
        change_type = section.get("change_type")
        old_title = str(section.get("old_title") or "")
        new_title = str(section.get("new_title") or "")
        display_title = new_title or old_title
        if change_type == "added":
            changes.append({"type": "added_section", "section": display_title, "severity": "major"})
        elif change_type == "removed":
            changes.append({"type": "removed_section", "section": old_title, "severity": "major"})
        elif change_type == "renamed":
            changes.append({
                "type": "renamed_section",
                "from": old_title,
                "to": new_title,
                "section": display_title,
                "severity": "minor",
            })
        elif change_type == "rewritten":
            changes.append({
                "type": "rewritten_section",
                "from": old_title,
                "to": new_title,
                "section": display_title,
                "severity": "minor",
            })
        elif change_type == "reordered":
            changes.append({
                "type": "reordered_section",
                "section": display_title,
                "from_index": section.get("old_index"),
                "to_index": section.get("new_index"),
                "severity": "minor",
            })
    return changes


def diff_report_version_to_current(version_id: int, task_id: str = "") -> dict[str, Any] | None:
    """Compatibility view of the unified version-to-current comparator."""
    detailed = compare_report_versions(version_id, task_id=task_id)
    if detailed is None:
        return None
    section_diffs = [{
        "section": section["section"],
        "old_title": section["old_title"],
        "new_title": section["new_title"],
        "change_type": section["change_type"],
        "old_text": "".join(p["old_text"] for p in section["paragraphs"]),
        "new_text": "".join(p["new_text"] for p in section["paragraphs"]),
        "old_chars": sum(len(p["old_text"]) for p in section["paragraphs"]),
        "new_chars": sum(len(p["new_text"]) for p in section["paragraphs"]),
    } for section in detailed["sections"]]
    detailed["structure_changes"] = _paired_structure_changes(detailed["sections"])
    detailed["section_diffs"] = section_diffs
    detailed["summary"].update({
        "added_sections": sum(1 for s in detailed["sections"] if s["change_type"] == "added"),
        "modified_sections": sum(1 for s in detailed["sections"] if s["change_type"] in {"modified", "rewritten", "provenance_changed"}),
        "removed_sections": sum(1 for s in detailed["sections"] if s["change_type"] == "removed"),
    })
    return detailed


def diff_report_version_sentences(version_id: int, task_id: str = "") -> dict[str, Any] | None:
    """Sentence/paragraph diff backed by the unified section pairing model."""
    return compare_report_versions(version_id, task_id=task_id)

__all__ = [
    "VersionSnapshot",
    "ensure_report_version",
    "build_report_snapshot",
    "list_report_versions",
    "get_report_version",
    "latest_report_version",
    "create_incremental_delta",
    "build_incremental_impact",
    "diff_report_version_to_current",
    "diff_report_version_sentences",
    "compare_report_versions",
    "restore_report_version",
    "restore_report_version_scope",
    "get_report_delta",
    "refresh_incremental_delta",
    "attach_delta_version",
]
