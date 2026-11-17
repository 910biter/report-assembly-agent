"""Default-deny business gate and administrator-only activation routes."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.api.auth_routes import admin_principal, current_principal
from .protocol import LicenseError
from . import service


def get_service():
    return service.get_service()

router = APIRouter(prefix="/api/license")


class TokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=16384)


@router.get("/status")
def status(principal=Depends(current_principal)):
    return get_service().status(admin=principal.role == "admin")


@router.get("/request")
def installation_request(_admin=Depends(admin_principal)):
    try:
        return {"request": get_service().request()}
    except Exception:
        raise HTTPException(409, detail="INSTALLATION_IDENTITY_UNAVAILABLE") from None


@router.post("/activate")
def activate(body: TokenBody, _admin=Depends(admin_principal)):
    try:
        return get_service().activate(body.token)
    except LicenseError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    except Exception:
        raise HTTPException(503, detail="LICENSE_STORAGE_UNAVAILABLE") from None


@router.get("/recovery-request")
def recovery_request(_admin=Depends(admin_principal)):
    try:
        return get_service().recovery_request()
    except Exception:
        raise HTTPException(409, detail="LICENSE_RECOVERY_UNAVAILABLE") from None


@router.post("/recover-clock")
def recover(body: TokenBody, _admin=Depends(admin_principal)):
    try:
        return get_service().recover_clock(body.token)
    except LicenseError as exc:
        raise HTTPException(422, detail=str(exc)) from None


_PUBLIC_API = {
    "/api/auth/login", "/api/auth/me", "/api/auth/logout", "/api/auth/change-password",
    "/api/license/status", "/api/license/request", "/api/license/activate",
    "/api/license/recovery-request", "/api/license/recover-clock",
}


class LicenseMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "").rstrip("/")
        if scope["type"] in {"http", "websocket"} and path.startswith("/api") and path not in _PUBLIC_API:
            result = await run_in_threadpool(lambda: get_service().status())
            if not result["active"]:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4403})
                    return
                if path == "/api/health":
                    # Installation readiness must not depend on commercial activation.
                    response = JSONResponse({"status": "ok", "license": result["status"]})
                else:
                    response = JSONResponse(
                        {"detail": "LICENSE_REQUIRED", "license_status": result["status"]},
                        status_code=403, headers={"Cache-Control": "no-store"})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
