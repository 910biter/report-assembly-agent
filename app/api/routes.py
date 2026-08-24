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

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import session_scope
from app.export import export_report
from app.infrastructure.orm import (
    Base,
    ORMConflict,
    ORMEvidence,
    ORMFact,
    ORMInference,
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
from app.llm_scheduler import invoke
from app.memory import short_term, style
from app.parser import parse_file
from app.rendering.headings import detect_numbering_strategy, format_heading, strip_heading_prefix
from app.report_versions import (
    attach_delta_version,
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
    latest_report_version,
    list_report_versions,
)
from app.task_runs import create_task_run
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
    theme: str = Form(...),
    requirements: str = Form(""),
    variant_id: int | None = Form(None),
    existing_material_ids: str = Form(""),
    files: list[UploadFile] | None = File(default=None),
):
    """创建任务:上传材料 + 指定主题 + 可选模板(不选则用全局默认模板)。"""
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
    })
    return {"task_id": task_id, "material_count": len(material_ids)}


@router.post("/reports/{report_id}/incremental/tasks")
def create_incremental_task(
    report_id: int,
    update_reason: str = Form(""),
    existing_material_ids: str = Form(""),
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
    }


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
            "run_history": payload.get("run_history") or [],
            "incremental_update": bool(payload.get("incremental_update")),
            "incremental_added_material_count": len(payload.get("incremental_added_material_ids") or []),
            "update_reason": payload.get("incremental_delta", {}).get("update_reason", "") if isinstance(payload.get("incremental_delta"), dict) else "",
            "progress": _progress_summary(payload),
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
    return _task_view(task)


@router.post("/tasks/{task_id}/evidence-gaps/retrieve")
def retrieve_evidence_gaps(task_id: str):
    """Bounded WriteHERE hook: retrieve QA-declared evidence gaps only."""
    if short_term.load_task(task_id) is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    from app.workflow.controller import WorkflowController
    try:
        facts = WorkflowController(task_id).reflow_evidence_gaps()
    except Exception as exc:
        return JSONResponse({"error": "EVIDENCE_GAP_RETRIEVAL_FAILED", "detail": str(exc)[:200]}, status_code=500)
    return {"status": "retrieved", "fact_count": len(facts), "facts": facts}


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
    with session_scope() as s:
        facts = []
        for fact_id in task.get("fact_ids", []):
            row = s.execute(
                select(ORMFact).where(ORMFact.c.id == fact_id)
            ).mappings().first()
            if row is None:
                continue
            evidence = s.execute(
                select(
                    ORMEvidence.c.source_file, ORMEvidence.c.page, ORMEvidence.c.paragraph,
                    ORMEvidence.c.quote,
                ).where(ORMEvidence.c.fact_id == fact_id)
            ).mappings().all()
            facts.append({
                "id": row["id"], "content": row["content"], "dimension": row["dimension"],
                "evidence": [dict(e) for e in evidence],
            })
        inferences = []
        for inference_id in task.get("inference_ids", []) + task.get("external_ids", []):
            row = s.execute(
                select(ORMInference).where(ORMInference.c.id == inference_id)
            ).mappings().first()
            if row is not None:
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
        conflicts = []
        for conflict_id in task.get("conflict_ids", []):
            row = s.execute(
                select(ORMConflict).where(ORMConflict.c.id == conflict_id)
            ).mappings().first()
            if row is not None:
                conflicts.append({
                    "id": row["id"], "fact_key": row["fact_key"],
                    "entries": json.loads(row["entries"]), "status": row["status"],
                })
    return {"facts": facts, "inferences": inferences, "conflicts": conflicts}


@router.get("/tasks/{task_id}/graph")
def task_graph(task_id: str):
    """Evidence-grounded task graph for the Analysis workspace.

    The API intentionally exposes only this task's graph projection. Every
    edge carries Fact IDs, so the UI can return to the ordinary evidence panel.
    """
    if short_term.load_task(task_id) is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    from app.graph import graph_service
    return graph_service.task_graph(task_id)


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
    """材料详情:内容单元列表(文本/表格/图片文本和解析元数据)。"""
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
    return {
        "id": material["id"], "filename": material["filename"], "file_type": material["file_type"],
        "parsed_at": material["parsed_at"],
        "parse_status": "ready" if units else "pending",
        "is_duplicate": material["is_duplicate"], "duplicate_of": material["duplicate_of"],
        "tasks": material_tasks.get(material["id"], []),
        "units": [{
            "id": u["id"], "kind": u["kind"], "content": u["content"],
            "page": u["page"], "image_desc": u["image_desc"],
            "metadata": json.loads(u["metadata_json"] or "{}"),
        } for u in units],
    }


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
    result = {
        "id": report["id"],
        "title": report["title"],
        "status": report["status"],
        "task_id": task_id,
        "versions": list_report_versions(report["id"]),
        "qa_issues": list((task_payload or {}).get("qa_notes") or []),
        "sections": [],
    }
    heading_strategy = _report_heading_strategy(report["style_profile_id"])
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
                "paragraphs": [],
            }
            result["sections"].append(section)
        if row["paragraph"] != current_paragraph:
            current_paragraph = row["paragraph"]
            section["paragraphs"].append({"sentences": []})
        if row["source_level"] == "SUBHEADING":
            subsection_index += 1
            detail["display_content"] = format_heading(
                2,
                [chapter_index, subsection_index],
                detail["content"],
                heading_strategy,
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


@router.post("/reports/{report_id}/finalize")
def finalize_report(report_id: int, payload: dict | None = None):
    """审核完成:报告置为 final,任务进入已完成。"""
    task_id = _find_task_by_report(report_id)
    if task_id is None:
        return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
    payload = payload or {}
    task = short_term.load_task(task_id) or {}
    blocking_types = {"MISSING_SECTION", "SCALE_UNDERFILL", "CITATION_MISMATCH"}
    blocking = [issue for issue in (task.get("qa_notes") or []) if issue.get("type") in blocking_types]
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
    return [variant_fields(v) for v in style.list_variants()]


@router.get("/style/variants/{variant_id}/template-schema")
def get_template_schema(variant_id: int):
    variant = style.get_variant(variant_id)
    if variant is None:
        return JSONResponse({"error": "VARIANT_NOT_FOUND"}, status_code=404)
    dominant = variant.format_spec.get("dominant") if isinstance(variant.format_spec, dict) else {}
    schema = dominant.get("template_schema") if isinstance(dominant, dict) else {}
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


# ---------- 系统 ----------

@router.get("/health")
def health():
    """网关连通与模型在位状态(系统设置页使用)。"""
    from app.gateway import model_gateway

    try:
        status = invoke("health", model_gateway.health)
        from app.graph import graph_service
        return {
            "version": status.get("version"),
            "models": status.get("models", {}),
            "gateway_url": settings.ollama_url,
            "runtime_root": str(settings.runtime_root),
            "queues": {
                "tasks": task_queue_status(),
                "llm": llm_queue_stats(),
            },
            "graph": {
                "mode": graph_service.mode,
                "neo4j_configured": graph_service.projector.available(),
            },
        }
    except Exception as exc:
        return {
            "error": str(exc),
            "gateway_url": settings.ollama_url,
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
    """返回报告的根任务,兼容旧版曾创建的独立增量任务。"""
    from app.infrastructure.orm import ORMShortMemory
    from sqlalchemy import select
    with session_scope() as s:
        rows = s.execute(select(ORMShortMemory.c.task_id, ORMShortMemory.c.payload)).all()
    matches: list[tuple[str, dict]] = []
    for row in rows:
        payload = json.loads(row[1])
        try:
            payload_report_id = int(payload.get("report_id"))
        except (TypeError, ValueError):
            continue
        if payload_report_id == int(report_id):
            matches.append((str(row[0]), payload))
    if not matches:
        return None
    roots = [item for item in matches if not item[1].get("incremental_base_task_id")]
    candidates = roots or matches
    candidates.sort(key=lambda item: (str(item[1].get("created_at") or ""), item[0]))
    return candidates[0][0]


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


def _report_heading_strategy(style_profile_id: int | None):
    variant = style.get_variant(style_profile_id) if style_profile_id else None
    locked = style.get_locked_variant()
    selected = variant if _api_variant_has_template_roles(variant) else None
    if selected is None and _api_variant_has_template_roles(locked):
        selected = locked
    dominant = selected.format_spec.get("dominant") if selected and isinstance(selected.format_spec, dict) else {}
    schema = dominant.get("template_schema") if isinstance(dominant, dict) else {}
    return detect_numbering_strategy(schema if isinstance(schema, dict) else {})


def _api_variant_has_template_roles(variant) -> bool:
    if variant is None or not isinstance(variant.format_spec, dict):
        return False
    dominant = variant.format_spec.get("dominant")
    schema = dominant.get("template_schema") if isinstance(dominant, dict) else {}
    roles = schema.get("style", {}).get("roles", {}) if isinstance(schema, dict) else {}
    return isinstance(roles, dict) and bool(roles.get("document_title") and roles.get("body"))


def variant_fields(variant) -> dict:
    return {
        "id": variant.id,
        "name": variant.name,
        "description": variant.description,
        "structure": variant.structure,
        "writing_style": variant.writing_style,
        "terminology": variant.terminology,
        "format_spec": variant.format_spec,
        "style_samples": variant.style_samples,
        "chapter_styles": variant.chapter_styles,
        "reasoning_profile": variant.reasoning_profile,
        "institution_rules": variant.institution_rules,
        "source_reports": variant.source_reports,
        "status": variant.status,
    }


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
