"""REST API:任务、材料、报告、模板。"""
import hashlib
import json
import os
import shutil
import subprocess
import uuid
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import session_scope
from app.export import export_report
from app.infrastructure.orm import (
    Base,
    ORMEvidence,
    ORMFact,
    ORMInference,
    ORMInsight,
    ORMMaterial,
    ORMPlan,
    ORMReport,
    ORMReportVersion,
    ORMSentence,
    ORMShortMemory,
    ORMTaskArtifact,
    ORMUnit,
)
from app.llm_queue import llm_queue_stats
from app.llm_scheduler import invoke, model_lane_stats
from app.memory import short_term, style
from app.memory.style_jobs import create_job as create_style_job
from app.memory.style_jobs import get_job as get_style_job
from app.memory.style_jobs import run_job as run_style_job
from app.interaction import create_thread as create_interaction_thread
from app.interaction import close_thread as close_interaction_thread
from app.interaction import decide_proposal, get_thread as get_interaction_thread
from app.interaction import list_notifications as list_interaction_notifications
from app.interaction import list_threads as list_interaction_threads
from app.interaction import mark_notification_read, review_workspace
from app.interaction import post_message as post_interaction_message
from app.interaction import queue_message as queue_interaction_message
from app.interaction import attach_draft_thread
from app.material_comparison import (
    accepted_update_handoff,
    create_comparison_run,
    get_comparison,
    get_comparison_item,
    list_comparisons,
    update_comparison_item,
)
from app.parsing import parse_file
from app.rendering.headings import detect_numbering_strategy, format_heading, strip_heading_prefix
from app.report_versions import (
    create_incremental_delta,
    diff_report_version_to_current,
    diff_report_version_sentences,
    ensure_report_version,
    get_report_delta,
    get_report_version,
    restore_report_version,
    restore_report_version_scope,
    save_report_change_decision,
    list_report_change_decisions,
    apply_report_change_decisions,
    list_report_versions,
)
from app.task_runs import create_task_run
from app.template_engine import compile_template
from app.template_engine.compiler import COMPILER_VERSION
from app.token_monitor import build_token_efficiency, build_workload_profile, list_llm_calls
from app.workflow import WorkflowController
from app.workflow.queue import enqueue_task, request_control, task_queue_status

router = APIRouter(prefix="/api")


def _ui_timestamp(value) -> str:
    """Normalize mixed historical timestamps for browser display and sorting."""
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.astimezone().isoformat(timespec="seconds")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    return datetime.fromtimestamp(numeric).astimezone().isoformat(timespec="seconds")


# ---------- 任务 ----------

@router.post("/tasks")
def create_task(
    theme: str = Form(""),
    requirements: str = Form(""),
    variant_id: int | None = Form(None),
    existing_material_ids: str = Form(""),
    interaction_draft_id: str = Form(""),
    workflow_mode: str = Form("automatic"),
    requirement_review: str = Form("auto"),
    directory_review: str = Form("auto"),
    files: list[UploadFile] | None = File(default=None),
):
    """创建任务:上传材料 + 指定主题 + 可选模板(不选则用全局默认模板)。"""
    workflow_mode = workflow_mode if workflow_mode in {"automatic", "collaborative"} else "automatic"
    requirement_review = requirement_review if requirement_review in {"auto", "required"} else "auto"
    directory_review = directory_review if directory_review in {"auto", "required"} else "auto"
    if workflow_mode == "automatic" and not theme.strip():
        return JSONResponse({"error": "THEME_REQUIRED"}, status_code=400)
    task_id = uuid.uuid4().hex[:12]
    try:
        material_ids = _collect_material_ids(task_id, existing_material_ids, files)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    material_ids = list(dict.fromkeys(material_ids))
    if not material_ids:
        return JSONResponse({"error": "NO_MATERIALS"}, status_code=400)
    run_id = create_task_run(task_id, revision=1, run_mode="initial")
    short_term.save_task(task_id, {
        "theme": theme,
        "user_requirements": requirements,
        "variant_id": variant_id,
        "material_ids": material_ids,
        "stage": "created",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "run_revision": 1,
        "run_id": run_id,
        "run_mode": "initial",
        "run_history": [],
        "workflow_mode": workflow_mode,
        "requirement_review": requirement_review,
        "directory_review": directory_review,
        # The requirement checkpoint becomes available only after material
        # understanding; it must not occupy the queue before that artifact exists.
        "requirement_review_pending": False,
        "requirement_review_completed": workflow_mode == "automatic" or requirement_review == "auto",
        "directory_review_pending": False,
        "directory_review_completed": directory_review == "auto" or workflow_mode == "automatic",
    })
    attach_draft_thread(interaction_draft_id, task_id)
    queue = enqueue_task(task_id) if workflow_mode == "collaborative" else {}
    return {"task_id": task_id, "material_count": len(material_ids), "queue": queue}


@router.post("/documents/parse")
def create_document_analysis(
    files: list[UploadFile] | None = File(default=None),
):
    """Parse uploaded documents and stop after reusable material understanding."""
    task_id = uuid.uuid4().hex[:12]
    try:
        material_ids = _collect_material_ids(task_id, files=files)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if not material_ids:
        return JSONResponse({"error": "NO_MATERIALS"}, status_code=400)
    with session_scope() as s:
        names = [str(row["filename"]) for row in s.execute(
            select(ORMMaterial.c.filename).where(ORMMaterial.c.id.in_(material_ids))
        ).mappings().all()]
    run_id = create_task_run(task_id, revision=1, run_mode="document_analysis")
    short_term.save_task(task_id, {
        "theme": f"文档解析：{'、'.join(names[:2])}{'等' if len(names) > 2 else ''}",
        "user_requirements": "解析所选材料，形成可追溯的摘要、提纲与关键信息，供后续理解和复用。",
        "material_ids": material_ids,
        "stage": "created",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "run_revision": 1,
        "run_id": run_id,
        "run_mode": "document_analysis",
        "workflow_mode": "automatic",
        "requirement_review_completed": True,
        "directory_review_completed": True,
    })
    return {"task_id": task_id, "material_count": len(material_ids), "queue": enqueue_task(task_id)}


@router.get("/documents")
def list_document_analyses():
    """List dedicated document-understanding runs without mixing them into report tasks."""
    with session_scope() as s:
        rows = s.execute(select(ORMShortMemory.c.task_id, ORMShortMemory.c.payload)).mappings().all()
    items = []
    for row in rows:
        payload = json.loads(row["payload"])
        if payload.get("run_mode") != "document_analysis":
            continue
        analysis = payload.get("document_analysis") or {}
        items.append({
            "task_id": row["task_id"],
            "theme": payload.get("theme", "文档解析"),
            "stage": payload.get("stage", "created"),
            "created_at": payload.get("created_at", ""),
            "updated_at": _ui_timestamp(payload.get("updated_at") or payload.get("last_progress_at") or payload.get("created_at", "")),
            "material_ids": payload.get("material_ids") or [],
            "material_count": len(payload.get("material_ids") or []),
            "summary": analysis.get("summary", ""),
        })
    return sorted(items, key=lambda item: item["updated_at"], reverse=True)


@router.get("/documents/{task_id}")
def get_document_analysis(task_id: str):
    task = short_term.load_task(task_id)
    if task is None or task.get("run_mode") != "document_analysis":
        return JSONResponse({"error": "DOCUMENT_ANALYSIS_NOT_FOUND"}, status_code=404)
    return {
        "task_id": task_id,
        "theme": task.get("theme", "文档解析"),
        "stage": task.get("stage", "created"),
        "material_ids": task.get("material_ids") or [],
        "parse_progress": task.get("parse_progress") or {},
        "material_analysis_progress": task.get("material_analysis_progress") or {},
        "material_analysis_status": task.get("material_analysis_status") or {},
        "queue_status": task.get("queue_status") or {},
        "analysis": task.get("document_analysis") or {},
        "error": task.get("error") or task.get("failure_reason") or "",
    }


@router.post("/reports/{report_id}/incremental/tasks")
def create_incremental_task(
    report_id: int,
    update_reason: str = Form(""),
    existing_material_ids: str = Form(""),
    source_comparison_id: int | None = Form(None),
    files: list[UploadFile] | None = File(default=None),
):
    """在原任务上创建一个增量运行轮次,不创建新的业务任务。"""
    task_id = _find_task_by_report(report_id)
    if task_id is None:
        return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
    base_task = short_term.load_task(task_id) or {}
    queues = task_queue_status()
    queue_state = str((base_task.get("queue_status") or {}).get("status") or "")
    terminal_stage = str(base_task.get("stage") or "") in {"review", "done", "failed", "paused"}
    live_in_queue = (
        queues.get("running_task_id") == task_id
        or task_id in set(queues.get("queued_task_ids") or [])
    )
    # 阶段终态优先于遗留的 queue_status。历史版本曾在 review 后留下
    # queue_status=running，不能因此阻止增量轮次创建；只有实际仍在队列
    # 中的任务才视为忙。
    if live_in_queue or (queue_state in {"queued", "running"} and not terminal_stage):
        return JSONResponse({"error": "TASK_BUSY", "task_id": task_id}, status_code=409)
    if terminal_stage and queue_state in {"queued", "running"}:
        base_task = short_term.update_task(
            task_id,
            queue_status={
                "status": "completed" if str(base_task.get("stage")) in {"review", "done"} else str(base_task.get("stage")),
                "finished_at": (base_task.get("queue_status") or {}).get("finished_at") or round(time.time(), 1),
            },
        )
    with session_scope() as s:
        report = s.execute(select(ORMReport).where(ORMReport.c.id == report_id)).mappings().first()
        if report is None:
            return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
    for h in reversed(base_task.get("run_history") or []):
        if h.get("mode") == "incremental" and h.get("stage") == "created":
            return JSONResponse({"error": "PREVIOUS_INCREMENT_PENDING",
                                 "message": "上一增量轮次尚未运行"}, status_code=409)
    # Always snapshot the current working tree. This preserves edits made after
    # the last version before an incremental run starts.
    version = ensure_report_version(
        report_id, task_id=task_id, status="snapshot",
        change_summary="增量更新前自动生成基线快照", kind="minor",
    )
    base_version = get_report_version(version.version_id)
    try:
        submitted_material_ids = _collect_material_ids(task_id, existing_material_ids, files)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    # 允许"无新增材料"的增量更新(补写模式):仅通过 update_reason 触发全章节重写。
    # 此时 material_ids = 旧材料全集, delta 不含新材料,
    # 影响范围由 _prepare_incremental_write_scope 判定(无章节映射 → no_mapped_section_rewrite_all → 全章节重写)。
    old_material_ids = [
        int(item.get("material_id"))
        for item in (base_version or {}).get("material_fingerprints", [])
        if str(item.get("material_id", "")).isdigit()
    ]
    old_fact_ids = [
        int(item.get("id"))
        for item in (base_version or {}).get("fact_snapshot", [])
        if str(item.get("id", "")).isdigit()
    ]
    old_inference_ids = [
        int(item.get("id"))
        for item in (base_version or {}).get("inference_snapshot", [])
        if str(item.get("id", "")).isdigit()
        and str(item.get("source_level") or "") != "EXTERNAL_INFORMATION"
    ]
    old_external_ids = [
        int(item.get("id"))
        for item in (base_version or {}).get("inference_snapshot", [])
        if str(item.get("id", "")).isdigit()
        and str(item.get("source_level") or "") == "EXTERNAL_INFORMATION"
    ]
    old_conflict_ids = [
        int(item.get("id"))
        for item in (base_version or {}).get("conflict_snapshot", [])
        if str(item.get("id", "")).isdigit()
    ]
    if not submitted_material_ids and not update_reason.strip():
        return JSONResponse({"error": "UPDATE_REASON_REQUIRED"}, status_code=400)
    comparison_handoff = None
    if source_comparison_id is not None:
        try:
            comparison_handoff = accepted_update_handoff(int(source_comparison_id))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if int(comparison_handoff["report_id"]) != int(report_id):
            return JSONResponse({"error": "COMPARISON_REPORT_MISMATCH"}, status_code=409)
    previous_revision = max(1, int(base_task.get("run_revision") or 1))
    revision = previous_revision + 1
    run_id = create_task_run(
        task_id, revision=revision, run_mode="incremental",
        base_version_id=int((base_version or {}).get("id") or 0) or None,
        update_reason=update_reason,
    )
    delta = create_incremental_delta(
        report_id, submitted_material_ids, update_reason=update_reason, run_id=run_id,
    )
    added_material_ids = [
        int(item.get("material_id"))
        for item in delta.get("added_materials", [])
        if str(item.get("material_id", "")).isdigit()
    ]
    material_ids = list(dict.fromkeys(old_material_ids + added_material_ids))
    plan_snapshot = (base_version or {}).get("report_plan_snapshot") or {}
    run_history = list(base_task.get("run_history") or [])
    run_history.append({
        "revision": previous_revision,
        "mode": str(base_task.get("run_mode") or "initial"),
        "stage": str(base_task.get("stage") or ""),
        "report_version_id": (base_version or {}).get("id"),
        "material_count": len(old_material_ids),
        "fact_count": len(old_fact_ids),
        "inference_count": len(old_inference_ids) + len(old_external_ids),
        "finished_at": (base_task.get("queue_status") or {}).get("finished_at"),
    })
    next_payload = dict(base_task)
    next_payload.update({
        "theme": base_task.get("theme") or plan_snapshot.get("title") or report["title"],
        "user_requirements": base_task.get("user_requirements", ""),
        "variant_id": base_task.get("variant_id") or report["style_profile_id"],
        "material_ids": material_ids,
        "stage": "created",
        "report_id": report_id,
        "plan_id": report["plan_id"],
        "plan_title": plan_snapshot.get("title") or report["title"],
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
        "incremental_base_version_id": (base_version or {}).get("id"),
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
        # 增量默认沿用已冻结目录;受影响范围由 Delta 决定,避免重写旧 Plan。
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
    short_term.save_task(task_id, next_payload)
    return {
        "task_id": task_id,
        "report_id": report_id,
        "revision": revision,
        "base_version_id": (base_version or {}).get("id"),
        "delta_id": delta.get("id"),
        "material_count": len(material_ids),
        "added_material_count": len(added_material_ids),
        "status": "created",
        "source_comparison_id": source_comparison_id,
    }


@router.post("/reports/{report_id}/material-comparisons")
def create_material_comparison(
    report_id: int,
    focus: str = Form(""),
    base_version_id: int | None = Form(None),
    existing_material_ids: str = Form(""),
    files: list[UploadFile] | None = File(default=None),
):
    """Create an independent, read-only new-material comparison task."""
    root_task_id = _find_task_by_report(report_id)
    if root_task_id is None:
        return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
    base_task = short_term.load_task(root_task_id) or {}
    if base_version_id is not None:
        version = get_report_version(int(base_version_id))
        if version is None or int(version["report_id"]) != int(report_id):
            return JSONResponse({"error": "REPORT_VERSION_NOT_FOUND"}, status_code=404)
    else:
        snapshot = ensure_report_version(
            report_id, task_id=root_task_id, status="snapshot",
            change_summary="新增材料对比基线", kind="minor",
        )
        version = get_report_version(snapshot.version_id)
    comparison_task_id = f"cmp-{uuid.uuid4().hex[:12]}"
    try:
        material_ids = _collect_material_ids(comparison_task_id, existing_material_ids, files)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if not material_ids:
        return JSONResponse({"error": "NO_NEW_MATERIALS"}, status_code=400)
    run_id = create_task_run(
        comparison_task_id, revision=1, run_mode="material_comparison",
        base_version_id=int(version["id"]), update_reason=focus,
    )
    comparison = create_comparison_run(
        comparison_task_id, report_id, int(version["id"]), material_ids, focus=focus,
    )
    short_term.save_task(comparison_task_id, {
        "theme": f"新增材料对比：{version.get('title') or base_task.get('theme') or '报告'}",
        "user_requirements": (
            "只分析新增材料相对于基线报告带来的新增、补强、细化、更新、冲突、削弱与无关信息；"
            "不得修改基线报告。" + (f"\n用户关注：{focus}" if focus.strip() else "")
        ),
        "variant_id": version.get("template_id") or base_task.get("variant_id"),
        "material_ids": material_ids,
        "stage": "created",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "run_revision": 1,
        "run_id": run_id,
        "run_mode": "material_comparison",
        "comparison_id": comparison["id"],
        "comparison_report_id": report_id,
        "comparison_base_version_id": int(version["id"]),
        "comparison_base_task_id": root_task_id,
        "queue_status": {"status": "created"},
    })
    queue = enqueue_task(comparison_task_id)
    return {
        "comparison_id": comparison["id"], "task_id": comparison_task_id,
        "report_id": report_id, "base_version_id": int(version["id"]),
        "material_count": len(material_ids), "queue": queue,
    }


@router.get("/reports/{report_id}/material-comparisons")
def report_material_comparisons(report_id: int):
    return list_comparisons(report_id)


@router.get("/material-comparisons/{comparison_id}")
def material_comparison_detail(comparison_id: int, compact: bool = False):
    result = get_comparison(comparison_id, compact=compact)
    if result is None:
        return JSONResponse({"error": "COMPARISON_NOT_FOUND"}, status_code=404)
    return result


@router.get("/material-comparisons/{comparison_id}/items/{item_id}")
def material_comparison_item_detail(comparison_id: int, item_id: int):
    result = get_comparison_item(comparison_id, item_id)
    if result is None:
        return JSONResponse({"error": "COMPARISON_ITEM_NOT_FOUND"}, status_code=404)
    return result


@router.patch("/material-comparisons/{comparison_id}/items/{item_id}")
def review_material_comparison_item(comparison_id: int, item_id: int, payload: dict):
    try:
        result = update_comparison_item(
            comparison_id, item_id, status=str(payload.get("status") or "pending_review"),
            user_note=str(payload.get("user_note") or ""), change_type=payload.get("change_type"),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if result is None:
        return JSONResponse({"error": "COMPARISON_ITEM_NOT_FOUND"}, status_code=404)
    return result


@router.post("/material-comparisons/{comparison_id}/update-handoff")
def material_comparison_update_handoff(comparison_id: int):
    """Prepare selected changes for the existing incremental-update flow."""
    try:
        return accepted_update_handoff(comparison_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


def _collect_material_ids(task_id: str, existing_material_ids: str = "",
                          files: list[UploadFile] | None = None) -> list[int]:
    """Create/reuse Material rows from uploaded files and existing IDs."""
    settings.ensure_dirs()
    material_ids: list[int] = []
    for item in (existing_material_ids or "").split(","):
        item = item.strip()
        if item.isdigit():
            material_ids.append(int(item))
    for upload in files or []:
        if not upload.filename:
            continue
        content = upload.file.read()
        file_hash = hashlib.sha256(content).hexdigest()
        with session_scope() as s:
            existing = s.execute(
                select(ORMMaterial.c.id).where(ORMMaterial.c.file_hash == file_hash)
            ).mappings().first()
            if existing is not None:
                material_ids.append(existing["id"])
                continue
            filename = Path(upload.filename).name
            dest = settings.materials_dir / f"{task_id}_{filename}"
            with dest.open("wb") as fh:
                fh.write(content)
            try:
                result = s.execute(
                    insert(ORMMaterial).values(
                        filename=filename,
                        file_type=dest.suffix.lower().lstrip("."),
                        path=str(dest),
                        fingerprint=file_hash,
                        file_hash=file_hash,
                    )
                )
                material_ids.append(result.inserted_primary_key[0])
            except IntegrityError:
                s.rollback()
                existing = s.execute(
                    select(ORMMaterial.c.id).where(ORMMaterial.c.file_hash == file_hash)
                ).mappings().first()
                dest.unlink(missing_ok=True)
                if existing is None:
                    raise ValueError(f"MATERIAL_SAVE_FAILED:{filename}")
                material_ids.append(existing["id"])
    return list(dict.fromkeys(material_ids))


@router.get("/tasks")
def list_tasks():
    """历史任务列表(按创建时间倒序),含主题/状态/报告链接。"""
    with session_scope() as s:
        rows = s.execute(
            select(ORMShortMemory.c.task_id, ORMShortMemory.c.payload)
        ).mappings().all()
    tasks = []
    for row in rows:
        payload = _task_view(json.loads(row["payload"]))
        if payload.get("run_mode") in {"document_analysis", "material_discussion"}:
            continue
        tasks.append({
            "task_id": row["task_id"],
            "theme": payload.get("theme", ""),
            "stage": payload.get("stage", ""),
            "created_at": payload.get("created_at", ""),
            "updated_at": _ui_timestamp(payload.get("updated_at") or payload.get("last_progress_at") or payload.get("created_at", "")),
            "report_id": payload.get("report_id"),
            "material_count": len(payload.get("material_ids", []) or []),
            "variant_id": payload.get("variant_id"),
            "run_revision": int(payload.get("run_revision") or 1),
            "run_mode": payload.get("run_mode") or "initial",
            "incremental_update": bool(payload.get("incremental_update")),
            "incremental_added_material_count": len(payload.get("incremental_added_material_ids") or []),
            "update_reason": payload.get("incremental_delta", {}).get("update_reason", "") if isinstance(payload.get("incremental_delta"), dict) else "",
            # Lists only need enough state to classify a task. Detailed timing,
            # token and resource samples remain available from the task detail API.
            "progress": {
                "stage": payload.get("stage", ""),
                "queue_status": payload.get("queue_status") or {},
            },
        })
    return tasks


@router.post("/tasks/{task_id}/run")
def run_task(task_id: str):
    """将任务加入队列,由单 worker 跑完整流程并停在评审阶段。"""
    if short_term.load_task(task_id) is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    result = enqueue_task(task_id)
    if result.get("status") == "not_found":
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    return result


@router.post("/tasks/{task_id}/control/{action}")
def control_task(task_id: str, action: str):
    """Request cooperative pause/resume at a safe workflow boundary."""
    result = request_control(task_id, action)
    if result.get("status") == "not_found":
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    if result.get("status") in {"not_running", "not_paused", "unsupported", "already_running"}:
        return JSONResponse(result, status_code=409)
    return result


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    task = short_term.load_task(task_id)
    if task is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    view = _task_view(task)
    comparison = view.get("material_comparison") or {}
    return {
        "task_id": task_id,
        "theme": view.get("theme", ""),
        "user_requirements": view.get("user_requirements", ""),
        "stage": view.get("stage", "created"),
        "created_at": view.get("created_at", ""),
        "updated_at": _ui_timestamp(view.get("updated_at") or view.get("last_progress_at") or view.get("created_at", "")),
        "report_id": view.get("report_id"),
        "material_ids": view.get("material_ids") or [],
        "material_count": len(view.get("material_ids") or []),
        "variant_id": view.get("variant_id"),
        "run_revision": int(view.get("run_revision") or 1),
        "run_mode": view.get("run_mode") or "initial",
        "workflow_mode": view.get("workflow_mode") or "automatic",
        "requirement_review": view.get("requirement_review") or "auto",
        "requirement_review_pending": bool(view.get("requirement_review_pending", view.get("planning_review_pending"))),
        "requirement_review_completed": bool(view.get("requirement_review_completed", not view.get("planning_review_pending"))),
        "directory_review": view.get("directory_review") or "auto",
        "directory_review_pending": bool(view.get("directory_review_pending")),
        "directory_review_completed": bool(view.get("directory_review_completed")),
        "queue_status": view.get("queue_status") or {},
        "parse_progress": view.get("parse_progress") or {},
        "evidence_progress": view.get("evidence_progress") or {},
        "write_progress": view.get("write_progress") or {},
        "graph_status": view.get("graph_status") or {},
        "error": view.get("error") or "",
        "failure_reason": view.get("failure_reason") or "",
        "comparison_id": view.get("comparison_id"),
        "comparison_report_id": view.get("comparison_report_id"),
        "material_comparison": {"summary": comparison.get("summary") or {}},
        "artifact_counts": {
            "facts": len(view.get("fact_ids") or []),
            "inferences": len(view.get("inference_ids") or []),
            "conflicts": len(view.get("conflict_ids") or []),
            "qa_issues": len(view.get("qa_notes") or []),
        },
    }


@router.post("/tasks/{task_id}/requirements/confirm")
def confirm_task_requirements(task_id: str, payload: dict):
    """确认材料理解后的任务需求，并继续 AnalysisPlan。"""
    from app.workflow.checkpoints import CheckpointError, confirm_requirements
    try:
        return confirm_requirements(
            task_id,
            theme=str(payload.get("theme") or ""),
            requirements=str(payload.get("requirements") or ""),
            feedback=str(payload.get("feedback") or ""),
        )
    except CheckpointError as exc:
        return _checkpoint_error_response(str(exc))


@router.post("/tasks/{task_id}/directory/confirm")
def confirm_task_directory(task_id: str, payload: dict):
    """确认最终目录，并继续同一任务的报告写作。"""
    from app.workflow.checkpoints import CheckpointError, confirm_directory
    try:
        return confirm_directory(
            task_id,
            feedback=str(payload.get("feedback") or ""),
            structure=payload.get("structure") if isinstance(payload.get("structure"), list) else None,
        )
    except CheckpointError as exc:
        return _checkpoint_error_response(str(exc))


def _checkpoint_error_response(code: str) -> JSONResponse:
    status = {
        "TASK_NOT_FOUND": 404,
        "REQUIREMENT_REVIEW_NOT_PENDING": 409,
        "DIRECTORY_REVIEW_NOT_PENDING": 409,
        "DIRECTORY_PLAN_NOT_FOUND": 409,
        "THEME_REQUIRED": 400,
        "REQUIREMENTS_REQUIRED": 400,
    }.get(code, 400)
    return JSONResponse({"error": code}, status_code=status)


@router.get("/tasks/{task_id}/assistant-context")
def task_assistant_context(task_id: str):
    task = short_term.load_task(task_id)
    if task is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    return _assistant_task_context(task_id, task)


@router.get("/reports/{report_id}/assistant-context")
def report_assistant_context(report_id: int):
    task_id = _find_task_by_report(report_id)
    if task_id is None:
        return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
    task = short_term.load_task(task_id) or {}
    result = _assistant_task_context(task_id, task)
    result["report_id"] = int(report_id)
    return result


def _assistant_task_context(task_id: str, task: dict) -> dict:
    """Small polling payload for the always-available collaboration assistant."""
    from app.control_agent import build_task_agent_context
    control_context = build_task_agent_context(task_id).model_dump(mode="json")
    return {
        "task_id": task_id,
        "report_id": task.get("report_id"),
        "theme": task.get("theme", ""),
        "user_requirements": task.get("user_requirements", ""),
        "stage": task.get("stage", "created"),
        "workflow_mode": task.get("workflow_mode") or "automatic",
        "requirement_review_pending": bool(task.get("requirement_review_pending", task.get("planning_review_pending"))),
        "directory_review_pending": bool(task.get("directory_review_pending")),
        "run_revision": task.get("run_revision", 1),
        "queue_status": task.get("queue_status") or {},
        "parse_progress": task.get("parse_progress") or {},
        "material_analysis_progress": task.get("material_analysis_progress") or {},
        "evidence_progress": task.get("evidence_progress") or {},
        "write_progress": task.get("write_progress") or {},
        "error": task.get("error") or task.get("failure_reason") or "",
        "updated_at": task.get("updated_at") or task.get("last_progress_at"),
        "artifact_counts": control_context["artifact_counts"],
        "available_artifacts": control_context["available_artifacts"],
    }


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    """删除任务记录。材料与已生成报告保留在资产库中。"""
    task = short_term.load_task(task_id)
    if task is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    queue_status = task.get("queue_status") or {}
    queues = task_queue_status()
    if (
        queue_status.get("status") in {"queued", "running"}
        or queues.get("running_task_id") == task_id
        or task_id in set(queues.get("queued_task_ids") or [])
    ):
        return JSONResponse({"error": "TASK_BUSY"}, status_code=409)
    short_term.delete_task(task_id)
    with session_scope() as s:
        s.execute(delete(ORMTaskArtifact).where(ORMTaskArtifact.c.task_id == task_id))
    return {"ok": True}


@router.get("/tasks/{task_id}/materials")
def task_materials(task_id: str):
    """任务材料解析状态:每份材料的内容单元数/页数/图片文本/解析异常。"""
    task = short_term.load_task(task_id)
    if task is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    parse_errors: dict[str, str] = {}
    embed_errors = {e["filename"]: e.get("error", "") for e in task.get("embed_errors", [])}
    for item in task.get("parse_errors", []):
        filename = item.get("filename")
        error = str(item.get("error", ""))
        if not filename:
            continue
        if error.lower().startswith("embed:"):
            embed_errors.setdefault(filename, error)
        else:
            parse_errors[filename] = error
    parse_profiles = {
        int(item.get("material_id")): item
        for item in task.get("parse_results", [])
        if item.get("material_id") is not None
    }
    material_ids = [int(i) for i in task.get("material_ids", [])]
    if not material_ids:
        return []
    with session_scope() as s:
        parsed_ids = {r["material_id"] for r in s.execute(
            select(ORMUnit.c.material_id).distinct()
        ).mappings().all()}
        materials = s.execute(
            select(ORMMaterial).where(ORMMaterial.c.id.in_(material_ids))
        ).mappings().all()
        parsed_count = sum(1 for m in materials if m["id"] in parsed_ids)
        result = []
        for material in materials:
            units = s.execute(
                select(
                    ORMUnit.c.kind, ORMUnit.c.content, ORMUnit.c.image_desc,
                    ORMUnit.c.page, ORMUnit.c.metadata_json,
                ).where(ORMUnit.c.material_id == material["id"])
            ).mappings().all()
            pages = {u["page"] for u in units if u["page"] is not None}
            images = [u for u in units if u["kind"] == "image"]
            parsed = len(units) > 0
            profile = parse_profiles.get(int(material["id"]), {})
            if parsed:
                # Units 已入库时材料正文可用。向量化或历史解析告警不应显示成
                # “材料解析错误”,否则用户会误以为 Docling 没有解析出内容。
                parse_status = "partial" if material["filename"] in embed_errors else "ok"
            elif material["filename"] in parse_errors:
                parse_status = "error"
            elif material["filename"] in embed_errors:
                parse_status = "vector_error"
            else:
                parse_status = "pending"  # 未解析(尚未轮到/中断),与材料库 unit_count 判断一致
            result.append({
                "filename": material["filename"],
                "file_type": material["file_type"],
                "units_count": len(units),
                "pages": sorted(pages),
                "is_duplicate": material["is_duplicate"],
                "parsed_count": parsed_count,
                "duplicate_of": material["duplicate_of"],
                "images": [{
                    "text": u["content"],
                    "ocr_text": u["content"],
                    "image_desc": u["image_desc"],
                } for u in images],
                "parse_status": parse_status,
                "parse_error": parse_errors.get(material["filename"], ""),
                "embed_error": embed_errors.get(material["filename"], ""),
                "parse_profile": profile,
            })
    return result


@router.get("/tasks/{task_id}/analysis")
def task_analysis(task_id: str):
    """任务分析结果:事实(带来源)/推断/外部信息/冲突。"""
    task = short_term.load_task(task_id)
    if task is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    fact_ids = [int(item) for item in task.get("fact_ids", [])]
    inference_ids = [int(item) for item in task.get("inference_ids", []) + task.get("external_ids", [])]
    with session_scope() as s:
        fact_rows = s.execute(
            select(ORMFact).where(ORMFact.c.id.in_(fact_ids))
        ).mappings().all() if fact_ids else []
        evidence_rows = s.execute(
            select(
                ORMEvidence.c.fact_id, ORMEvidence.c.source_file, ORMEvidence.c.page,
                ORMEvidence.c.paragraph, ORMEvidence.c.quote,
            ).where(ORMEvidence.c.fact_id.in_(fact_ids))
        ).mappings().all() if fact_ids else []
        evidence_by_fact: dict[int, list[dict]] = {}
        for item in evidence_rows:
            evidence_by_fact.setdefault(int(item["fact_id"]), []).append({
                "source_file": item["source_file"], "page": item["page"],
                "paragraph": item["paragraph"], "quote": item["quote"],
            })
        fact_by_id = {int(row["id"]): row for row in fact_rows}
        facts = [{
            "id": row["id"], "content": row["content"], "dimension": row["dimension"],
            "evidence": evidence_by_fact.get(fact_id, []),
        } for fact_id in fact_ids if (row := fact_by_id.get(fact_id)) is not None]
        inferences = []
        inference_rows = s.execute(
            select(ORMInference).where(ORMInference.c.id.in_(inference_ids))
        ).mappings().all() if inference_ids else []
        inference_by_id = {int(row["id"]): row for row in inference_rows}
        for inference_id in inference_ids:
            row = inference_by_id.get(inference_id)
            if row is None:
                continue
            inferences.append({
                "id": row["id"], "content": row["content"],
                "source_level": row["source_level"],
                "based_fact_ids": json.loads(row["based_fact_ids"]),
                "reasoning_chain": row["reasoning_chain"],
                "dimension": row["dimension"],
                "analysis_type": row["analysis_type"],
                "confidence_level": row["confidence_level"],
                "confidence_reason": row["confidence_reason"],
                "uncertainty": row["uncertainty"],
            })
        from app.evidence.extractor import load_conflict_records
        conflicts = load_conflict_records(task.get("conflict_ids", []))
    return {
        "facts": facts, "inferences": inferences, "conflicts": conflicts,
        "qa_notes": task.get("qa_notes") or [],
    }


@router.get("/tasks/{task_id}/graph")
def task_graph(task_id: str):
    """Evidence-grounded task graph for the Analysis workspace.

    The API intentionally exposes only this task's graph projection. Every
    edge carries Fact IDs, so the UI can return to the ordinary evidence panel.
    """
    task = short_term.load_task(task_id)
    if task is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    from app.graph import graph_service
    result = graph_service.task_graph(task_id)
    result["build_status"] = task.get("graph_status") or {"status": "unknown"}
    jobs = task.get("background_jobs") or {}
    background_job = jobs.get("graph_rebuild") or jobs.get("graph_build") or {}
    result["background_job"] = background_job
    result["build_active"] = _graph_job_active(background_job)
    return result


def _graph_job_active(job: dict) -> bool:
    if str((job or {}).get("status") or "") not in {"queued", "running"}:
        return False
    try:
        timestamp = float((job or {}).get("started_at") or (job or {}).get("queued_at") or 0)
    except (TypeError, ValueError):
        return False
    # A dead process must not leave graph recovery permanently locked.
    return bool(timestamp and time.time() - timestamp < max(1800, settings.gateway_timeout_seconds * 2))


def _run_graph_rebuild(task_id: str) -> None:
    """Backfill only the task graph; Evidence, Analysis and report stay intact."""
    from app.llm_queue import PRIORITY_BACKGROUND, llm_priority

    controller = WorkflowController(task_id)
    jobs = dict(controller.task.get("background_jobs") or {})
    jobs["graph_rebuild"] = {"status": "running", "started_at": round(time.time(), 1)}
    controller._update(graph_status={"status": "running"}, background_jobs=jobs)
    started = time.time()
    try:
        facts = controller._facts()
        with llm_priority(PRIORITY_BACKGROUND), controller._token_context("graph_build"):
            graph_status = controller._build_task_graph(facts)
        artifact_status = (
            "done" if graph_status.get("status") in {"ready", "reused"}
            else "partial" if graph_status.get("status") == "partial_ready"
            else "skipped" if graph_status.get("status") == "skipped"
            else "failed"
        )
        controller._record_artifact(
            "graph", {"graph_status": graph_status}, status=artifact_status,
        )
        jobs = dict(controller.task.get("background_jobs") or {})
        jobs["graph_rebuild"] = {
            "status": (
                "done" if graph_status.get("status") in {"ready", "reused"}
                else "partial" if graph_status.get("status") == "partial_ready"
                else "failed"
            ),
            "duration_seconds": round(time.time() - started, 1),
            "error": graph_status.get("error", ""),
        }
        controller._update(background_jobs=jobs)
    except Exception as exc:
        jobs = dict(controller.task.get("background_jobs") or {})
        jobs["graph_rebuild"] = {
            "status": "failed",
            "duration_seconds": round(time.time() - started, 1),
            "error": str(exc)[:300],
        }
        controller._update(
            graph_status={"status": "degraded", "error": str(exc)[:300]},
            background_jobs=jobs,
        )


@router.post("/tasks/{task_id}/graph/rebuild", status_code=202)
def rebuild_task_graph(task_id: str, background_tasks: BackgroundTasks):
    """Queue graph-only recovery for completed or degraded tasks."""
    task = short_term.load_task(task_id)
    if task is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    from app.graph import graph_service
    if graph_service.mode == "off":
        return JSONResponse({"error": "GRAPH_MODE_OFF"}, status_code=409)
    jobs = task.get("background_jobs") or {}
    active_job = jobs.get("graph_rebuild") or jobs.get("graph_build") or {}
    if _graph_job_active(active_job):
        return {"status": "already_running", "task_id": task_id}
    if not task.get("fact_ids"):
        return JSONResponse({"error": "TASK_HAS_NO_FACTS"}, status_code=409)
    jobs = dict(jobs)
    jobs["graph_rebuild"] = {"status": "queued", "queued_at": round(time.time(), 1)}
    short_term.update_task(task_id, graph_status={"status": "queued"}, background_jobs=jobs)
    background_tasks.add_task(_run_graph_rebuild, task_id)
    return {"status": "queued", "task_id": task_id, "fact_count": len(task.get("fact_ids") or [])}


@router.get("/tasks/{task_id}/graph/changesets")
def task_graph_changesets(task_id: str):
    if short_term.load_task(task_id) is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    from app.graph import graph_service
    return {"changesets": graph_service.changesets(task_id)}


@router.get("/tasks/{task_id}/workload-profile")
def task_workload_profile(task_id: str):
    """任务级工作负载画像:阶段Token分布、上下文长度、Prefill/Decode与算力需求。"""
    task = short_term.load_task(task_id)
    if task is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    return task.get("workload_profile") or build_workload_profile(task_id, run_id=str(task.get("run_id") or ""))


@router.get("/tasks/{task_id}/token-efficiency")
def task_token_efficiency(task_id: str):
    """任务级 Token 成本结构与中间产物利用率。"""
    task = short_term.load_task(task_id)
    if task is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    run_id = str(task.get("run_id") or "")
    summary = task.get("token_efficiency") or build_token_efficiency(task_id, task.get("report_id"), run_id=run_id)
    return {
        "summary": summary,
        "calls": list_llm_calls(task_id, run_id=run_id),
    }


# ---------- 材料 ----------

@router.get("/materials")
def list_materials():
    """材料库:材料列表 + 解析状态(单元数/重复标记)。"""
    material_tasks = _material_task_index()
    with session_scope() as s:
        rows = s.execute(
            select(
                ORMMaterial.c.id, ORMMaterial.c.filename, ORMMaterial.c.file_type,
                ORMMaterial.c.parsed_at,
                ORMMaterial.c.is_duplicate, ORMMaterial.c.duplicate_of,
                func.count(ORMUnit.c.id).label("unit_count"),
            )
            .select_from(
                ORMMaterial.outerjoin(ORMUnit, ORMUnit.c.material_id == ORMMaterial.c.id)
            )
            .group_by(
                ORMMaterial.c.id, ORMMaterial.c.filename, ORMMaterial.c.file_type,
                ORMMaterial.c.parsed_at,
                ORMMaterial.c.is_duplicate, ORMMaterial.c.duplicate_of,
            )
            .order_by(ORMMaterial.c.id.desc())
        ).mappings().all()
    return [{
        "id": r["id"], "filename": r["filename"], "file_type": r["file_type"],
        "unit_count": r["unit_count"], "is_duplicate": r["is_duplicate"],
        "duplicate_of": r["duplicate_of"],
        "parsed_at": r["parsed_at"],
        "parse_status": "ready" if r["unit_count"] else "pending",
        "tasks": material_tasks.get(r["id"], []),
    } for r in rows]



@router.get("/materials/{material_id}")
def get_material(material_id: int):
    """Material detail with parsed units and its reusable understanding result."""
    material_tasks = _material_task_index()
    with session_scope() as s:
        material = s.execute(
            select(ORMMaterial).where(ORMMaterial.c.id == material_id)
        ).mappings().first()
        if material is None:
            return JSONResponse({"error": "MATERIAL_NOT_FOUND"}, status_code=404)
        units = s.execute(
            select(ORMUnit).where(ORMUnit.c.material_id == material_id).order_by(ORMUnit.c.id)
        ).mappings().all()
        insight = s.execute(
            select(ORMInsight).where(ORMInsight.c.material_id == material_id)
            .order_by(ORMInsight.c.id.desc())
        ).mappings().first()
    return {
        "id": material["id"], "filename": material["filename"], "file_type": material["file_type"],
        "parsed_at": material["parsed_at"],
        "parse_status": "ready" if units else "pending",
        "is_duplicate": material["is_duplicate"], "duplicate_of": material["duplicate_of"],
        "tasks": material_tasks.get(material["id"], []),
        "insight": {
            "doc_type": insight["doc_type"] if insight else "",
            "topic": insight["topic"] if insight else "",
            "key_points": json.loads(insight["key_points"] or "[]") if insight else [],
            "key_sections": json.loads(insight["key_sections"] or "[]") if insight else [],
            "entities": json.loads(insight["entities"] or "[]") if insight else [],
            "times": json.loads(insight["times"] or "[]") if insight else [],
            "material_role": insight["material_role"] if insight else "",
            "missing_information": json.loads(insight["missing_information"] or "[]") if insight else [],
        },
        "units": [{
            "id": u["id"], "kind": u["kind"], "content": u["content"],
            "page": u["page"], "image_desc": u["image_desc"],
            "metadata": json.loads(u["metadata_json"] or "{}"),
        } for u in units],
    }


@router.post("/materials/{material_id}/assistant-session")
def create_material_assistant_session(material_id: int):
    """Create a material-scoped assistant context without entering report generation."""
    with session_scope() as s:
        material = s.execute(select(ORMMaterial).where(ORMMaterial.c.id == material_id)).mappings().first()
    if material is None:
        return JSONResponse({"error": "MATERIAL_NOT_FOUND"}, status_code=404)
    task_id = uuid.uuid4().hex[:12]
    short_term.save_task(task_id, {
        "theme": f"材料理解：{material['filename']}",
        "user_requirements": "解释所选材料的内容、结构、关键信息、可证明范围与缺失信息；不得引用未选材料。",
        "material_ids": [material_id],
        "stage": "review",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "run_revision": 1,
        "run_mode": "material_discussion",
        "workflow_mode": "automatic",
        "queue_status": {"status": "completed"},
        "requirement_review_completed": True,
        "directory_review_completed": True,
    })
    return {"task_id": task_id}


# ---------- 报告 ----------

@router.get("/reports")
def list_reports():
    """报告库列表(按生成时间倒序)。"""
    report_tasks = _report_task_index()
    with session_scope() as s:
        rows = s.execute(
            select(
                ORMReport.c.id, ORMReport.c.title, ORMReport.c.status, ORMReport.c.created_at,
                func.count(ORMSentence.c.id).label("sentence_count"),
            )
            .select_from(
                ORMReport.outerjoin(ORMSentence, ORMSentence.c.report_id == ORMReport.c.id)
            )
            .group_by(
                ORMReport.c.id, ORMReport.c.title, ORMReport.c.status, ORMReport.c.created_at,
            )
            .order_by(ORMReport.c.id.desc())
        ).mappings().all()
        report_ids = [int(row["id"]) for row in rows]
        version_rows = s.execute(
            select(ORMReportVersion).where(ORMReportVersion.c.report_id.in_(report_ids))
            .order_by(ORMReportVersion.c.report_id, ORMReportVersion.c.version_no.desc())
        ).mappings().all() if report_ids else []
    version_index = {}
    for version in version_rows:
        report_id = int(version["report_id"])
        if report_id in version_index:
            continue
        major = int(version.get("version_major") or version["version_no"])
        minor = int(version.get("version_minor") or 0)
        version_index[report_id] = {
            "id": version["id"], "version_no": version["version_no"],
            "version_label": str(major) if minor == 0 else f"{major}.{minor}",
            "status": version["status"], "created_at": version["created_at"],
        }
    return [{
        "id": r["id"], "title": r["title"], "status": r["status"],
        "created_at": r["created_at"], "sentence_count": r["sentence_count"],
        "task": report_tasks.get(r["id"]),
        "current_version": version_index.get(r["id"]),
    } for r in rows]


@router.get("/reports/{report_id}")
def get_report(report_id: int):
    """报告数据(章节→段落→句子,句子带来源分级与溯源细节)。"""
    with session_scope() as s:
        report = s.execute(
            select(ORMReport).where(ORMReport.c.id == report_id)
        ).mappings().first()
        if report is None:
            return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
        sentences = s.execute(
            select(ORMSentence).where(ORMSentence.c.report_id == report_id)
            .order_by(ORMSentence.c.position)
        ).mappings().all()
    task_id = _find_task_by_report(report["id"])
    task_payload = short_term.load_task(task_id) if task_id else {}
    details = _sentence_details_bulk(sentences)
    from app.quality import attach_quality_issue_locations
    qa_issues = attach_quality_issue_locations(
        report_id, list((task_payload or {}).get("qa_notes") or []), rows=sentences,
    )
    result = {
        "id": report["id"],
        "title": report["title"],
        "status": report["status"],
        "task_id": task_id,
        "versions": list_report_versions(report["id"]),
        "qa_issues": qa_issues,
        "sections": [],
    }
    document_shape = _report_document_shape(report["plan_id"])
    composition_mode = _report_composition_mode(report["plan_id"])
    heading_strategy = _report_heading_strategy(
        report["style_profile_id"], fallback_defaults=document_shape.get("heading_policy") == "numbered",
    )
    current_section = None
    current_paragraph = None
    chapter_index = 0
    subsection_index = 0
    for row in sentences:
        detail = details[int(row["id"])]
        if current_section is None or row["section"] != current_section:
            current_section = row["section"]
            current_paragraph = None
            chapter_index += 1
            subsection_index = 0
            section = {
                "title": row["section"],
                "display_title": format_heading(1, [chapter_index], row["section"], heading_strategy),
                "show_title": (
                    document_shape.get("section_policy") != "hidden"
                    and composition_mode != "article_beats"
                ),
                "paragraphs": [],
            }
            result["sections"].append(section)
        if row["paragraph"] != current_paragraph:
            current_paragraph = row["paragraph"]
            section["paragraphs"].append({"sentences": []})
        if row["source_level"] == "SUBHEADING":
            subsection_index += 1
            detail["show_subheading"] = document_shape.get("subheading_policy") != "hidden"
            if detail["show_subheading"]:
                detail["display_content"] = format_heading(
                    2, [chapter_index, subsection_index], detail["content"], heading_strategy,
                )
        section["paragraphs"][-1]["sentences"].append(detail)
    return result


@router.put("/reports/{report_id}")
def update_report_meta(report_id: int, payload: dict):
    """在线编辑报告元信息:当前只开放标题,同步到导出与 ReportPlan。"""
    title = str(payload.get("title", "")).strip()
    if not title:
        return JSONResponse({"error": "TITLE_REQUIRED"}, status_code=400)
    user_memory = Base.metadata.tables["user_memory"]
    with session_scope() as s:
        report = s.execute(
            select(ORMReport.c.plan_id).where(ORMReport.c.id == report_id)
        ).mappings().first()
        if report is None:
            return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
        s.execute(update(ORMReport).where(ORMReport.c.id == report_id).values(title=title))
        s.execute(update(ORMPlan).where(ORMPlan.c.id == report["plan_id"]).values(title=title))
        s.execute(
            insert(user_memory).values(
                note_type="edit", summary="用户修改了报告题目", content=title[:120]
            )
        )
    return {"ok": True, "title": title}


@router.put("/reports/{report_id}/sections")
def update_report_section(report_id: int, payload: dict):
    """在线编辑章节标题:同步句子 section 与 ReportPlan 结构。"""
    old_title = str(payload.get("old_title", "")).strip()
    new_title = strip_heading_prefix(str(payload.get("new_title", "")).strip())
    if not old_title or not new_title:
        return JSONResponse({"error": "SECTION_TITLE_REQUIRED"}, status_code=400)
    if old_title == new_title:
        return {
            "ok": True,
            "title": new_title,
            "display_title": _display_section_title(report_id, new_title, payload.get("section_index")),
        }
    from app.infrastructure.orm import ORMSentence, ORMPlan, ORMReport, Base
    from sqlalchemy import select, update
    with session_scope() as s:
        report = s.execute(
            select(ORMReport.c.plan_id, ORMReport.c.style_profile_id).where(ORMReport.c.id == report_id)
        ).first()
        if report is None:
            return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
        result = s.execute(
            update(ORMSentence).where(
                ORMSentence.c.report_id == report_id, ORMSentence.c.section == old_title
            ).values(section=new_title)
        )
        if result.rowcount == 0:
            return JSONResponse({"error": "SECTION_NOT_FOUND"}, status_code=404)
        plan = s.execute(
            select(ORMPlan.c.structure, ORMPlan.c.chapter_plans).where(ORMPlan.c.id == report[0])
        ).first()
        if plan is not None:
            structure = json.loads(plan[0] or "[]")
            chapter_plans = json.loads(plan[1] or "[]")
            structure = [new_title if str(item) == old_title else item for item in structure]
            for chapter in chapter_plans:
                if str(chapter.get("title", "")) == old_title:
                    chapter["title"] = new_title
            s.execute(
                update(ORMPlan).where(ORMPlan.c.id == report[0]).values(
                    structure=json.dumps(structure, ensure_ascii=False),
                    chapter_plans=json.dumps(chapter_plans, ensure_ascii=False),
                )
            )
        user_memory = Base.metadata.tables["user_memory"]
        s.execute(user_memory.insert().values(
            note_type="edit", summary="用户修改了章节标题",
            content=f"{old_title} → {new_title}",
        ))
    display_title = _display_section_title(report_id, new_title, payload.get("section_index"))
    return {"ok": True, "old_title": old_title, "title": new_title, "display_title": display_title}


def _display_section_title(report_id: int, title: str, section_index) -> str:
    try:
        index = int(section_index or 0)
    except (TypeError, ValueError):
        index = 0
    if index <= 0:
        return title
    with session_scope() as s:
        report = s.execute(select(ORMReport.c.style_profile_id).where(ORMReport.c.id == report_id)).first()
    style_profile_id = report[0] if report else None
    return format_heading(1, [index], title, _report_heading_strategy(style_profile_id))


@router.put("/reports/{report_id}/sentences/{sentence_id}")
def update_sentence(report_id: int, sentence_id: int, payload: dict):
    """在线编辑:勾选状态与用户修改文字(修改内容记入 edit_history + user_memory)。"""
    content = payload.get("content")
    from app.infrastructure.orm import ORMSentence, Base
    from sqlalchemy import select, update
    with session_scope() as s:
        row = s.execute(
            select(ORMSentence.c.content, ORMSentence.c.user_edit, ORMSentence.c.edit_history, ORMSentence.c.source_level)
            .where(ORMSentence.c.id == sentence_id, ORMSentence.c.report_id == report_id)
        ).first()
        if row is None:
            return JSONResponse({"error": "SENTENCE_NOT_FOUND"}, status_code=404)
        history = json.loads(row[2] or "[]")
        if content is not None and row[3] == "SUBHEADING":
            content = strip_heading_prefix(str(content))
        if content is not None and content != (row[1] or ""):
            history.append({"time": datetime.now().strftime("%m-%d %H:%M"), "editor": "user", "content": content})
            user_memory = Base.metadata.tables["user_memory"]
            s.execute(user_memory.insert().values(
                note_type="edit", summary="用户修改了报告句子",
                content=f"{str(row[0] or '')[:60]} → {content[:60]}",
            ))
        values = {
            "user_edit": content if content is not None else row[1],
            "edit_history": json.dumps(history, ensure_ascii=False),
        }
        if "selected" in payload:
            values["selected"] = 1 if payload.get("selected") else 0
        s.execute(
            update(ORMSentence).where(
                ORMSentence.c.id == sentence_id, ORMSentence.c.report_id == report_id
            ).values(**values)
        )
    return {"ok": True, "content": content if content is not None else row[1]}


@router.patch("/reports/{report_id}/quality-issues/{issue_id}")
def update_quality_issue(report_id: int, issue_id: str, payload: dict):
    """Record a review decision for one quality issue without mutating report text."""
    task_id = _find_task_by_report(report_id)
    if task_id is None:
        return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"open", "resolved", "ignored"}:
        return JSONResponse({"error": "INVALID_QA_STATUS"}, status_code=400)

    from app.quality import attach_quality_issue_locations
    task = short_term.load_task(task_id) or {}
    issues = attach_quality_issue_locations(
        report_id, list(task.get("qa_notes") or []),
    )
    target = next((item for item in issues if item.get("issue_id") == issue_id), None)
    if target is None:
        return JSONResponse({"error": "QA_ISSUE_NOT_FOUND"}, status_code=404)
    target["status"] = status
    target["reviewed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    note = str(payload.get("note") or "").strip()
    if note:
        target["review_note"] = note[:500]
    short_term.update_task(task_id, qa_notes=issues)
    return {"ok": True, "issue": target}


@router.post("/reports/{report_id}/finalize")
def finalize_report(report_id: int, payload: dict | None = None):
    """审核完成:报告置为 final,任务进入已完成。"""
    task_id = _find_task_by_report(report_id)
    if task_id is None:
        return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
    payload = payload or {}
    task = short_term.load_task(task_id) or {}
    blocking_types = {"MISSING_SECTION", "SCALE_UNDERFILL", "CITATION_MISMATCH"}
    blocking = [
        issue for issue in (task.get("qa_notes") or [])
        if issue.get("type") in blocking_types
        and str(issue.get("status") or "open") not in {"resolved", "ignored"}
    ]
    if blocking and not payload.get("force"):
        return JSONResponse({"error": "QA_BLOCKING", "issues": blocking}, status_code=409)
    WorkflowController(task_id).finalize()
    return {"ok": True}


@router.post("/reports/{report_id}/versions")
def create_report_version(report_id: int, payload: dict | None = None):
    """手动创建当前报告快照。用于阶段性沉淀或审核前留痕。

    人工保存 = 小版本(kind="minor"),挂在当前大版本下递增(如 2.0 → 2.1)。
    """
    payload = payload or {}
    try:
        version = ensure_report_version(
            report_id,
            task_id=_find_task_by_report(report_id) or "",
            status=str(payload.get("status") or "snapshot"),
            change_summary=str(payload.get("change_summary") or "手动生成版本快照"),
            kind="minor",
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return {"ok": True, "version_id": version.version_id, "version_no": version.version_no,
            "version_label": version.version_label}


@router.get("/reports/{report_id}/versions")
def report_versions(report_id: int):
    """报告版本链。"""
    return {"report_id": report_id, "versions": list_report_versions(report_id)}


@router.get("/report-versions/{version_id}/diff-current")
def report_version_diff_current(version_id: int, granularity: str = "section"):
    """类似 git diff:比较某个历史版本与当前候选稿。"""
    version = get_report_version(version_id)
    if version is None:
        return JSONResponse({"error": "REPORT_VERSION_NOT_FOUND"}, status_code=404)
    task_id = _find_task_by_report(int(version["report_id"])) or ""
    if granularity == "sentence":
        diff = diff_report_version_sentences(version_id, task_id=task_id)
    else:
        diff = diff_report_version_to_current(version_id, task_id=task_id)
    return diff or JSONResponse({"error": "REPORT_VERSION_NOT_FOUND"}, status_code=404)


@router.post("/report-versions/{version_id}/decisions")
def stage_report_version_decision(version_id: int, payload: dict):
    try:
        decision = save_report_change_decision(version_id, payload or {})
    except ValueError as exc:
        status = 409 if str(exc) == "CANDIDATE_CHANGED" else 400
        return JSONResponse({"error": str(exc)}, status_code=status)
    return {"ok": True, "decision": decision}


@router.get("/report-versions/{version_id}/decisions")
def report_version_decisions(version_id: int, candidate_hash: str = ""):
    return {"decisions": list_report_change_decisions(version_id, candidate_hash)}


@router.post("/report-versions/{version_id}/decisions/apply")
def apply_version_decisions(version_id: int, payload: dict):
    try:
        result = apply_report_change_decisions(version_id, str((payload or {}).get("candidate_hash") or ""))
    except ValueError as exc:
        status = 409 if str(exc) == "CANDIDATE_CHANGED" else 400
        return JSONResponse({"error": str(exc)}, status_code=status)
    return {"ok": True, **result}


@router.post("/report-versions/{version_id}/restore")
def restore_version(version_id: int):
    """把历史版本恢复为当前可编辑稿。"""
    restored = restore_report_version(version_id)
    if restored is None:
        return JSONResponse({"error": "REPORT_VERSION_NOT_FOUND"}, status_code=404)
    return {"ok": True, **restored}


@router.post("/report-versions/{version_id}/restore-scope")
def restore_version_scope(version_id: int, payload: dict):
    """从历史版本按 section/paragraph/sentence 选择性恢复。"""
    restored = restore_report_version_scope(version_id, payload or {})
    if restored is None:
        return JSONResponse({"error": "REPORT_VERSION_NOT_FOUND"}, status_code=404)
    return {"ok": True, **restored}


@router.get("/report-versions/{version_id}")
def report_version_detail(version_id: int):
    """不可变报告版本详情。"""
    version = get_report_version(version_id)
    if version is None:
        return JSONResponse({"error": "REPORT_VERSION_NOT_FOUND"}, status_code=404)
    return version


@router.post("/reports/{report_id}/incremental/preview")
def incremental_update_preview(report_id: int, payload: dict):
    """增量更新预检:只记录 Delta,不改报告正文、不重写章节。"""
    material_ids = payload.get("material_ids") or payload.get("added_material_ids") or []
    try:
        delta = create_incremental_delta(
            report_id,
            added_material_ids=material_ids,
            update_reason=str(payload.get("update_reason") or ""),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return delta


@router.get("/report-deltas/{delta_id}")
def report_delta_detail(delta_id: int):
    """报告增量差异详情。"""
    delta = get_report_delta(delta_id)
    if delta is None:
        return JSONResponse({"error": "REPORT_DELTA_NOT_FOUND"}, status_code=404)
    return delta


@router.get("/reports/{report_id}/export")
def export(report_id: int):
    """导出勾选内容为 Word(浏览器 location.href 直接下载,故用 GET)。"""
    try:
        path = export_report(report_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# ---------- 模板中心 ----------

@router.post("/style/analyze-jobs", status_code=202)
def create_style_learning_job(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    asset_role: str = Form("auto"),
):
    """Accept uploads quickly, then learn the template asynchronously."""
    if not files:
        return JSONResponse({"error": "NO_FILES"}, status_code=400)
    asset_role = str(asset_role or "auto").lower()
    if asset_role not in {"auto", "editorial", "structure", "layout"}:
        return JSONResponse({"error": "INVALID_ASSET_ROLE"}, status_code=400)
    job_id = uuid.uuid4().hex[:16]
    job_dir = settings.runtime_root / "style_jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    stored: list[dict] = []
    try:
        for index, upload in enumerate(files, start=1):
            filename = Path(upload.filename or f"template-{index}.docx").name
            target = job_dir / f"{index:03d}-{filename}"
            with target.open("wb") as handle:
                shutil.copyfileobj(upload.file, handle)
            stored.append({
                "filename": filename, "path": str(target), "size": target.stat().st_size,
                "asset_role": asset_role,
            })
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        return JSONResponse({"error": f"UPLOAD_SAVE_FAILED:{exc}"}, status_code=500)
    job = create_style_job(stored, job_id=job_id)
    background_tasks.add_task(run_style_job, job["id"])
    return job


@router.get("/style/analyze-jobs/{job_id}")
def style_learning_job(job_id: str):
    job = get_style_job(job_id)
    if job is None:
        return JSONResponse({"error": "STYLE_JOB_NOT_FOUND"}, status_code=404)
    return job

@router.post("/style/analyze")
def analyze_style(files: list[UploadFile] = File(...)):
    """上传模板或参考成品报告 → 提取文档格式与写作风格 → 生成可复核模板。

    每份 docx 报告的硬排版格式(字体/字号/行距/页边距)被真实提取,
    结构化 Schema 与原始 DOCX 双轨保存,供导出器稳定复用。
    """
    reports: list[dict] = []
    tmp_files: list[Path] = []
    settings.ensure_dirs()
    for upload in files:
        filename = Path(upload.filename).name
        tmp = settings.materials_dir / f"_style_{filename}"
        tmp_files.append(tmp)
        with tmp.open("wb") as fh:
            shutil.copyfileobj(upload.file, fh)
        try:
            units = parse_file(tmp)
        except Exception:
            continue
        text = "\n".join(u.content for u in units if u.content.strip())
        if text:
            reports.append({"filename": filename, "text": text, "path": str(tmp)})
    try:
        variants = style.analyze_library(reports) if reports else []
    finally:
        for tmp in tmp_files:
            tmp.unlink(missing_ok=True)
    if not variants:
        return JSONResponse({"error": "NO_PARSEABLE_FILES"}, status_code=400)
    return {"variants": [variant_fields(v) for v in variants]}


@router.get("/style/variants")
def list_style_variants():
    # The exemplar bank can contain hundreds of long paragraphs. Keep the
    # selector/list payload light and load the full profile only on inspection.
    return [variant_summary_fields(v) for v in style.list_variants()]


@router.get("/style/variants/{variant_id}/template-schema")
def get_template_schema(variant_id: int):
    variant = style.get_variant(variant_id)
    if variant is None:
        return JSONResponse({"error": "VARIANT_NOT_FOUND"}, status_code=404)
    dominant = variant.format_spec.get("dominant") if isinstance(variant.format_spec, dict) else {}
    schema = dominant.get("template_schema") if isinstance(dominant, dict) else {}
    if isinstance(dominant, dict) and schema.get("compiler_version") != COMPILER_VERSION:
        source = Path(str(dominant.get("source_template_path") or ""))
        if source.exists():
            schema = compile_template(source)
    if not schema:
        return JSONResponse({"error": "TEMPLATE_SCHEMA_NOT_FOUND"}, status_code=404)
    return schema


@router.patch("/style/variants/{variant_id}")
def update_style_variant(variant_id: int, payload: dict):
    """调整变体:改名/改描述(用户对聚类结果的确认步骤)。"""
    if style.get_variant(variant_id) is None:
        return JSONResponse({"error": "VARIANT_NOT_FOUND"}, status_code=404)
    style.update_variant(variant_id, name=payload.get("name"), description=payload.get("description"))
    return {"ok": True}


@router.delete("/style/variants/{variant_id}")
def delete_style_variant(variant_id: int):
    """从模板中心移除模板/写作风格。历史报告导出仍可读取旧格式数据。"""
    if not style.delete_variant(variant_id):
        return JSONResponse({"error": "VARIANT_NOT_FOUND"}, status_code=404)
    return {"ok": True}


@router.post("/style/variants/{variant_id}/confirm")
def confirm_style_variant(variant_id: int):
    style.confirm_variant(variant_id)
    return {"ok": True}


@router.post("/style/variants/{variant_id}/lock")
def lock_style_variant(variant_id: int):
    style.lock_variant(variant_id)
    return {"ok": True}


@router.get("/style/variants/{variant_id}/profile")
def get_style_profile(variant_id: int):
    variant = style.get_variant(variant_id)
    if variant is None:
        return JSONResponse({"error": "VARIANT_NOT_FOUND"}, status_code=404)
    return variant_fields(variant)


@router.patch("/style/variants/{variant_id}/profile")
def update_style_profile(variant_id: int, payload: dict):
    try:
        variant = style.update_profile(variant_id, payload)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return variant_fields(variant)


# ---------- 异步用户介入 ----------

@router.post("/interactions")
def start_interaction(payload: dict):
    try:
        return create_interaction_thread(
            task_id=str(payload.get("task_id") or ""),
            report_id=int(payload["report_id"]) if payload.get("report_id") is not None else None,
            artifact_type=str(payload.get("artifact_type") or ""),
            artifact_version=str(payload.get("artifact_version") or ""),
            object_id=str(payload.get("object_id") or ""),
            scope=payload.get("scope") if isinstance(payload.get("scope"), dict) else {},
        )
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/interactions")
def interactions(task_id: str = "", report_id: int | None = None, draft_id: str = ""):
    return list_interaction_threads(task_id=task_id, report_id=report_id, draft_id=draft_id)


@router.get("/tasks/{task_id}/review-workspace")
def task_review_workspace(task_id: str, artifact_type: str = "task_brief", q: str = "",
                          offset: int = 0, limit: int = 50):
    try:
        return review_workspace(task_id, artifact_type=artifact_type, query=q, offset=offset, limit=limit)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


@router.get("/interaction-notifications")
def interaction_notifications(task_id: str = "", report_id: int | None = None):
    return list_interaction_notifications(task_id=task_id, report_id=report_id)


@router.patch("/interaction-notifications/{notification_id}/read")
def read_interaction_notification(notification_id: int):
    if not mark_notification_read(notification_id):
        return JSONResponse({"error": "NOTIFICATION_NOT_FOUND"}, status_code=404)
    return {"ok": True}


@router.get("/interactions/{thread_id}")
def interaction_detail(thread_id: int):
    result = get_interaction_thread(thread_id)
    if result is None:
        return JSONResponse({"error": "INTERACTION_THREAD_NOT_FOUND"}, status_code=404)
    return result


@router.post("/interactions/{thread_id}/close")
def close_interaction(thread_id: int):
    try:
        return close_interaction_thread(thread_id)
    except ValueError as exc:
        status = 409 if str(exc) == "INTERACTION_THREAD_BUSY" else 404
        return JSONResponse({"error": str(exc)}, status_code=status)


@router.post("/interactions/{thread_id}/messages")
def send_interaction_message(thread_id: int, payload: dict):
    try:
        if bool(payload.get("async", True)):
            return queue_interaction_message(
                thread_id,
                str(payload.get("content") or ""),
                request_id=str(payload.get("request_id") or ""),
                context=payload.get("context") if isinstance(payload.get("context"), dict) else None,
                draft_current=payload.get("draft_current") if isinstance(payload.get("draft_current"), dict) else None,
            )
        return post_interaction_message(
            thread_id, str(payload.get("content") or ""),
            context=payload.get("context") if isinstance(payload.get("context"), dict) else None,
            draft_current=payload.get("draft_current") if isinstance(payload.get("draft_current"), dict) else None,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/change-proposals/{proposal_id}/decision")
def decide_change_proposal(proposal_id: int, payload: dict):
    try:
        return decide_proposal(proposal_id, str(payload.get("decision") or ""))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


# ---------- 系统 ----------

@router.get("/health")
def health():
    """网关连通与模型在位状态(系统设置页使用)。"""
    from app.gateway import model_gateway
    from app.runtime_profiles import runtime_profile_manifest
    from app.context_budget import tokenizer_method

    generation_url = settings.generation_url or "http://127.0.0.1:8100/v1"
    embedding_runtime = {
        "backend": settings.embedding_backend,
        "model": settings.embedding_model_path or settings.embedding_model,
    }

    try:
        status = invoke("health", model_gateway.health)
        from app.graph import graph_service
        return {
            "version": status.get("version"),
            "models": status.get("models", {}),
            "gateway_url": generation_url,
            "embedding_runtime": embedding_runtime,
            "runtime_root": str(settings.runtime_root),
            "queues": {
                "tasks": task_queue_status(),
                "llm": llm_queue_stats(),
                "model_lanes": model_lane_stats(),
            },
            "graph": {
                "mode": graph_service.mode,
                "neo4j_configured": graph_service.projector.available(),
            },
            "runtime_profiles": runtime_profile_manifest(),
            "context_tokenizer": {
                "method": tokenizer_method(),
                "configured_path": settings.generation_tokenizer_path,
                "exact": tokenizer_method().startswith("tokenizer:"),
            },
        }
    except Exception as exc:
        return {
            "error": str(exc),
            "gateway_url": generation_url,
            "embedding_runtime": embedding_runtime,
            "runtime_root": str(settings.runtime_root),
        }


@router.get("/system/queues")
def queues():
    """任务队列与 LLM 队列状态。"""
    return {
        "tasks": task_queue_status(),
        "llm": llm_queue_stats(),
    }


@router.post("/system/restart")
def restart_system_services():
    """Restart application services only while the task worker is idle."""
    queue = task_queue_status()
    running_task_id = str(queue.get("running_task_id") or "")
    if running_task_id:
        return JSONResponse(
            {"error": "TASK_RUNNING", "running_task_id": running_task_id},
            status_code=409,
        )
    app_root = Path(__file__).resolve().parents[2]
    script = app_root / "deploy" / "start_services.sh"
    if not script.is_file():
        return JSONResponse({"error": "RESTART_SCRIPT_NOT_FOUND"}, status_code=500)
    log_path = Path("/tmp/report-assembly-restart.log")
    with log_path.open("ab") as log:
        subprocess.Popen(
            [
                "bash", "-lc",
                'sleep 1; exec "$1" "$2"',
                "restart-services", str(script), str(app_root),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return JSONResponse(
        {"ok": True, "status": "restarting", "log": str(log_path)},
        status_code=202,
    )


@router.get("/system/resources")
def system_resources():
    """主机资源快照:GPU/CPU/内存(系统设置页 15s 轮询)。只读,不触发模型调度。"""
    return _resource_snapshot()


@router.get("/system/performance-summary")
def system_performance_summary():
    """Return the latest completed workload profile without exposing task payloads."""
    with session_scope() as s:
        rows = s.execute(
            select(ORMShortMemory.c.task_id, ORMShortMemory.c.payload)
        ).mappings().all()
    candidates = []
    for row in rows:
        payload = json.loads(row["payload"] or "{}")
        profile = payload.get("workload_profile") or {}
        totals = profile.get("totals") if isinstance(profile, dict) else {}
        if not totals or not int(totals.get("calls") or 0):
            continue
        updated_at = payload.get("updated_at") or payload.get("last_progress_at") or 0
        try:
            sort_value = float(updated_at)
        except (TypeError, ValueError):
            sort_value = 0.0
        candidates.append((
            sort_value,
            str(row["task_id"]),
            payload,
            profile,
        ))
    if not candidates:
        return {"task": None, "profile": {}}
    _updated, task_id, payload, profile = max(candidates, key=lambda item: item[0])
    return {
        "task": {
            "task_id": task_id,
            "theme": payload.get("theme") or "未命名任务",
            "stage": payload.get("stage") or "",
            "updated_at": payload.get("updated_at") or payload.get("last_progress_at") or "",
        },
        "profile": profile,
    }


def _resource_snapshot() -> dict:
    import psutil
    payload: dict = {
        "gpu": {"available": False, "processes": []},
        "cpu": {"load_avg": [], "cores": 0, "percent": 0.0},
        "memory": {"total_mb": 0, "used_mb": 0, "available_mb": 0, "percent": 0.0},
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        cpu = psutil.cpu_percent(interval=0.15)
        payload["cpu"] = {
            "load_avg": [round(x, 2) for x in os.getloadavg()],
            "cores": psutil.cpu_count(logical=True) or 0,
            "percent": round(float(cpu), 1),
        }
    except Exception:
        pass
    try:
        mem = psutil.virtual_memory()
        payload["memory"] = {
            "total_mb": int(mem.total / 1024 / 1024),
            "used_mb": int(mem.used / 1024 / 1024),
            "available_mb": int(mem.available / 1024 / 1024),
            "percent": round(float(mem.percent), 1),
        }
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        processes = []
        for line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if len(parts) < 3:
                continue
            processes.append({"pid": parts[0], "name": parts[1], "used_mb": int(float(parts[2]))})
        gpu_out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        first = [p.strip() for p in gpu_out.stdout.strip().splitlines()[0].split(",")] if gpu_out.stdout.strip() else []
        payload["gpu"] = {
            "available": bool(first),
            "utilization": int(first[0]) if len(first) > 0 else 0,
            "used_mb": int(first[1]) if len(first) > 1 else 0,
            "total_mb": int(first[2]) if len(first) > 2 else 0,
            "free_mb": int(first[3]) if len(first) > 3 else 0,
            "processes": processes,
        }
    except Exception:
        payload["gpu"] = {"available": False, "processes": []}
    return payload


# ---------- 辅助 ----------

def _find_task_by_report(report_id: int) -> str | None:
    """Return the task that owns the report via the indexed report_versions row."""
    from app.infrastructure.orm import ORMReportVersion
    with session_scope() as s:
        row = s.execute(
            select(ORMReportVersion.c.task_id)
            .where(ORMReportVersion.c.report_id == int(report_id))
            .order_by(ORMReportVersion.c.id.desc())
            .limit(1)
        ).mappings().first()
    task_id = str(row["task_id"] or "") if row else ""
    return task_id if task_id else _find_task_by_short_memory(report_id)


def _find_task_by_short_memory(report_id: int) -> str | None:
    from app.infrastructure.orm import ORMShortMemory
    with session_scope() as s:
        rows = s.execute(select(ORMShortMemory.c.task_id, ORMShortMemory.c.payload)).all()
    for task_id, payload_text in rows:
        try:
            payload = json.loads(payload_text or "{}")
        except (TypeError, ValueError):
            continue
        if payload.get("report_id") == int(report_id):
            return task_id
    return None


def _sentence_details_bulk(rows) -> dict[int, dict]:
    """Resolve all sentence lineage in three queries instead of per sentence."""
    refs_by_sentence = {int(row["id"]): json.loads(row["source_refs"] or "{}") for row in rows}
    fact_ids = {int(fid) for refs in refs_by_sentence.values() for fid in (refs.get("fact_ids") or [])}
    inference_ids = {int(iid) for refs in refs_by_sentence.values() for iid in (refs.get("inference_ids") or [])}
    with session_scope() as s:
        facts = s.execute(select(ORMFact).where(ORMFact.c.id.in_(fact_ids))).mappings().all() if fact_ids else []
        evidence = s.execute(select(ORMEvidence).where(ORMEvidence.c.fact_id.in_(fact_ids))).mappings().all() if fact_ids else []
        inferences = s.execute(select(ORMInference).where(ORMInference.c.id.in_(inference_ids))).mappings().all() if inference_ids else []
    evidence_by_fact: dict[int, list[dict]] = {}
    for item in evidence:
        evidence_by_fact.setdefault(int(item["fact_id"]), []).append({
            "source_file": item["source_file"], "page": item["page"],
            "paragraph": item["paragraph"], "quote": item["quote"],
        })
    fact_by_id = {int(item["id"]): item for item in facts}
    inference_by_id = {int(item["id"]): item for item in inferences}
    result = {}
    for row in rows:
        sentence_id = int(row["id"])
        refs = refs_by_sentence[sentence_id]
        sources = [{
            "fact_id": fact_id,
            "content": fact_by_id[fact_id]["content"],
            "evidence": evidence_by_fact.get(fact_id, []),
        } for fact_id in (int(fid) for fid in (refs.get("fact_ids") or [])) if fact_id in fact_by_id]
        sentence_inferences = []
        for inference_id in (int(iid) for iid in (refs.get("inference_ids") or [])):
            item = inference_by_id.get(inference_id)
            if item is None:
                continue
            sentence_inferences.append({
                "inference_id": inference_id, "content": item["content"],
                "source_level": item["source_level"],
                "based_fact_ids": json.loads(item["based_fact_ids"] or "[]"),
                "reasoning_chain": item["reasoning_chain"], "dimension": item["dimension"],
                "analysis_type": item["analysis_type"], "confidence_level": item["confidence_level"],
                "confidence_reason": item["confidence_reason"], "uncertainty": item["uncertainty"],
            })
        result[sentence_id] = {
            "id": sentence_id, "content": row["user_edit"] or row["content"],
            "original_content": row["content"], "source_level": row["source_level"],
            "selected": row["selected"], "edit_history": json.loads(row["edit_history"] or "[]"),
            "sources": sources, "inferences": sentence_inferences,
        }
    return result


def _report_heading_strategy(style_profile_id: int | None, *, fallback_defaults: bool = False):
    variant = style.get_variant(style_profile_id) if style_profile_id else None
    dominant = variant.format_spec.get("dominant") if variant and isinstance(variant.format_spec, dict) else {}
    schema = dominant.get("template_schema") if isinstance(dominant, dict) else {}
    return detect_numbering_strategy(
        schema if isinstance(schema, dict) else {}, fallback_defaults=fallback_defaults,
    )


def _report_document_shape(plan_id: int | None) -> dict:
    from app.document_shape import normalize_document_shape
    if not plan_id:
        return normalize_document_shape(None)
    with session_scope() as s:
        row = s.execute(
            select(ORMPlan.c.final_plan_json).where(ORMPlan.c.id == int(plan_id))
        ).mappings().first()
    try:
        payload = json.loads((row or {}).get("final_plan_json") or "{}")
    except (TypeError, ValueError):
        payload = {}
    return normalize_document_shape(payload.get("document_shape"))


def _report_composition_mode(plan_id: int | None) -> str:
    from app.document_shape import normalize_composition_mode
    if not plan_id:
        return "chaptered"
    with session_scope() as s:
        row = s.execute(
            select(ORMPlan.c.final_plan_json).where(ORMPlan.c.id == int(plan_id))
        ).mappings().first()
    try:
        payload = json.loads((row or {}).get("final_plan_json") or "{}")
    except (TypeError, ValueError):
        payload = {}
    return normalize_composition_mode(
        payload.get("composition_mode"), shape=payload.get("document_shape"),
    )


def variant_fields(variant) -> dict:
    structure = dict(variant.structure or {})
    structure["asset_roles"] = _variant_asset_roles(variant)
    return {
        "id": variant.id,
        "name": variant.name,
        "description": variant.description,
        "structure": structure,
        "writing_style": variant.writing_style,
        "writing_patterns": variant.writing_patterns,
        "terminology": variant.terminology,
        "format_spec": variant.format_spec,
        "style_samples": variant.style_samples,
        "chapter_styles": variant.chapter_styles,
        "reasoning_profile": variant.reasoning_profile,
        "institution_rules": variant.institution_rules,
        "structure_policy": variant.structure_policy,
        "exemplar_bank": variant.exemplar_bank,
        "profile_confidence": {
            key: value for key, value in (variant.profile_confidence or {}).items()
            if key != "evidence_usage"
        },
        "profile_version": variant.profile_version,
        "source_reports": variant.source_reports,
        "status": variant.status,
    }


def variant_summary_fields(variant) -> dict:
    return {
        "id": variant.id,
        "name": variant.name,
        "description": variant.description,
        "status": variant.status,
        "profile_version": variant.profile_version,
        "profile_confidence": {
            key: value for key, value in (variant.profile_confidence or {}).items()
            if key != "evidence_usage"
        },
        "source_reports": variant.source_reports,
        "exemplar_count": len(variant.exemplar_bank or []),
        "asset_roles": _variant_asset_roles(variant),
        "document_shape": (variant.structure or {}).get("document_shape", {}),
    }


def _variant_asset_roles(variant) -> dict:
    """Expose template capabilities consistently for both new and legacy rows."""
    structure = variant.structure if isinstance(variant.structure, dict) else {}
    saved = structure.get("asset_roles") if isinstance(structure.get("asset_roles"), dict) else {}
    format_spec = variant.format_spec if isinstance(variant.format_spec, dict) else {}
    dominant = format_spec.get("dominant") if isinstance(format_spec.get("dominant"), dict) else format_spec
    has_layout = bool(
        saved.get("layout_master_available")
        or dominant.get("source_template_path")
        or dominant.get("template_schema")
    )
    has_structure = bool(
        saved.get("structural_reference")
        or structure.get("sections")
        or structure.get("heading_patterns")
    )
    has_editorial = bool(
        saved.get("editorial_reference")
        or variant.exemplar_bank
        or variant.style_samples
        or variant.writing_style
    )
    if saved:
        result = dict(saved)
        result.update({
            "editorial_reference": has_editorial,
            "structural_reference": has_structure,
            "layout_master_available": has_layout,
            "layout_master": bool(saved.get("layout_master") or has_layout),
        })
    else:
        result = {
            "editorial_reference": has_editorial,
            "structural_reference": has_structure,
            "layout_master": has_layout,
            "layout_master_available": has_layout,
        }
    result.setdefault(
        "note",
        "该模板包含可复用的原始 DOCX 版式母版。" if has_layout
        else "该模板用于文体和结构参考；Word 导出将使用任务默认版式。",
    )
    return result


def _task_rows() -> list:
    from app.infrastructure.orm import ORMShortMemory
    from sqlalchemy import select
    with session_scope() as s:
        return s.execute(
            select(ORMShortMemory.c.task_id, ORMShortMemory.c.payload)
            .order_by(ORMShortMemory.c.task_id.desc())
        ).mappings().all()


def _material_task_index() -> dict[int, list[dict]]:
    index: dict[int, list[dict]] = {}
    for row in _task_rows():
        payload = json.loads(row["payload"])
        for material_id in payload.get("material_ids", []) or []:
            try:
                mid = int(material_id)
            except (TypeError, ValueError):
                continue
            index.setdefault(mid, []).append({
                "task_id": row["task_id"],
                "theme": payload.get("theme", ""),
                "stage": payload.get("stage", ""),
                "report_id": payload.get("report_id"),
            })
    return index


def _report_task_index() -> dict[int, dict]:
    index: dict[int, dict] = {}
    for row in _task_rows():
        payload = json.loads(row["payload"])
        report_id = payload.get("report_id")
        if report_id is None:
            continue
        try:
            rid = int(report_id)
        except (TypeError, ValueError):
            continue
        index[rid] = {
            "task_id": row["task_id"],
            "theme": payload.get("theme", ""),
            "stage": payload.get("stage", ""),
            "created_at": payload.get("created_at", ""),
        }
    return index


def _task_view(payload: dict) -> dict:
    """Return UI-safe task payload without mutating historical task records."""
    view = dict(payload or {})
    stage = str(view.get("stage") or "")
    queue_status = dict(view.get("queue_status") or {})
    queue_state = str(queue_status.get("status") or "")
    if stage in {"review", "done"} and queue_state in {"", "queued", "running", "paused"}:
        queue_status["status"] = "completed"
        view["queue_status"] = queue_status
    elif stage == "failed" and queue_state in {"", "queued", "running", "paused"}:
        queue_status["status"] = "failed"
        view["queue_status"] = queue_status
    parse_errors = []
    embed_errors = list(view.get("embed_errors") or [])
    seen_embed = {item.get("filename") for item in embed_errors if isinstance(item, dict)}
    for item in view.get("parse_errors") or []:
        if not isinstance(item, dict):
            continue
        error = str(item.get("error", ""))
        if error.lower().startswith("embed:"):
            filename = item.get("filename")
            if filename not in seen_embed:
                embed_errors.append(item)
                seen_embed.add(filename)
        else:
            parse_errors.append(item)
    view["parse_errors"] = parse_errors
    view["embed_errors"] = embed_errors
    stats = dict(view.get("parse_stats") or {})
    if stats:
        stats["parse_error_count"] = len(parse_errors)
        stats["embed_error_count"] = len(embed_errors)
        stats["failure_reasons"] = _reason_buckets_for_api(parse_errors)
        stats["embed_failure_reasons"] = _reason_buckets_for_api(embed_errors)
        view["parse_stats"] = stats
    return view


def _reason_buckets_for_api(errors: list[dict]) -> dict:
    buckets: dict[str, int] = {}
    for item in errors:
        error = str(item.get("error", "")).lower()
        if "timed out" in error or "timeout" in error:
            key = "timeout"
        elif "database is locked" in error:
            key = "database_locked"
        elif "no route to host" in error or "connection reset" in error:
            key = "network"
        elif "embed:" in error:
            key = "embedding"
        else:
            key = "other"
        buckets[key] = buckets.get(key, 0) + 1
    return buckets


def _progress_summary(payload: dict) -> dict:
    stage_timings = payload.get("stage_timings") or {}
    stage = payload.get("stage", "")
    progress = {
        "stage": stage,
        "parse_progress": payload.get("parse_progress"),
        "material_analysis_progress": payload.get("material_analysis_progress"),
        "evidence_progress": payload.get("evidence_progress"),
        "write_progress": payload.get("write_progress"),
        "stage_timings": stage_timings,
        "stage_durations": payload.get("stage_durations") or {},
        "llm_stats": payload.get("llm_stats"),
        "token_efficiency": payload.get("token_efficiency") or {},
        "workload_profile": payload.get("workload_profile") or {},
        "resource_samples": payload.get("resource_samples") or [],
        "ttfr_seconds": payload.get("ttfr_seconds"),
        "critical_path_done": payload.get("critical_path_done", False),
        "background_jobs": payload.get("background_jobs") or {},
        "artifact_status": payload.get("artifact_status") or {},
        "stage_health": {
            "material_analysis": payload.get("material_analysis_status") or {},
            "intelligence": payload.get("intelligence_status") or {},
            "quality": payload.get("qa_status") or {},
            "artifact_recording": payload.get("artifact_recording_error") or {},
        },
        "queue_status": payload.get("queue_status") or {},
        "performance": {
            "parse_reused": payload.get("parse_reused", 0),
            "parser_version": payload.get("parser_version", ""),
            "material_analysis_reused": payload.get("material_analysis_reused", 0),
            "material_analysis_cache_hits": payload.get("material_analysis_cache_hits", 0),
            "ttfr_seconds": payload.get("ttfr_seconds"),
        },
        # 解析/向量化错误分开透传(历史任务 embed: 前缀已由 _task_view 拆分)
        "parse_errors": payload.get("parse_errors") or [],
        "embed_errors": payload.get("embed_errors") or [],
        "parse_stats": payload.get("parse_stats") or {},
        "versions": payload.get("versions") or {},
    }
    if stage_timings:
        progress["total_seconds"] = max(stage_timings.values())
    return progress
