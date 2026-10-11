import os
from uuid import uuid4

import pytest


@pytest.mark.skipif(not os.environ.get("IRA_TEST_DB_URL"), reason="isolated PostgreSQL required")
def test_password_change_revokes_sessions_and_preserves_other_account(monkeypatch):
    from app import db
    from app.auth.accounts import change_own_password, create_user
    from app.auth.sessions import authenticate, create_session, resolve_session

    monkeypatch.setattr(db.settings, "db_url", os.environ["IRA_TEST_DB_URL"])
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    user = create_user("self" + uuid4().hex[:8], "initial-password-long")
    other = create_user("else" + uuid4().hex[:8], "another-password-long")
    token = create_session(user)
    other_token = create_session(other)
    with pytest.raises(ValueError, match="INVALID_CURRENT_PASSWORD"):
        change_own_password(user.user_id, "wrong-password", "replacement-password-long")
    assert resolve_session(token) == user
    with pytest.raises(ValueError, match="PASSWORD_TOO_SHORT"):
        change_own_password(user.user_id, "initial-password-long", "short")
    assert resolve_session(token) == user
    change_own_password(user.user_id, "initial-password-long", "replacement-password-long")
    assert resolve_session(token) is None
    assert resolve_session(other_token) == other
    assert authenticate(user.username, "initial-password-long") is None
    assert authenticate(user.username, "replacement-password-long") == user


@pytest.mark.skipif(not os.environ.get("IRA_TEST_DB_URL"), reason="isolated PostgreSQL required")
def test_personal_queue_snapshot_hides_other_tasks(monkeypatch):
    from sqlalchemy import delete
    from app import db
    from app.auth.accounts import create_user
    from app.workflow.queue_store import enqueue, personal_snapshot
    from app.infrastructure.orm import Base

    monkeypatch.setattr(db.settings, "db_url", os.environ["IRA_TEST_DB_URL"])
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    with db.session_scope() as s:
        s.execute(delete(Base.metadata.tables["task_queue_entries"]))
    foreign_user = create_user("foreign" + uuid4().hex[:8], "foreign-password-long")
    own_user = create_user("queue" + uuid4().hex[:8], "queue-password-long")
    foreign_task, own_task = uuid4().hex, uuid4().hex
    enqueue(foreign_task, "run", foreign_user.user_id)
    enqueue(own_task, "run", own_user.user_id)
    snapshot = personal_snapshot(own_user.user_id)
    assert snapshot["waiting"] == 2
    assert snapshot["own_tasks"] == [{"task_id": own_task, "status": "queued", "ahead": 1}]
    assert foreign_task not in str(snapshot)
