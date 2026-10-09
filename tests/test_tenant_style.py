import os
from uuid import uuid4

import pytest


@pytest.mark.skipif(not os.environ.get("IRA_TEST_DB_URL"), reason="isolated PostgreSQL required")
def test_template_reuse_and_locked_selection_are_per_owner(monkeypatch):
    from app import db
    from app.auth.accounts import create_user
    from app.infrastructure.orm import ORMVariant
    from app.memory import style

    monkeypatch.setattr(db.settings, "db_url", os.environ["IRA_TEST_DB_URL"])
    monkeypatch.setattr(style.settings, "multiuser_enabled", True)
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    first = create_user("style" + uuid4().hex[:8], "a-long-test-password")
    second = create_user("style" + uuid4().hex[:8], "a-long-test-password")
    report = {"filename": "shared.docx", "asset_role": "auto"}
    library_id = style._ensure_library()
    with db.session_scope() as session:
        session.execute(ORMVariant.insert().values(
            owner_user_id=first.user_id, library_id=library_id,
            name="private", source_hash=style._source_hash(report), status="locked",
        ))
    assert style._reuse_by_source_hash([report], owner_user_id=first.user_id)
    assert style._reuse_by_source_hash([report], owner_user_id=second.user_id) is None
    assert style.get_locked_variant(owner_user_id=first.user_id).name == "private"
    assert style.get_locked_variant(owner_user_id=second.user_id) is None
    assert [v.name for v in style.list_variants(owner_user_id=second.user_id)] == []
    with db.session_scope() as session:
        result = session.execute(ORMVariant.insert().values(
            owner_user_id=second.user_id, library_id=library_id,
            name="second", status="draft",
        ))
        second_variant_id = int(result.inserted_primary_key[0])
    style.lock_variant(second_variant_id)
    assert style.get_locked_variant(owner_user_id=first.user_id).name == "private"
    assert style.get_locked_variant(owner_user_id=second.user_id).name == "second"
