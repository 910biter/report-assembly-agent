"""Run only with an explicitly isolated IRA_TEST_DB_URL."""

import os

import pytest
from sqlalchemy import insert, select


@pytest.mark.skipif(not os.environ.get("IRA_TEST_DB_URL"), reason="isolated PostgreSQL required")
def test_first_admin_claims_existing_roots_and_sessions_are_revocable(monkeypatch):
    from app import db
    from app.auth.accounts import bootstrap_admin, create_user, disable_user, assert_multiuser_data_ready
    from app.auth.sessions import create_session, resolve_session
    from app.infrastructure.orm import Base

    monkeypatch.setattr(db.settings, "db_url", os.environ["IRA_TEST_DB_URL"])
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    users = Base.metadata.tables["users"]
    tasks = Base.metadata.tables["short_memory"]
    materials = Base.metadata.tables["materials"]
    with db.session_scope() as s:
        assert s.execute(select(users.c.id)).first() is None
        s.execute(insert(tasks).values(task_id="legacy-task", payload="{}"))
        s.execute(insert(materials).values(
            filename="old.pdf", file_type="pdf", path="/tmp/old.pdf",
            fingerprint="legacy-hash", file_hash="legacy-hash",
        ))
    with pytest.raises(RuntimeError, match="MULTIUSER_ADMIN_NOT_BOOTSTRAPPED"):
        assert_multiuser_data_ready()
    admin = bootstrap_admin("Admin", "correct horse battery staple")
    assert admin.role == "admin"
    assert_multiuser_data_ready()
    with db.session_scope() as s:
        assert s.execute(select(tasks.c.owner_user_id).where(
            tasks.c.task_id == "legacy-task",
        )).scalar_one() == admin.user_id
        assert s.execute(select(materials.c.owner_user_id).where(
            materials.c.fingerprint == "legacy-hash",
        )).scalar_one() == admin.user_id
    user = create_user("alice", "another correct horse battery")
    from app.api.routes import _collect_material_ids
    with db.session_scope() as s:
        material_id = s.execute(select(materials.c.id).where(
            materials.c.fingerprint == "legacy-hash",
            materials.c.owner_user_id == admin.user_id,
        )).scalar_one()
    assert _collect_material_ids("new-task", str(material_id), owner_user_id=admin.user_id) == [material_id]
    with pytest.raises(ValueError, match="MATERIAL_NOT_FOUND"):
        _collect_material_ids("new-task", str(material_id), owner_user_id=user.user_id)
    with db.session_scope() as s:
        other_id = s.execute(insert(materials).values(
            owner_user_id=user.user_id, filename="alice.pdf", file_type="pdf",
            path="/tmp/alice.pdf", fingerprint="legacy-hash", file_hash="legacy-hash",
        )).inserted_primary_key[0]
    assert _collect_material_ids("new-task", str(other_id), owner_user_id=user.user_id) == [other_id]
    token = create_session(user)
    assert resolve_session(token) == user
    disable_user(user.user_id)
    assert resolve_session(token) is None
