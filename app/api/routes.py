"""REST API:任务、材料、报告、模板。"""
import hashlib
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, func, insert, select, update

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
    ORMSentence,
    ORMShortMemory,
    ORMTaskArtifact,
    ORMUnit,
)
from app.llm_queue import llm_queue_stats
from app.llm_scheduler import invoke
from app.memory import short_term, style
from app.parser import parse_file
from app.token_monitor import build_token_efficiency, list_llm_calls
from app.workflow import WorkflowController
from app.workflow.queue import enqueue_task, task_queue_status

router = APIRouter(prefix="/api")


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
                material_ids.append(existing["id"])  # 同内容文件:复用已入库材料(解析/向量/理解全缓存)
                continue
            filename = Path(upload.filename).name
            dest = settings.materials_dir / f"{task_id}_{filename}"
            with dest.open("wb") as fh:
                fh.write(content)
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
    material_ids = list(dict.fromkeys(material_ids))
    if not material_ids:
        return JSONResponse({"error": "NO_MATERIALS"}, status_code=400)
    short_term.save_task(task_id, {
        "theme": theme,
        "user_requirements": requirements,
        "variant_id": variant_id,
        "material_ids": material_ids,
        "stage": "created",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    return {"task_id": task_id, "material_count": len(material_ids)}


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
            "report_id": payload.get("report_id"),
            "material_count": len(payload.get("material_ids", []) or []),
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


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    task = short_term.load_task(task_id)
    if task is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    return _task_view(task)


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


@router.get("/tasks/{task_id}/token-efficiency")
def task_token_efficiency(task_id: str):
    """任务级 Token 成本结构与中间产物利用率。"""
    task = short_term.load_task(task_id)
    if task is None:
        return JSONResponse({"error": "TASK_NOT_FOUND"}, status_code=404)
    summary = task.get("token_efficiency") or build_token_efficiency(task_id, task.get("report_id"))
    return {
        "summary": summary,
        "calls": list_llm_calls(task_id),
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
                ORMMaterial.c.is_duplicate, ORMMaterial.c.duplicate_of,
                func.count(ORMUnit.c.id).label("unit_count"),
            )
            .select_from(
                ORMMaterial.outerjoin(ORMUnit, ORMUnit.c.material_id == ORMMaterial.c.id)
            )
            .group_by(
                ORMMaterial.c.id, ORMMaterial.c.filename, ORMMaterial.c.file_type,
                ORMMaterial.c.is_duplicate, ORMMaterial.c.duplicate_of,
            )
            .order_by(ORMMaterial.c.id.desc())
        ).mappings().all()
    return [{
        "id": r["id"], "filename": r["filename"], "file_type": r["file_type"],
        "unit_count": r["unit_count"], "is_duplicate": r["is_duplicate"],
        "duplicate_of": r["duplicate_of"],
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
    return [{
        "id": r["id"], "title": r["title"], "status": r["status"],
        "created_at": r["created_at"], "sentence_count": r["sentence_count"],
        "task": report_tasks.get(r["id"]),
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
    result = {
        "id": report["id"],
        "title": report["title"],
        "status": report["status"],
        "task_id": _find_task_by_report(report["id"]),
        "sections": [],
    }
    current_section = None
    current_paragraph = None
    for row in sentences:
        refs = json.loads(row["source_refs"])
        detail = _sentence_detail(row, refs)
        if current_section is None or row["section"] != current_section:
            current_section = row["section"]
            current_paragraph = None
            section = {"title": row["section"], "paragraphs": []}
            result["sections"].append(section)
        if row["paragraph"] != current_paragraph:
            current_paragraph = row["paragraph"]
            section["paragraphs"].append({"sentences": []})
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
    new_title = str(payload.get("new_title", "")).strip()
    if not old_title or not new_title:
        return JSONResponse({"error": "SECTION_TITLE_REQUIRED"}, status_code=400)
    if old_title == new_title:
        return {"ok": True, "title": new_title}
    from app.infrastructure.orm import ORMSentence, ORMPlan, ORMReport, Base
    from sqlalchemy import select, update
    with session_scope() as s:
        report = s.execute(select(ORMReport.c.plan_id).where(ORMReport.c.id == report_id)).first()
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
    return {"ok": True, "old_title": old_title, "title": new_title}


@router.put("/reports/{report_id}/sentences/{sentence_id}")
def update_sentence(report_id: int, sentence_id: int, payload: dict):
    """在线编辑:勾选状态与用户修改文字(修改内容记入 edit_history + user_memory)。"""
    content = payload.get("content")
    from app.infrastructure.orm import ORMSentence, Base
    from sqlalchemy import select, update
    with session_scope() as s:
        row = s.execute(
            select(ORMSentence.c.content, ORMSentence.c.user_edit, ORMSentence.c.edit_history)
            .where(ORMSentence.c.id == sentence_id, ORMSentence.c.report_id == report_id)
        ).first()
        if row is None:
            return JSONResponse({"error": "SENTENCE_NOT_FOUND"}, status_code=404)
        history = json.loads(row[2] or "[]")
        if content is not None and content != (row[1] or ""):
            history.append({"time": datetime.now().strftime("%m-%d %H:%M"), "editor": "user", "content": content})
            user_memory = Base.metadata.tables["user_memory"]
            s.execute(user_memory.insert().values(
                note_type="edit", summary="用户修改了报告句子",
                content=f"{str(row[0] or '')[:60]} → {content[:60]}",
            ))
        s.execute(
            update(ORMSentence).where(
                ORMSentence.c.id == sentence_id, ORMSentence.c.report_id == report_id
            ).values(
                selected=1 if payload.get("selected", True) else 0,
                user_edit=content if content is not None else row[1],
                edit_history=json.dumps(history, ensure_ascii=False),
            )
        )
    return {"ok": True}


@router.post("/reports/{report_id}/finalize")
def finalize_report(report_id: int):
    """审核完成:报告置为 final,任务进入已完成。"""
    task_id = _find_task_by_report(report_id)
    if task_id is None:
        return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
    WorkflowController(task_id).finalize()
    return {"ok": True}


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
        return {
            "version": status.get("version"),
            "models": status.get("models", {}),
            "gateway_url": settings.ollama_url,
            "runtime_root": str(settings.runtime_root),
            "queues": {
                "tasks": task_queue_status(),
                "llm": llm_queue_stats(),
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


# ---------- 辅助 ----------

def _find_task_by_report(report_id: int) -> str | None:
    """从短期记忆中反查持有该报告的任务。"""
    from app.infrastructure.orm import ORMShortMemory
    from sqlalchemy import select
    with session_scope() as s:
        rows = s.execute(select(ORMShortMemory.c.task_id, ORMShortMemory.c.payload)).all()
    for row in rows:
        payload = json.loads(row[1])
        try:
            payload_report_id = int(payload.get("report_id"))
        except (TypeError, ValueError):
            continue
        if payload_report_id == int(report_id):
            return row[0]
    return None


def _sentence_detail(row, refs: dict) -> dict:
    fact_ids = refs.get("fact_ids", [])
    inference_ids = refs.get("inference_ids", [])
    sources = []
    from app.infrastructure.orm import ORMFact, ORMEvidence
    from sqlalchemy import select
    with session_scope() as s:
        for fact_id in fact_ids:
            fact = s.execute(select(ORMFact).where(ORMFact.c.id == fact_id)).mappings().first()
            if fact is None:
                continue
            evidence = s.execute(
                select(ORMEvidence.c.source_file, ORMEvidence.c.page,
                       ORMEvidence.c.paragraph, ORMEvidence.c.quote)
                .where(ORMEvidence.c.fact_id == fact_id)
            ).mappings().all()
            sources.append({
                "fact_id": fact_id,
                "content": fact["content"],
                "evidence": [dict(item) for item in evidence],
            })
        inferences = []
        from app.infrastructure.orm import ORMInference
        for inference_id in inference_ids:
            inference = s.execute(select(ORMInference).where(ORMInference.c.id == inference_id)).mappings().first()
            if inference is not None:
                inferences.append({
                    "inference_id": inference_id,
                    "content": inference["content"],
                    "source_level": inference["source_level"],
                    "based_fact_ids": json.loads(inference["based_fact_ids"]),
                    "reasoning_chain": inference["reasoning_chain"],
                })
    return {
        "id": row["id"],
        "content": row["user_edit"] or row["content"],
        "original_content": row["content"],
        "source_level": row["source_level"],
        "selected": row["selected"],
        "edit_history": json.loads(row["edit_history"] or "[]"),
        "sources": sources,
        "inferences": inferences,
    }


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
