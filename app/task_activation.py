"""Atomic database transitions that activate a new run for an existing task."""
from __future__ import annotations

from datetime import datetime
import time
from typing import Any

from app.db import session_scope
from app.memory import short_term
from app.report_versions import create_incremental_delta, ensure_report_version, get_report_version
from app.task_runs import create_task_run
from app.workflow.execution import require_idle_task
from app.workflow.queue import task_queue_status


def _snapshot_ids(rows: list[dict], *, exclude_external: bool = False) -> list[int]:
    result = []
    for item in rows:
        raw_id = item.get("id")
        if not str(raw_id or "").isdigit():
            continue
        if exclude_external and str(item.get("source_level") or "") == "EXTERNAL_INFORMATION":
            continue
        result.append(int(raw_id))
    return result


def activate_incremental_task(task_id: str, report: dict[str, Any], submitted_material_ids: list[int], *,
                              update_reason: str = "", source_comparison_id: int | None = None,
                              comparison_handoff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create Run, Delta, and next task state as one locked PostgreSQL transition."""
    report_id = int(report["id"])
    if not submitted_material_ids and not update_reason.strip():
        raise ValueError("UPDATE_REASON_REQUIRED")

    result: dict[str, Any] = {}
    # Snapshot process-local queue state before taking the database task-row
    # lock. Queue operations persist task state while holding their own lock;
    # acquiring that lock from inside this transaction would invert lock order.
    queues = task_queue_status()

    def transition(current: dict, tx) -> dict:
        stage = str(current.get("stage") or "")
        queue = dict(current.get("queue_status") or {})
        queue_state = str(queue.get("status") or "")
        live_in_queue = (
            queues.get("running_task_id") == task_id
            or task_id in set(queues.get("queued_task_ids") or [])
        )
        terminal_stage = stage in {"review", "done", "failed", "paused"}
        if live_in_queue or (queue_state in {"queued", "running"} and not terminal_stage):
            raise ValueError("TASK_BUSY")

        if terminal_stage and queue_state in {"queued", "running"}:
            current["queue_status"] = {
                "status": "completed" if stage in {"review", "done"} else stage,
                "finished_at": queue.get("finished_at") or round(time.time(), 1),
            }

        for history_item in reversed(current.get("run_history") or []):
            if history_item.get("mode") == "incremental" and history_item.get("stage") == "created":
                raise ValueError("PREVIOUS_INCREMENT_PENDING")
        if stage == "created" and str(current.get("run_mode") or "") in {
            "incremental", "interaction_revision",
        }:
            raise ValueError("PREVIOUS_INCREMENT_PENDING")

        version = ensure_report_version(
            report_id, task_id=task_id, status="snapshot",
            change_summary="增量更新前自动生成基线快照", kind="minor", _session=tx,
        )
        base_version = get_report_version(version.version_id, _session=tx)
        if base_version is None:
            raise ValueError("BASE_VERSION_NOT_FOUND")

        old_material_ids = _snapshot_ids(base_version.get("material_fingerprints") or [])
        old_fact_ids = _snapshot_ids(base_version.get("fact_snapshot") or [])
        inference_rows = base_version.get("inference_snapshot") or []
        old_inference_ids = _snapshot_ids(inference_rows, exclude_external=True)
        old_external_ids = [
            int(item["id"]) for item in inference_rows
            if str(item.get("id") or "").isdigit()
            and str(item.get("source_level") or "") == "EXTERNAL_INFORMATION"
        ]
        old_conflict_ids = _snapshot_ids(base_version.get("conflict_snapshot") or [])
        previous_revision = max(1, int(current.get("run_revision") or 1))
        revision = previous_revision + 1
        run_history = list(current.get("run_history") or [])
        run_history.append({
            "revision": previous_revision,
            "mode": str(current.get("run_mode") or "initial"),
            "stage": stage,
            "report_version_id": base_version.get("id"),
            "material_count": len(old_material_ids),
            "fact_count": len(old_fact_ids),
            "inference_count": len(old_inference_ids) + len(old_external_ids),
            "finished_at": queue.get("finished_at"),
        })

        run_id = create_task_run(
            task_id, revision=revision, run_mode="incremental",
            base_version_id=int(base_version.get("id") or 0) or None,
            update_reason=update_reason, _session=tx,
        )
        delta = create_incremental_delta(
            report_id, submitted_material_ids, update_reason=update_reason,
            run_id=run_id, _session=tx,
        )
        added_material_ids = [
            int(item.get("material_id"))
            for item in delta.get("added_materials", [])
            if str(item.get("material_id") or "").isdigit()
        ]
        material_ids = list(dict.fromkeys(old_material_ids + added_material_ids))
        plan_snapshot = base_version.get("report_plan_snapshot") or {}
        next_payload = dict(current)
        next_payload.update({
            "theme": current.get("theme") or plan_snapshot.get("title") or report.get("title"),
            "user_requirements": current.get("user_requirements", ""),
            "variant_id": current.get("variant_id") or report.get("style_profile_id"),
            "material_ids": material_ids,
            "stage": "created",
            "report_id": report_id,
            "plan_id": report.get("plan_id"),
            "plan_title": plan_snapshot.get("title") or report.get("title"),
            "fact_ids": old_fact_ids,
            "inference_ids": old_inference_ids,
            "external_ids": old_external_ids,
            "conflict_ids": old_conflict_ids,
            "run_revision": revision,
            "run_id": run_id,
            "run_mode": "incremental",
            "run_history": run_history[-50:],
            "revision_started_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "incremental_update": True,
            "incremental_base_task_id": task_id,
            "incremental_base_version_id": base_version.get("id"),
            "incremental_delta_id": delta.get("id"),
            "incremental_added_material_ids": added_material_ids,
            "incremental_update_reason": update_reason,
            "incremental_inherited_fact_ids": old_fact_ids,
            "incremental_inherited_inference_ids": old_inference_ids,
            "incremental_inherited_external_ids": old_external_ids,
            "incremental_inherited_conflict_ids": old_conflict_ids,
            "incremental_new_fact_ids": [],
            "incremental_generated_inference_ids": [],
            "incremental_generated_external_ids": [],
            "incremental_plan": {},
            "incremental_delta": {},
            "incremental_structure_review_required": False,
            "incremental_source_comparison_id": source_comparison_id,
            "incremental_selected_change_ids": (comparison_handoff or {}).get("accepted_item_ids", []),
            "analysis_done": False,
            "final_plan_frozen": bool(plan_snapshot.get("structure")),
            "parse_progress": {},
            "material_analysis_progress": {},
            "evidence_progress": {},
            "write_progress": {},
            "parse_errors": [],
            "embed_errors": [],
            "qa_notes": [],
            "stage_timings": {},
            "stage_durations": {},
            "llm_stats": {},
            "token_efficiency": {},
            "workload_profile": {},
            "resource_samples": [],
            "background_jobs": {},
            "artifact_status": {},
            "queue_status": {"status": "created"},
            "control_request": "",
            "error": "",
            "critical_path_done": False,
        })
        result.update({
            "task_id": task_id,
            "report_id": report_id,
            "revision": revision,
            "base_version_id": base_version.get("id"),
            "delta_id": delta.get("id"),
            "material_count": len(material_ids),
            "added_material_count": len(added_material_ids),
            "status": "created",
            "source_comparison_id": source_comparison_id,
        })
        return next_payload

    with session_scope() as tx:
        require_idle_task(tx, task_id)
        short_term.transition_task(task_id, transition, _session=tx)
    return result
