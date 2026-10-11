"""Opaque, revocable browser sessions."""

from __future__ import annotations

import hashlib
import secrets
import time

from sqlalchemy import insert, select, update

from app.auth.accounts import Principal
from app.auth.passwords import verify_password
from app.db import session_scope
from app.infrastructure.orm import Base

_USERS = Base.metadata.tables["users"]
_SESSIONS = Base.metadata.tables["user_sessions"]
SESSION_SECONDS = 12 * 60 * 60


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def authenticate(username: str, password: str) -> Principal | None:
    normalized = username.strip().casefold()
    with session_scope() as s:
        row = s.execute(select(_USERS).where(_USERS.c.username == normalized)).mappings().first()
    if row is None or bool(row["disabled"]):
        return None
    if not verify_password(password, str(row["password_hash"])):
        return None
    return Principal(int(row["id"]), str(row["username"]), str(row["role"]))


def create_session(principal: Principal) -> str:
    token = secrets.token_urlsafe(48)
    with session_scope() as s:
        s.execute(insert(_SESSIONS).values(
            user_id=principal.user_id,
            token_hash=session_token_hash(token),
            expires_at=time.time() + SESSION_SECONDS,
        ))
    return token


def resolve_session(token: str) -> Principal | None:
    if not token or len(token) > 256:
        return None
    try:
        digest = session_token_hash(token)
    except UnicodeEncodeError:
        return None
    with session_scope() as s:
        row = s.execute(
            select(_USERS.c.id, _USERS.c.username, _USERS.c.role)
            .select_from(_SESSIONS.join(_USERS, _SESSIONS.c.user_id == _USERS.c.id))
            .where(
                _SESSIONS.c.token_hash == digest,
                _SESSIONS.c.revoked == 0,
                _SESSIONS.c.expires_at > time.time(),
                _USERS.c.disabled == 0,
            )
        ).mappings().first()
    if row is None:
        return None
    return Principal(int(row["id"]), str(row["username"]), str(row["role"]))


def revoke_session(token: str) -> None:
    if not token:
        return
    try:
        digest = session_token_hash(token)
    except UnicodeEncodeError:
        return
    with session_scope() as s:
        s.execute(update(_SESSIONS).where(_SESSIONS.c.token_hash == digest).values(revoked=1))
