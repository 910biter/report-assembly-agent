"""Browser mutation guard for session-cookie authentication."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from app.config import settings


def validate_mutation_origin(request: Request) -> None:
    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    origin = request.headers.get("origin", "")
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=403, detail="ORIGIN_REQUIRED")
    allowed = {str(request.base_url).rstrip("/")}
    allowed.update(item.strip().rstrip("/") for item in settings.auth_allowed_origins.split(",") if item.strip())
    if f"{parsed.scheme}://{parsed.netloc}" not in allowed:
        raise HTTPException(status_code=403, detail="ORIGIN_NOT_ALLOWED")
