"""Persistent login failure throttling by account and direct client address."""

from __future__ import annotations

import hashlib
import time

from sqlalchemy import delete, func, select, update

from app.db import session_scope
from app.infrastructure.orm import Base

_ATTEMPTS = Base.metadata.tables["auth_login_attempts"]
_WINDOW_SECONDS = 900


def _keys(username: str, address: str) -> tuple[str, str]:
    normalized = username.strip().casefold()
    return "user:" + hashlib.sha256(normalized.encode()).hexdigest(), "ip:" + hashlib.sha256(address.encode()).hexdigest()


def is_blocked(username: str, address: str) -> bool:
    now = time.time()
    with session_scope() as session:
        rows = session.execute(select(_ATTEMPTS.c.blocked_until).where(
            _ATTEMPTS.c.attempt_key.in_(_keys(username, address)),
        )).scalars().all()
    return any(float(until) > now for until in rows)


def record_failure(username: str, address: str) -> None:
    now = time.time()
    with session_scope() as session:
        for key, limit in zip(_keys(username, address), (5, 30)):
            lock_key = int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big", signed=True)
            session.execute(select(func.pg_advisory_xact_lock(lock_key)))
            row = session.execute(select(_ATTEMPTS).where(
                _ATTEMPTS.c.attempt_key == key,
            ).with_for_update()).mappings().first()
            attempts = int(row["attempts"]) + 1 if row and now - float(row["window_started"]) < _WINDOW_SECONDS else 1
            values = {
                "attempts": attempts,
                "window_started": float(row["window_started"]) if row and attempts > 1 else now,
                "blocked_until": now + _WINDOW_SECONDS if attempts >= limit else 0,
            }
            if row:
                session.execute(update(_ATTEMPTS).where(_ATTEMPTS.c.attempt_key == key).values(**values))
            else:
                session.execute(_ATTEMPTS.insert().values(attempt_key=key, **values))


def clear_user(username: str) -> None:
    with session_scope() as session:
        session.execute(delete(_ATTEMPTS).where(_ATTEMPTS.c.attempt_key == _keys(username, "")[0]))
