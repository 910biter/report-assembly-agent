"""在线页面:任务创建、历史任务、报告查看/编辑、模板管理。"""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app.db import session_scope
from app.infrastructure.orm import ORMReport
from sqlalchemy import select

_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"
templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))

web_router = APIRouter()


@web_router.get("/")
def index(request: Request):
    from dataclasses import asdict

    from app.memory.style import get_locked_variant, list_variants

    locked = get_locked_variant()
    template_locked = locked is not None
    template_summary = ""
    if locked is not None:
        dominant = locked.format_spec.get("dominant") if isinstance(locked.format_spec, dict) else {}
        template_summary = (
            f"变体「{locked.name}」 | 章节: {locked.structure.get('pattern', '') or '结构化章节'}"
            f" | 字体: {dominant.get('font_name', '默认')} {dominant.get('font_size_pt', '')}pt"
        )
    template_options = [
        {"id": v["id"], "name": v["name"], "status": v["status"], "structure_pattern": str(v["structure"])[:40]}
        for v in (asdict(variant) for variant in list_variants())
    ]
    return templates.TemplateResponse(request, "index.html", {
        "request": request, "active": "home",
        "template_locked": template_locked,
        "template_summary": template_summary,
        "template_options": template_options,
    })


@web_router.get("/tasks")
def tasks_page(request: Request):
    return templates.TemplateResponse(request, "tasks.html", {"request": request, "active": "tasks"})


@web_router.get("/tasks/{task_id}")
def task_page(request: Request, task_id: str):
    return templates.TemplateResponse(request, "task.html", {
        "request": request, "active": "tasks", "task_id": task_id,
    })


@web_router.get("/materials")
def materials_page(request: Request):
    return templates.TemplateResponse(request, "materials.html", {"request": request, "active": "materials"})


@web_router.get("/reports")
def reports_page(request: Request):
    return templates.TemplateResponse(request, "reports.html", {"request": request, "active": "reports"})


@web_router.get("/reports/{report_id}")
def report_page(request: Request, report_id: int):
    with session_scope() as s:
        report = s.execute(select(ORMReport).where(ORMReport.c.id == report_id)).mappings().first()
    if report is None:
        return JSONResponse({"error": "REPORT_NOT_FOUND"}, status_code=404)
    return templates.TemplateResponse(request, "report.html", {
        "request": request, "active": "reports",
        "report_id": report_id, "report_title": report["title"],
    })


@web_router.get("/style")
def style_page(request: Request):
    return templates.TemplateResponse(request, "style.html", {"request": request, "active": "style"})


@web_router.get("/settings")
def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {"request": request, "active": "settings"})
