"""短期记忆:当前任务上下文(task_id → payload JSON)。"""
import json
from contextlib import nullcontext
from typing import Callable

from app.db import session_scope
from app.infrastructure.orm import ORMShortMemory
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert


def _session_manager(_session):
    return session_scope() if _session is None else nullcontext(_session)


def create_task(task_id: str, payload: dict, *, _session=None) -> None:
    """Insert a new task payload without ever replacing an existing task."""
    with _session_manager(_session) as s:
        existing = s.execute(
            select(ORMShortMemory.c.task_id).where(ORMShortMemory.c.task_id == task_id)
        ).first()
        if existing is not None:
            raise ValueError("TASK_ALREADY_EXISTS")
        s.execute(
            ORMShortMemory.insert().values(
                task_id=task_id,
                payload=json.dumps(payload, ensure_ascii=False),
            )
        )


def load_task(task_id: str) -> dict | None:
    with session_scope() as s:
        row = s.execute(
            select(ORMShortMemory.c.payload).where(ORMShortMemory.c.task_id == task_id)
        ).first()
    return json.loads(row[0]) if row else None


def mutate_task(task_id: str, mutator: Callable[[dict], dict | None], *,
                create: bool = False, _session=None) -> dict:
    """Read, mutate, and persist one payload under a PostgreSQL row lock."""
    return transition_task(
        task_id,
        lambda payload, _tx: mutator(payload),
        create=create,
        _session=_session,
    )


def transition_task(task_id: str, transition: Callable[[dict, object], dict | None], *,
                    create: bool = False, _session=None) -> dict:
    """Run a short database transition and payload write in the same transaction.

    The callback receives the freshly locked payload and the active SQLAlchemy
    session, allowing related rows (for example TaskRun and Delta) to commit or
    roll back together with the payload state change.
    """
    statement = (
        select(ORMShortMemory.c.payload)
        .where(ORMShortMemory.c.task_id == task_id)
        .with_for_update()
    )
    with _session_manager(_session) as s:
        row = s.execute(statement).first()
        if row is None and create:
            s.execute(
                pg_insert(ORMShortMemory)
                .values(task_id=task_id, payload="{}")
                .on_conflict_do_nothing(index_elements=[ORMShortMemory.c.task_id])
            )
            row = s.execute(statement).first()
        if row is None:
            raise KeyError("TASK_NOT_FOUND")
        current = json.loads(row[0])
        updated = transition(current, s)
        payload = current if updated is None else updated
        if not isinstance(payload, dict):
            raise TypeError("TASK_PAYLOAD_MUST_BE_OBJECT")
        s.execute(
            ORMShortMemory.update()
            .where(ORMShortMemory.c.task_id == task_id)
            .values(payload=json.dumps(payload, ensure_ascii=False))
        )
    return payload


def update_task(task_id: str, **fields) -> dict:
    """Atomically shallow-merge task fields under the task row lock."""
    return mutate_task(
        task_id,
        lambda payload: payload.update(fields),
        create=True,
    )


def list_tasks() -> list[tuple[str, dict]]:
    """Return persisted task payloads for process-level state reconciliation."""
    with session_scope() as s:
        rows = s.execute(
            select(ORMShortMemory.c.task_id, ORMShortMemory.c.payload)
        ).all()
    return [(str(row[0]), json.loads(row[1])) for row in rows]


def delete_task(task_id: str, *, _session=None) -> None:
    with _session_manager(_session) as s:
        s.execute(delete(ORMShortMemory).where(ORMShortMemory.c.task_id == task_id))
