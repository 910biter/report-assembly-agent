"""REST API:任务、材料、报告、模板。"""
import hashlib
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.config import settings
from app.db import connect
from app.export import export_report
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
        with connect() as conn:
            existing = conn.execute(
                "SELECT id FROM materials WHERE file_hash=?", (file_hash,)
            ).fetchone()
            if existing is not None:
                material_ids.append(existing["id"])  # 同内容文件:复用已入库材料(解析/向量/理解全缓存)
                continue
            filename = Path(upload.filename).name
            dest = settings.materials_dir / f"{task_id}_{filename}"
            with dest.open("wb") as fh:
                fh.write(content)
            cur = conn.execute(
                "INSERT INTO materials(filename, file_type, path, fingerprint, file_hash) VALUES(?, ?, ?, ?, ?)",
                (filename, dest.suffix.lower().lstrip("."), str(dest), file_hash, file_hash),
            )
            material_ids.append(cur.lastrowid)
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
    with connect() as conn:
        rows = conn.execute("SELECT task_id, payload FROM short_memory ORDER BY rowid DESC").fetchall()
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
    with connect() as conn:
        conn.execute("DELETE FROM task_artifacts WHERE task_id=?", (task_id,))
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
    placeholders = ",".join("?" * len(material_ids))
    with connect() as conn:
        parsed_ids = {r["material_id"] for r in conn.execute(
            "SELECT DISTINCT material_id FROM units").fetchall()}
        materials = conn.execute(
            f"SELECT * FROM materials WHERE id IN ({placeholders})", material_ids
        ).fetchall()
        parsed_count = sum(1 for m in materials if m["id"] in parsed_ids)
        result = []
        for material in materials:
            units = conn.execute(
                "SELECT kind, content, image_desc, page, metadata_json FROM units WHERE material_id=?",
                (material["id"],),
            ).fetchall()
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
    with connect() as conn:
        facts = []
        for fact_id in task.get("fact_ids", []):
            row = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
            if row is None:
                continue
            evidence = conn.execute(
                "SELECT source_file, page, paragraph, quote FROM evidence WHERE fact_id=?",
                (fact_id,),
            ).fetchall()
            facts.append({
                "id": row["id"], "content": row["content"], "dimension": row["dimension"],
                "evidence": [dict(e) for e in evidence],
            })
        inferences = []
        for inference_id in task.get("inference_ids", []) + task.get("external_ids", []):
            row = conn.execute("SELECT * FROM inferences WHERE id=?", (inference_id,)).fetchone()
            if row is not None:
                inferences.append({
                    "id": row["id"], "content": row["content"],
                    "source_level": row["source_level"],
                    "based_fact_ids": json.loads(row["based_fact_ids"]),
                    "reasoning_chain": row["reasoning_chain"],
                })
        conflicts = []
        for conflict_id in task.get("conflict_ids", []):
            row = conn.execute("SELECT * FROM conflicts WHERE id=?", (conflict_id,)).fetchone()
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
    with connect() as conn:
        rows = conn.execute(
            "SELECT m.id, m.filename, m.file_type, m.is_duplicate, m.duplicate_of, "
            "COUNT(u.id) AS unit_count "
            "FROM materials m LEFT JOIN units u ON u.material_id = m.id "
            "GROUP BY m.id ORDER BY m.id DESC"
        ).fetchall()
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
    with connect() as conn:
        material = conn.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
        if material is None:
            return JSONResponse({"error": "MATERIAL_NOT_FOUND"}, status_code=404)
        units = conn.execute(
            "SELECT * FROM units WHERE material_id=? ORDER BY id", (material_id,)
        ).fetchall()
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
    with connect() as conn:
        rows = conn.execute(
            "SELECT r.id, r.title, r.status, r.created_at, "
            "COUNT(s.id) AS sentence_count "
            "FROM reports r LEFT JOIN report_sentences s ON s.report_id = r.id "
            "GROUP BY r.id ORDER BY r.id DESC"
        ).fetchall()
    return [{
        "id": r["id"], "title": r["title"], "status": r["status"],
        "created_at": r["created_at"], "sentence_count": r["sentence_count"],
        "task": report_tasks.get(r["id"]),
    } for r in rows]


@router.get("/reports/{report_id}")
def get_report(report_id: int):
    """报告数据(章节→段落→句子,句子带来源分级与溯源细节)。"""
    with connect() as conn:
        report = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if report is None:
            return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
        sentences = conn.execute(
            "SELECT * FROM report_sentences WHERE report_id=? ORDER BY position",
            (report_id,),
        ).fetchall()
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
    with connect() as conn:
        report = conn.execute("SELECT plan_id FROM reports WHERE id=?", (report_id,)).fetchone()
        if report is None:
            return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
        conn.execute("UPDATE reports SET title=? WHERE id=?", (title, report_id))
        conn.execute("UPDATE report_plans SET title=? WHERE id=?", (title, report["plan_id"]))
        conn.execute(
            "INSERT INTO user_memory(note_type, summary, content) VALUES('edit', ?, ?)",
            ("用户修改了报告题目", title[:120]),
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
    with connect() as conn:
        report = conn.execute("SELECT plan_id FROM reports WHERE id=?", (report_id,)).fetchone()
        if report is None:
            return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
        count = conn.execute(
            "UPDATE report_sentences SET section=? WHERE report_id=? AND section=?",
            (new_title, report_id, old_title),
        ).rowcount
        if count == 0:
            return JSONResponse({"error": "SECTION_NOT_FOUND"}, status_code=404)
        plan = conn.execute(
            "SELECT structure, chapter_plans FROM report_plans WHERE id=?",
            (report["plan_id"],),
        ).fetchone()
        if plan is not None:
            structure = json.loads(plan["structure"] or "[]")
            chapter_plans = json.loads(plan["chapter_plans"] or "[]")
            structure = [new_title if str(item) == old_title else item for item in structure]
            for chapter in chapter_plans:
                if str(chapter.get("title", "")) == old_title:
                    chapter["title"] = new_title
            conn.execute(
                "UPDATE report_plans SET structure=?, chapter_plans=? WHERE id=?",
                (
                    json.dumps(structure, ensure_ascii=False),
                    json.dumps(chapter_plans, ensure_ascii=False),
                    report["plan_id"],
                ),
            )
        conn.execute(
            "INSERT INTO user_memory(note_type, summary, content) VALUES('edit', ?, ?)",
            ("用户修改了章节标题", f"{old_title} → {new_title}"),
        )
    return {"ok": True, "old_title": old_title, "title": new_title}


@router.put("/reports/{report_id}/sentences/{sentence_id}")
def update_sentence(report_id: int, sentence_id: int, payload: dict):
    """在线编辑:勾选状态与用户修改文字(修改内容记入 edit_history + user_memory)。"""
    content = payload.get("content")
    with connect() as conn:
        row = conn.execute(
            "SELECT content, user_edit, edit_history FROM report_sentences "
            "WHERE id=? AND report_id=?",
            (sentence_id, report_id),
        ).fetchone()
        if row is None:
            return JSONResponse({"error": "SENTENCE_NOT_FOUND"}, status_code=404)
        history = json.loads(row["edit_history"] or "[]")
        if content is not None and content != (row["user_edit"] or ""):
            history.append({"time": datetime.now().strftime("%m-%d %H:%M"), "editor": "user", "content": content})
            conn.execute(
                "INSERT INTO user_memory(note_type, summary, content) VALUES('edit', ?, ?)",
                ("用户修改了报告句子", f"{str(row['content'] or '')[:60]} → {content[:60]}"),
            )
        conn.execute(
            "UPDATE report_sentences SET selected=?, user_edit=?, edit_history=? WHERE id=? AND report_id=?",
            (1 if payload.get("selected", True) else 0,
             content if content is not None else row["user_edit"],
             json.dumps(history, ensure_ascii=False), sentence_id, report_id),
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
    with connect() as conn:
        rows = conn.execute("SELECT task_id, payload FROM short_memory").fetchall()
    for row in rows:
        payload = json.loads(row["payload"])
        try:
            payload_report_id = int(payload.get("report_id"))
        except (TypeError, ValueError):
            continue
        if payload_report_id == int(report_id):
            return row["task_id"]
    return None


def _sentence_detail(row, refs: dict) -> dict:
    fact_ids = refs.get("fact_ids", [])
    inference_ids = refs.get("inference_ids", [])
    sources = []
    with connect() as conn:
        for fact_id in fact_ids:
            fact = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
            if fact is None:
                continue
            evidence = conn.execute(
                "SELECT source_file, page, paragraph, quote FROM evidence WHERE fact_id=?",
                (fact_id,),
            ).fetchall()
            sources.append({
                "fact_id": fact_id,
                "content": fact["content"],
                "evidence": [dict(item) for item in evidence],
            })
        inferences = []
        for inference_id in inference_ids:
            inference = conn.execute("SELECT * FROM inferences WHERE id=?", (inference_id,)).fetchone()
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
    with connect() as conn:
        return conn.execute("SELECT task_id, payload FROM short_memory ORDER BY rowid DESC").fetchall()


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
        "business_coverage": payload.get("business_coverage"),
        "business_qa": payload.get("business_qa"),
        "preliminary_scale_plan": payload.get("preliminary_scale_plan") or {},
        "report_scale_plan": payload.get("report_scale_plan") or {},
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
