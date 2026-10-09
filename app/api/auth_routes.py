"""Administrator-provisioned account endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.auth.accounts import (
    Principal, change_own_password, create_user, disable_user, list_users, reset_password,
)
from app.auth.ownership import require_admin
from app.auth.guards import validate_mutation_origin
from app.auth import throttle
from app.auth.sessions import (
    SESSION_SECONDS, authenticate, create_session, resolve_session, revoke_session,
)
from app.config import settings
from app.db import session_scope
from app.infrastructure.orm import Base

router = APIRouter(prefix="/api")
_COOKIE = "ira_session"
_PREFERENCES = Base.metadata.tables["user_preferences"]
_PAGE_SIZE_KEYS = {"page_size:tasks", "page_size:materials", "page_size:analysis"}


class LoginBody(BaseModel):
    username: str
    password: str


class CreateUserBody(BaseModel):
    username: str
    password: str


class ResetPasswordBody(BaseModel):
    password: str


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


class PreferenceBody(BaseModel):
    value: int


def _page_size_key(key: str) -> str:
    if key not in _PAGE_SIZE_KEYS:
        raise HTTPException(status_code=404, detail="PREFERENCE_NOT_FOUND")
    return key


def current_principal(request: Request) -> Principal:
    if not settings.multiuser_enabled:
        raise HTTPException(status_code=503, detail="MULTIUSER_NOT_ENABLED")
    validate_mutation_origin(request)
    principal = resolve_session(request.cookies.get(_COOKIE, ""))
    if principal is None:
        raise HTTPException(status_code=401, detail="LOGIN_REQUIRED")
    return principal


def admin_principal(principal: Principal = Depends(current_principal)) -> Principal:
    require_admin(principal)
    return principal


@router.post("/auth/login")
def login(body: LoginBody, response: Response, request: Request):
    if not settings.multiuser_enabled:
        raise HTTPException(status_code=503, detail="MULTIUSER_NOT_ENABLED")
    validate_mutation_origin(request)
    address = request.client.host if request.client else "unknown"
    if throttle.is_blocked(body.username, address):
        raise HTTPException(status_code=429, detail="LOGIN_RATE_LIMITED")
    principal = authenticate(body.username, body.password)
    if principal is None:
        throttle.record_failure(body.username, address)
        raise HTTPException(status_code=401, detail="INVALID_CREDENTIALS")
    throttle.clear_user(body.username)
    token = create_session(principal)
    response.set_cookie(
        _COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
        secure=not settings.auth_http_test_mode, samesite="lax", path="/",
    )
    return {"id": principal.user_id, "username": principal.username, "role": principal.role}


@router.get("/auth/me")
def me(principal: Principal = Depends(current_principal)):
    return {"id": principal.user_id, "username": principal.username, "role": principal.role}


@router.get("/queue/me")
def my_queue(principal: Principal = Depends(current_principal)):
    from app.workflow.queue_store import personal_snapshot
    return personal_snapshot(principal.user_id)


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    if settings.multiuser_enabled:
        validate_mutation_origin(request)
    revoke_session(request.cookies.get(_COOKIE, ""))
    response.delete_cookie(_COOKIE, path="/")
    return {"status": "signed_out"}


@router.post("/auth/change-password")
def change_password(body: ChangePasswordBody, response: Response,
                    principal: Principal = Depends(current_principal)):
    try:
        change_own_password(principal.user_id, body.current_password, body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.delete_cookie(_COOKIE, path="/")
    return {"status": "password_changed"}


@router.get("/auth/preferences/{key}")
def get_preference(key: str, principal: Principal = Depends(current_principal)):
    key = _page_size_key(key)
    with session_scope() as session:
        value = session.execute(select(_PREFERENCES.c.value_json).where(
            _PREFERENCES.c.user_id == principal.user_id,
            _PREFERENCES.c.preference_key == key,
        )).scalar_one_or_none()
    return {"value": int(value) if value is not None else 20}


@router.put("/auth/preferences/{key}")
def set_preference(key: str, body: PreferenceBody,
                   principal: Principal = Depends(current_principal)):
    key = _page_size_key(key)
    if body.value not in {5, 10, 20, 50}:
        raise HTTPException(status_code=422, detail="INVALID_PAGE_SIZE")
    with session_scope() as session:
        statement = pg_insert(_PREFERENCES).values(
            user_id=principal.user_id, preference_key=key, value_json=str(body.value),
        )
        session.execute(statement.on_conflict_do_update(
            index_elements=[_PREFERENCES.c.user_id, _PREFERENCES.c.preference_key],
            set_={"value_json": str(body.value)},
        ))
    return {"value": body.value}


@router.get("/admin/users")
def users(_admin: Principal = Depends(admin_principal)):
    return list_users()


@router.post("/admin/users")
def add_user(body: CreateUserBody, _admin: Principal = Depends(admin_principal)):
    try:
        user = create_user(body.username, body.password, actor_user_id=_admin.user_id)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="USERNAME_EXISTS") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": user.user_id, "username": user.username, "role": user.role}


@router.post("/admin/users/{user_id}/disable")
def disable_account(user_id: int, _admin: Principal = Depends(admin_principal)):
    try:
        disable_user(user_id, actor_user_id=_admin.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "disabled"}


@router.post("/admin/users/{user_id}/reset-password")
def reset_account_password(user_id: int, body: ResetPasswordBody,
                           _admin: Principal = Depends(admin_principal)):
    try:
        reset_password(user_id, body.password, actor_user_id=_admin.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "password_reset"}
