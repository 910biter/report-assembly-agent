import os

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _request(origin: str | None, method: str = "POST") -> Request:
    headers = [(b"host", b"100.120.119.235:8000")]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request({
        "type": "http", "method": method, "scheme": "http", "path": "/api/tasks",
        "query_string": b"", "headers": headers,
        "client": ("100.120.119.2", 12345), "server": ("100.120.119.235", 8000),
    })


def test_mutation_origin_must_match_work_service():
    from app.auth.guards import validate_mutation_origin

    validate_mutation_origin(_request("http://100.120.119.235:8000"))
    validate_mutation_origin(_request(None, method="GET"))
    for origin in (None, "http://evil.example", "http://100.120.119.235:8001"):
        with pytest.raises(HTTPException) as exc:
            validate_mutation_origin(_request(origin))
        assert exc.value.status_code == 403


@pytest.mark.skipif(not os.environ.get("IRA_TEST_DB_URL"), reason="isolated PostgreSQL required")
def test_login_throttle_is_persistent_and_resets_window(monkeypatch):
    from app import db
    from app.auth import throttle

    monkeypatch.setattr(db.settings, "db_url", os.environ["IRA_TEST_DB_URL"])
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    username = "failed-login-" + os.urandom(5).hex()
    for _ in range(4):
        assert not throttle.is_blocked(username, "127.0.0.9")
        throttle.record_failure(username, "127.0.0.9")
    throttle.record_failure(username, "127.0.0.9")
    assert throttle.is_blocked(username, "127.0.0.9")
    assert throttle.is_blocked(username, "127.0.0.10")
    throttle.clear_user(username)
    assert not throttle.is_blocked(username, "127.0.0.9")
