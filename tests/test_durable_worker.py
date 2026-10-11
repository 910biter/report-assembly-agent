import os
from uuid import uuid4

import pytest


@pytest.mark.skipif(not os.environ.get("IRA_TEST_DB_URL"), reason="isolated PostgreSQL required")
def test_durable_worker_claims_and_finishes_while_preserving_fifo(monkeypatch):
    from app import db
    from app.auth.accounts import create_user
    from app.memory import short_term
    from app.workflow import queue, queue_store
    from app.workflow import controller

    monkeypatch.setattr(db.settings, "db_url", os.environ["IRA_TEST_DB_URL"])
    monkeypatch.setattr(queue.settings, "multiuser_enabled", True)
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    from sqlalchemy import delete
    from app.db import session_scope
    from app.infrastructure.orm import Base
    with session_scope() as session:
        session.execute(delete(Base.metadata.tables["task_queue_entries"]))
    owners = [create_user("queue" + uuid4().hex[:8], "a-long-test-password") for _ in range(2)]
    ids = [uuid4().hex for _ in range(3)]
    for task_id, owner in zip(ids, [owners[0], owners[1], owners[0]]):
        short_term.create_task(task_id, {"stage": "created", "run_id": task_id}, owner_user_id=owner.user_id)
        queue_store.enqueue(task_id, task_id, owner.user_id)

    seen = []

    class FakeController:
        def __init__(self, task_id):
            self.task_id = task_id

        def run_to_review(self):
            seen.append(self.task_id)
            short_term.update_task(self.task_id, stage="review")
            return "review"

    monkeypatch.setattr(controller, "WorkflowController", FakeController)
    assert queue._durable_claim_and_run()
    assert queue_store.position(ids[1]) == 0
    assert queue_store.position(ids[2]) == 1
    assert queue._durable_claim_and_run()
    assert queue._durable_claim_and_run()
    assert seen == ids
    assert queue_store.position(ids[0]) is None
    assert queue_store.position(ids[1]) is None
    assert queue_store.position(ids[2]) is None
