"""PostgreSQL account administration; no public registration path."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func, insert, select, update

from app.auth.passwords import hash_password, verify_password
from app.db import session_scope
from app.infrastructure.orm import Base

_USERS = Base.metadata.tables["users"]
_SESSIONS = Base.metadata.tables["user_sessions"]
_AUDIT = Base.metadata.tables["account_audit_events"]
_USERNAME = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
_OWNED_ROOTS = ("short_memory", "materials", "style_variants", "interaction_threads", "reports")


@dataclass(frozen=True)
class Principal:
    user_id: int
    username: str
    role: str


def normalize_username(username: str) -> str:
    value = username.strip().casefold()
    if not _USERNAME.fullmatch(value):
        raise ValueError("INVALID_USERNAME")
    return value


def bootstrap_admin(username: str, password: str) -> Principal:
    """Create the only initial admin, never an implicit default credential."""
    normalized = normalize_username(username)
    password_hash = hash_password(password)
    with session_scope() as s:
        s.execute(select(func.pg_advisory_xact_lock(782340998921)))
        existing = s.execute(select(_USERS.c.id).limit(1)).first()
        if existing:
            raise ValueError("ACCOUNTS_ALREADY_INITIALIZED")
        result = s.execute(insert(_USERS).values(
            username=normalized, password_hash=password_hash, role="admin",
        ))
        user_id = int(result.inserted_primary_key[0])
        for name in _OWNED_ROOTS:
            table = Base.metadata.tables[name]
            s.execute(update(table).where(table.c.owner_user_id.is_(None)).values(owner_user_id=user_id))
    return Principal(user_id, normalized, "admin")


def backfill_existing_to_first_admin() -> int:
    """Explicit idempotent migration for rows created before auth is enabled."""
    with session_scope() as s:
        admins = s.execute(select(_USERS.c.id).where(
            _USERS.c.role == "admin", _USERS.c.disabled == 0,
        ).order_by(_USERS.c.id)).all()
        if not admins:
            raise ValueError("ADMIN_REQUIRED_FOR_BACKFILL")
        admin_id = int(admins[0].id)
        changed = 0
        for name in _OWNED_ROOTS:
            table = Base.metadata.tables[name]
            result = s.execute(update(table).where(
                table.c.owner_user_id.is_(None),
            ).values(owner_user_id=admin_id))
            changed += result.rowcount
    return changed


def assert_multiuser_data_ready() -> None:
    """Refuse a tenant-enabled service before the explicit ownership migration."""
    with session_scope() as s:
        admin = s.execute(select(_USERS.c.id).where(
            _USERS.c.role == "admin", _USERS.c.disabled == 0,
        ).limit(1)).first()
        if admin is None:
            raise RuntimeError("MULTIUSER_ADMIN_NOT_BOOTSTRAPPED")
        for name in _OWNED_ROOTS:
            table = Base.metadata.tables[name]
            if s.execute(select(table.c.owner_user_id).where(
                table.c.owner_user_id.is_(None),
            ).limit(1)).first() is not None:
                raise RuntimeError(f"MULTIUSER_OWNER_MIGRATION_REQUIRED:{name}")


def create_user(username: str, password: str, *, role: str = "user",
                actor_user_id: int | None = None) -> Principal:
    if role != "user":
        raise ValueError("ADMIN_CREATION_REQUIRES_BOOTSTRAP")
    normalized = normalize_username(username)
    password_hash = hash_password(password)
    with session_scope() as s:
        result = s.execute(insert(_USERS).values(
            username=normalized, password_hash=password_hash, role=role,
        ))
        user_id = int(result.inserted_primary_key[0])
        if actor_user_id is not None:
            s.execute(insert(_AUDIT).values(actor_user_id=actor_user_id,
                                            target_user_id=user_id, action="create_user"))
    return Principal(user_id, normalized, role)


def disable_user(user_id: int, *, actor_user_id: int | None = None) -> None:
    with session_scope() as s:
        row = s.execute(select(_USERS.c.role).where(_USERS.c.id == user_id)).first()
        if row is None:
            raise ValueError("USER_NOT_FOUND")
        if row.role == "admin":
            raise ValueError("CANNOT_DISABLE_ADMIN")
        s.execute(update(_USERS).where(_USERS.c.id == user_id).values(disabled=1))
        s.execute(update(_SESSIONS).where(_SESSIONS.c.user_id == user_id).values(revoked=1))
        if actor_user_id is not None:
            s.execute(insert(_AUDIT).values(actor_user_id=actor_user_id,
                                            target_user_id=user_id, action="disable_user"))


def reset_password(user_id: int, password: str, *, actor_user_id: int | None = None) -> None:
    password_hash = hash_password(password)
    with session_scope() as s:
        result = s.execute(update(_USERS).where(_USERS.c.id == user_id).values(
            password_hash=password_hash,
        ))
        if result.rowcount != 1:
            raise ValueError("USER_NOT_FOUND")
        s.execute(update(_SESSIONS).where(_SESSIONS.c.user_id == user_id).values(revoked=1))
        if actor_user_id is not None:
            s.execute(insert(_AUDIT).values(actor_user_id=actor_user_id,
                                            target_user_id=user_id, action="reset_password"))


def list_users() -> list[dict]:
    with session_scope() as s:
        rows = s.execute(select(
            _USERS.c.id, _USERS.c.username, _USERS.c.role, _USERS.c.disabled,
            _USERS.c.created_at,
        ).order_by(_USERS.c.id)).mappings().all()
    return [dict(row) for row in rows]


def change_own_password(user_id: int, current_password: str, new_password: str) -> None:
    with session_scope() as s:
        row = s.execute(select(_USERS.c.password_hash).where(
            _USERS.c.id == user_id, _USERS.c.disabled == 0,
        ).with_for_update()).first()
        if row is None or not verify_password(current_password, row.password_hash):
            raise ValueError("INVALID_CURRENT_PASSWORD")
        password_hash = hash_password(new_password)
        s.execute(update(_USERS).where(_USERS.c.id == user_id).values(password_hash=password_hash))
        s.execute(update(_SESSIONS).where(_SESSIONS.c.user_id == user_id).values(revoked=1))
        s.execute(insert(_AUDIT).values(actor_user_id=user_id, target_user_id=user_id,
                                      action="change_own_password"))
