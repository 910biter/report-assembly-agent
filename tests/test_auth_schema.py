from app.infrastructure.orm import Base


def test_account_schema_has_unique_identity_and_revocable_sessions():
    users = Base.metadata.tables["users"]
    sessions = Base.metadata.tables["user_sessions"]
    assert users.c.id.primary_key
    assert users.c.username.unique
    assert not users.c.password_hash.nullable
    assert not users.c.disabled.nullable
    assert sessions.c.token_hash.unique
    assert not sessions.c.expires_at.nullable
    assert not sessions.c.revoked.nullable


def test_root_resources_have_ownership_columns():
    for name in ("short_memory", "materials", "style_variants", "interaction_threads", "reports"):
        assert "owner_user_id" in Base.metadata.tables[name].c


def test_material_fingerprint_is_not_globally_unique():
    materials = Base.metadata.tables["materials"]
    assert not materials.c.fingerprint.unique
