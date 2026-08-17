"""Task-scoped artifacts for resumable workflow stages.

Unlike the cross-task artifact cache, this module records the concrete outputs
produced for one task. It is the audit trail and resume index for long report
generation jobs.
"""
from __future__ import annotations

import json
from typing import Any

from app.cache import stable_hash
from app.db import session_scope
from app.infrastructure.orm import ORMTaskArtifact
from sqlalchemy import select

TASK_ARTIFACT_SCHEMA_VERSION = "task-artifact-1"


def artifact_input_hash(stage: str, effective_inputs: Any) -> str:
    return stable_hash({
        "schema": TASK_ARTIFACT_SCHEMA_VERSION,
        "stage": stage,
        "inputs": effective_inputs,
    })


def save_task_artifact(task_id: str, stage: str, effective_inputs: Any,
                       payload: Any, status: str = "done") -> str:
    """Persist a stage artifact and return its dependency hash."""
    input_hash = artifact_input_hash(stage, effective_inputs)
    with session_scope() as s:
        exists = s.execute(
            select(ORMTaskArtifact.c.id).where(
                ORMTaskArtifact.c.task_id == task_id,
                ORMTaskArtifact.c.stage == stage,
                ORMTaskArtifact.c.input_hash == input_hash,
            )
        ).first()
        values = dict(task_id=task_id, stage=stage, input_hash=input_hash,
                      status=status, payload=json.dumps(payload, ensure_ascii=False))
        if exists:
            s.execute(
                ORMTaskArtifact.update()
                .where(ORMTaskArtifact.c.task_id == task_id,
                       ORMTaskArtifact.c.stage == stage,
                       ORMTaskArtifact.c.input_hash == input_hash)
                .values(status=status, payload=values["payload"])
            )
        else:
            s.execute(ORMTaskArtifact.insert().values(**values))
    return input_hash


def latest_task_artifact(task_id: str, stage: str) -> dict | None:
    """Return the latest artifact for a task stage, if present."""
    with session_scope() as s:
        row = s.execute(
            select(ORMTaskArtifact).where(
                ORMTaskArtifact.c.task_id == task_id, ORMTaskArtifact.c.stage == stage
            ).order_by(ORMTaskArtifact.c.updated_at.desc(), ORMTaskArtifact.c.id.desc()).limit(1)
        ).mappings().first()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "stage": row["stage"],
        "input_hash": row["input_hash"],
        "status": row["status"],
        "payload": payload,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_task_artifacts(task_id: str) -> list[dict]:
    with session_scope() as s:
        rows = s.execute(
            select(ORMTaskArtifact.c.stage, ORMTaskArtifact.c.input_hash,
                   ORMTaskArtifact.c.status, ORMTaskArtifact.c.payload,
                   ORMTaskArtifact.c.updated_at)
            .where(ORMTaskArtifact.c.task_id == task_id).order_by(ORMTaskArtifact.c.id)
            (task_id,),
        ).fetchall()
    artifacts: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        artifacts.append({
            "stage": row["stage"],
            "input_hash": row["input_hash"],
            "status": row["status"],
            "payload": payload,
            "updated_at": row["updated_at"],
        })
    return artifacts
