"""Task-scoped artifacts for resumable workflow stages.

Unlike the cross-task artifact cache, this module records the concrete outputs
produced for one task. It is the audit trail and resume index for long report
generation jobs.
"""
from __future__ import annotations

import json
from typing import Any

from app.cache import stable_hash
from app.db import connect

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
    with connect() as conn:
        conn.execute(
            "INSERT INTO task_artifacts(task_id, stage, input_hash, status, payload) "
            "VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(task_id, stage, input_hash) DO UPDATE SET "
            "status=excluded.status, payload=excluded.payload, updated_at=datetime('now')",
            (
                task_id,
                stage,
                input_hash,
                status,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
    return input_hash


def latest_task_artifact(task_id: str, stage: str) -> dict | None:
    """Return the latest artifact for a task stage, if present."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM task_artifacts WHERE task_id=? AND stage=? "
            "ORDER BY updated_at DESC, id DESC LIMIT 1",
            (task_id, stage),
        ).fetchone()
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
    with connect() as conn:
        rows = conn.execute(
            "SELECT stage, input_hash, status, payload, updated_at "
            "FROM task_artifacts WHERE task_id=? ORDER BY id",
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
