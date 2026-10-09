import os
from uuid import uuid4

import pytest


def _clear_isolated_queue():
    from sqlalchemy import delete
    from app.db import session_scope
    from app.infrastructure.orm import Base
    with session_scope() as session:
        session.execute(delete(Base.metadata.tables["task_queue_entries"]))


@pytest.mark.skipif(not os.environ.get("IRA_TEST_DB_URL"), reason="isolated PostgreSQL required")
def test_fifo_position_cancel_and_claim(monkeypatch):
    from app import db
    from app.workflow.queue_store import (
        claim_next, enqueue, finish, position, cancel,
    )

    monkeypatch.setattr(db.settings, "db_url", os.environ["IRA_TEST_DB_URL"])
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    _clear_isolated_queue()
    prefix = uuid4().hex[:8]
    first, second, third = [f"{prefix}-{index}" for index in range(3)]
    first_entry = enqueue(first, "run-1", None)
    duplicate = enqueue(first, "run-1", None)
    assert duplicate["id"] == first_entry["id"]
    assert duplicate["duplicate"]
    enqueue(second, "run-1", None)
    enqueue(third, "run-1", None)
    assert position(third) == 2
    claimed = claim_next()
    assert claimed["task_id"] == first
    assert claim_next() is None
    assert position(third) == 2
    cancel(second)
    assert position(third) == 1
    finish(claimed["id"], "completed")
    assert position(third) == 0
    claimed_third = claim_next()
    assert claimed_third["task_id"] == third
    finish(claimed_third["id"], "completed")


@pytest.mark.skipif(not os.environ.get("IRA_TEST_DB_URL"), reason="isolated PostgreSQL required")
def test_one_active_job_per_task_and_restart_reconciliation(monkeypatch):
    from app import db
    from app.memory import short_term
    from app.workflow.queue_store import enqueue, claim_next, finish, position, reconcile_running

    monkeypatch.setattr(db.settings, "db_url", os.environ["IRA_TEST_DB_URL"])
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    _clear_isolated_queue()
    task_id = uuid4().hex
    short_term.create_task(task_id, {"stage": "created"})
    first = enqueue(task_id, "run-1", None)
    assert enqueue(task_id, "run-2", None)["id"] == first["id"]
    running = claim_next()
    assert running["id"] == first["id"]
    assert enqueue(task_id, "run-3", None)["id"] == first["id"]
    assert reconcile_running() == [task_id]
    assert position(task_id) is None
    queued = enqueue(task_id, "run-4", None)
    assert queued["id"] != first["id"]
    claimed = claim_next()
    assert claimed["id"] == queued["id"]
    finish(claimed["id"], "completed")


@pytest.mark.skipif(not os.environ.get("IRA_TEST_DB_URL"), reason="isolated PostgreSQL required")
def test_user_queue_quota_counts_running_and_queued(monkeypatch):
    from app import db
    from app.memory import short_term
    from app.auth.accounts import create_user
    from app.workflow import queue_store

    monkeypatch.setattr(db.settings, "db_url", os.environ["IRA_TEST_DB_URL"])
    monkeypatch.setattr(queue_store.settings, "max_active_jobs_per_user", 1)
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    _clear_isolated_queue()
    owner_id = create_user("quota" + uuid4().hex[:8], "a-long-test-password").user_id
    first, second = uuid4().hex, uuid4().hex
    short_term.create_task(first, {"stage": "created"}, owner_user_id=owner_id)
    short_term.create_task(second, {"stage": "created"}, owner_user_id=owner_id)
    queue_store.enqueue(first, "run-1", owner_id)
    assert queue_store.enqueue(first, "run-2", owner_id)["duplicate"]
    with pytest.raises(ValueError, match="USER_QUEUE_LIMIT_REACHED"):
        queue_store.enqueue(second, "run-1", owner_id)
    claimed = queue_store.claim_next()
    assert claimed["task_id"] == first
    with pytest.raises(ValueError, match="USER_QUEUE_LIMIT_REACHED"):
        queue_store.enqueue(second, "run-1", owner_id)
    queue_store.finish(claimed["id"], "completed")
    assert queue_store.enqueue(second, "run-1", owner_id)["status"] == "queued"
    queue_store.cancel(second)
