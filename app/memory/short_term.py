"""短期记忆:当前任务上下文(task_id → payload JSON)。"""
import json

from app.db import session_scope
from app.infrastructure.orm import ORMShortMemory
from sqlalchemy import delete, select


def save_task(task_id: str, payload: dict) -> None:
    with session_scope() as s:
        row = s.execute(
            select(ORMShortMemory.c.task_id).where(ORMShortMemory.c.task_id == task_id)
        ).first()
        if row:
            s.execute(
                ORMShortMemory.update()
                .where(ORMShortMemory.c.task_id == task_id)
                .values(payload=json.dumps(payload, ensure_ascii=False))
            )
        else:
            s.execute(
                ORMShortMemory.insert().values(
                    task_id=task_id, payload=json.dumps(payload, ensure_ascii=False)
                )
            )


def load_task(task_id: str) -> dict | None:
    with session_scope() as s:
        row = s.execute(
            select(ORMShortMemory.c.payload).where(ORMShortMemory.c.task_id == task_id)
        ).first()
    return json.loads(row[0]) if row else None


def delete_task(task_id: str) -> None:
    with session_scope() as s:
        s.execute(delete(ORMShortMemory).where(ORMShortMemory.c.task_id == task_id))


def update_task(task_id: str, **fields) -> dict:
    """按字段增量更新任务 payload(读-改-写,保留未涉及的字段)。"""
    payload = load_task(task_id) or {}
    payload.update(fields)
    save_task(task_id, payload)
    return payload
