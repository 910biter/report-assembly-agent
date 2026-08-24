"""Persistent execution rounds for one business task.

Task is the durable user workspace. TaskRun is one reproducible execution of
that task, so artifacts and performance data never have to infer a revision
from mutable short-term memory.
"""
from __future__ import annotations

import json
import time
import uuid

from sqlalchemy import insert, select, update

from app.db import session_scope
from app.infrastructure.orm import ORMTaskRun


def create_task_run(task_id: str, revision: int, run_mode: str = "initial",
                    base_version_id: int | None = None, update_reason: str = "") -> str:
    run_id = uuid.uuid4().hex[:16]
    with session_scope() as s:
        s.execute(insert(ORMTaskRun).values(
            run_id=run_id,
            task_id=task_id,
            revision=int(revision),
            run_mode=run_mode,
            status="created",
            base_version_id=base_version_id,
            update_reason=update_reason,
            metadata_json="{}",
        ))
    return run_id


def update_task_run(run_id: str, *, status: str | None = None,
                    candidate_version_id: int | None = None, metadata: dict | None = None,
                    finished: bool = False) -> None:
    if not run_id:
        return
    values = {}
    if status is not None:
        values["status"] = status
    if candidate_version_id is not None:
        values["candidate_version_id"] = int(candidate_version_id)
    if metadata is not None:
        values["metadata_json"] = json.dumps(metadata, ensure_ascii=False)
    if finished:
        values["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if values:
        with session_scope() as s:
            s.execute(update(ORMTaskRun).where(ORMTaskRun.c.run_id == run_id).values(**values))


def get_task_run(run_id: str) -> dict | None:
    with session_scope() as s:
        row = s.execute(select(ORMTaskRun).where(ORMTaskRun.c.run_id == run_id)).mappings().first()
    if row is None:
        return None
    result = dict(row)
    try:
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
    except (TypeError, ValueError):
        result["metadata"] = {}
    return result

