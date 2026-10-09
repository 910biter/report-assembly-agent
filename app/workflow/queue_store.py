"""PostgreSQL authority for report queue ordering and atomic claims."""

from __future__ import annotations

import time
from contextlib import nullcontext

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import session_scope
from app.config import settings
from app.infrastructure.orm import Base

_QUEUE = Base.metadata.tables["task_queue_entries"]
_ACTIVE = ("queued", "running")
_CLAIM_LOCK = 782340998922


def enqueue(task_id: str, run_id: str, owner_user_id: int | None,
            priority: int = 50, *, _session=None) -> dict:
    with (nullcontext(_session) if _session is not None else session_scope()) as s:
        if owner_user_id is not None:
            s.execute(select(func.pg_advisory_xact_lock(782341000000 + int(owner_user_id))))
            current = s.execute(select(_QUEUE.c.id, _QUEUE.c.status).where(
                _QUEUE.c.task_id == task_id, _QUEUE.c.status.in_(_ACTIVE),
            ).limit(1)).mappings().first()
            if current is not None:
                return {"id": int(current["id"]), "status": current["status"], "duplicate": True}
            active_count = s.execute(select(func.count()).select_from(_QUEUE).where(
                _QUEUE.c.owner_user_id == owner_user_id, _QUEUE.c.status.in_(_ACTIVE),
            )).scalar_one()
            if int(active_count) >= max(1, settings.max_active_jobs_per_user):
                raise ValueError("USER_QUEUE_LIMIT_REACHED")
        stmt = pg_insert(_QUEUE).values(
            task_id=task_id, run_id=run_id, owner_user_id=owner_user_id,
            priority=priority, status="queued", queued_at=time.time(),
        ).on_conflict_do_nothing().returning(_QUEUE.c.id)
        row = s.execute(stmt).first()
        if row is None:
            existing = s.execute(select(_QUEUE.c.id, _QUEUE.c.status).where(
                _QUEUE.c.task_id == task_id,
                _QUEUE.c.status.in_(_ACTIVE),
            ).order_by(_QUEUE.c.id.desc()).limit(1)).mappings().one()
            return {"id": int(existing["id"]), "status": existing["status"], "duplicate": True}
        return {"id": int(row.id), "status": "queued", "duplicate": False}


def claim_next() -> dict | None:
    """Claim at most one waiting run across all application processes."""
    with session_scope() as s:
        s.execute(select(func.pg_advisory_xact_lock(_CLAIM_LOCK)))
        active = s.execute(select(_QUEUE.c.id).where(_QUEUE.c.status == "running").limit(1)).first()
        if active:
            return None
        row = s.execute(select(_QUEUE).where(
            _QUEUE.c.status == "queued",
        ).order_by(_QUEUE.c.priority, _QUEUE.c.id).with_for_update(skip_locked=True).limit(1)).mappings().first()
        if row is None:
            return None
        now = time.time()
        s.execute(update(_QUEUE).where(_QUEUE.c.id == row["id"]).values(
            status="running", started_at=now, heartbeat_at=now,
        ))
        return dict(row) | {"status": "running", "started_at": now, "heartbeat_at": now}


def position(task_id: str) -> int | None:
    """Number of active jobs ahead; never return another user's job data."""
    with session_scope() as s:
        current = s.execute(select(_QUEUE.c.id, _QUEUE.c.priority, _QUEUE.c.status).where(
            _QUEUE.c.task_id == task_id, _QUEUE.c.status.in_(_ACTIVE),
        ).order_by(_QUEUE.c.id.desc()).limit(1)).mappings().first()
        if current is None:
            return None
        if current["status"] == "running":
            return 0
        running = s.execute(select(func.count()).select_from(_QUEUE).where(
            _QUEUE.c.status == "running",
        )).scalar_one()
        waiting_ahead = s.execute(select(func.count()).select_from(_QUEUE).where(
            _QUEUE.c.status == "queued",
            or_(
                _QUEUE.c.priority < current["priority"],
                and_(_QUEUE.c.priority == current["priority"], _QUEUE.c.id < current["id"]),
            ),
        )).scalar_one()
    return int(running) + int(waiting_ahead)


def cancel(task_id: str, *, _session=None) -> bool:
    with (nullcontext(_session) if _session is not None else session_scope()) as s:
        result = s.execute(update(_QUEUE).where(
            _QUEUE.c.task_id == task_id, _QUEUE.c.status == "queued",
        ).values(status="cancelled", finished_at=time.time()))
        return result.rowcount > 0


def finish(entry_id: int, status: str, error: str = "", *, task_id: str | None = None,
           task_updates: dict | None = None) -> None:
    if status not in {"completed", "failed", "paused"}:
        raise ValueError("INVALID_QUEUE_FINISH_STATUS")
    with session_scope() as s:
        if task_updates is not None:
            if not task_id:
                raise ValueError("TASK_ID_REQUIRED")
            from app.memory import short_term
            short_term.mutate_task(task_id, lambda task: {**task, **task_updates}, _session=s)
        result = s.execute(update(_QUEUE).where(
            _QUEUE.c.id == entry_id, _QUEUE.c.status == "running",
        ).values(status=status, finished_at=time.time(), error=error[:500]))
        if result.rowcount != 1:
            raise ValueError("QUEUE_ENTRY_NOT_RUNNING")


def reconcile_running() -> list[str]:
    """Call only while holding the cross-process execution slot."""
    with session_scope() as s:
        rows = s.execute(select(_QUEUE.c.id, _QUEUE.c.task_id).where(
            _QUEUE.c.status == "running",
        )).mappings().all()
        if rows:
            from app.memory import short_term
            for row in rows:
                short_term.mutate_task(str(row["task_id"]), lambda task: {
                    **task, "stage": "paused", "control_request": "",
                    "queue_status": {"status": "paused", "note": "service_restart"},
                }, _session=s)
            s.execute(update(_QUEUE).where(_QUEUE.c.id.in_(row["id"] for row in rows)).values(
                status="paused", finished_at=time.time(), error="service_restart",
            ))
    return [str(row["task_id"]) for row in rows]


def heartbeat(entry_id: int) -> None:
    with session_scope() as s:
        s.execute(update(_QUEUE).where(
            _QUEUE.c.id == entry_id, _QUEUE.c.status == "running",
        ).values(heartbeat_at=time.time()))


def active_state(task_id: str) -> dict | None:
    with session_scope() as s:
        row = s.execute(select(_QUEUE).where(
            _QUEUE.c.task_id == task_id, _QUEUE.c.status.in_(_ACTIVE),
        ).order_by(_QUEUE.c.id.desc()).limit(1)).mappings().first()
    return dict(row) if row else None


def snapshot() -> dict:
    with session_scope() as s:
        rows = s.execute(select(_QUEUE.c.task_id, _QUEUE.c.status).where(
            _QUEUE.c.status.in_(_ACTIVE),
        ).order_by(_QUEUE.c.priority, _QUEUE.c.id)).mappings().all()
    return {
        "running_task_id": next((row["task_id"] for row in rows if row["status"] == "running"), None),
        "pending": sum(row["status"] == "queued" for row in rows),
        "queued_task_ids": [row["task_id"] for row in rows if row["status"] == "queued"],
    }


def personal_snapshot(owner_user_id: int) -> dict:
    """Expose queue pressure and only the caller's task identifiers."""
    with session_scope() as s:
        rows = s.execute(select(
            _QUEUE.c.task_id, _QUEUE.c.owner_user_id, _QUEUE.c.status,
        ).where(_QUEUE.c.status.in_(_ACTIVE)).order_by(
            _QUEUE.c.priority, _QUEUE.c.id,
        )).mappings().all()
    rows = sorted(rows, key=lambda row: row["status"] != "running")
    running = any(row["status"] == "running" for row in rows)
    own = [{"task_id": row["task_id"], "status": row["status"], "ahead": index}
           for index, row in enumerate(rows) if row["owner_user_id"] == owner_user_id]
    return {"running": running, "waiting": sum(row["status"] == "queued" for row in rows),
            "own_tasks": own}
