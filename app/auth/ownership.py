"""Owner-bound lookup contracts shared by routes and assistant tools."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.auth.accounts import Principal
from app.db import session_scope
from app.infrastructure.orm import Base

_TABLES = Base.metadata.tables


def ownership_query(kind: str, resource_id: str | int, user_id: int):
    if kind == "task":
        table = _TABLES["short_memory"]
        return select(table.c.task_id).where(
            table.c.task_id == resource_id, table.c.owner_user_id == user_id,
        )
    if kind in {"material", "template", "report", "thread"}:
        name = {
            "material": "materials", "template": "style_variants",
            "report": "reports", "thread": "interaction_threads",
        }[kind]
        table = _TABLES[name]
        return select(table.c.id).where(
            table.c.id == resource_id, table.c.owner_user_id == user_id,
        )
    if kind == "report_version":
        version = _TABLES["report_versions"]
        report = _TABLES["reports"]
        return select(version.c.id).select_from(
            version.join(report, version.c.report_id == report.c.id)
        ).where(version.c.id == resource_id, report.c.owner_user_id == user_id)
    if kind in {"comparison", "report_delta"}:
        name = "material_comparison_runs" if kind == "comparison" else "report_version_deltas"
        child = _TABLES[name]
        report = _TABLES["reports"]
        return select(child.c.id).select_from(
            child.join(report, child.c.report_id == report.c.id)
        ).where(child.c.id == resource_id, report.c.owner_user_id == user_id)
    if kind == "proposal":
        child = _TABLES["change_proposals"]
        thread = _TABLES["interaction_threads"]
        return select(child.c.id).select_from(
            child.join(thread, child.c.thread_id == thread.c.id)
        ).where(child.c.id == resource_id, thread.c.owner_user_id == user_id)
    if kind == "notification":
        note = _TABLES["interaction_notifications"]
        task = _TABLES["short_memory"]
        report = _TABLES["reports"]
        return select(note.c.id).select_from(
            note.outerjoin(task, note.c.task_id == task.c.task_id)
            .outerjoin(report, note.c.report_id == report.c.id)
        ).where(note.c.id == resource_id, or_(
            task.c.owner_user_id == user_id, report.c.owner_user_id == user_id,
        ))
    raise ValueError("UNKNOWN_RESOURCE_KIND")


def require_owner(kind: str, resource_id: str | int, principal: Principal) -> None:
    if kind != "task":
        try:
            resource_id = int(resource_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    with session_scope() as s:
        found = s.execute(ownership_query(kind, resource_id, principal.user_id)).first()
    if found is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")


def require_admin(principal: Principal) -> None:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="ADMIN_REQUIRED")
