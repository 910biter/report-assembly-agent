"""Vue single-page application routes.

The report workflow remains behind /api. Browser routes all receive the same
compiled shell so navigation and refresh behave consistently.
"""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"
_SPA_INDEX = _WEB_DIR / "spa" / "index.html"

web_router = APIRouter(include_in_schema=False)


def _spa_response():
    if not _SPA_INDEX.exists():
        return JSONResponse(
            {"error": "FRONTEND_NOT_BUILT", "hint": "Run npm run build in frontend/"},
            status_code=503,
        )
    return FileResponse(
        _SPA_INDEX,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@web_router.get("/")
@web_router.get("/tasks")
@web_router.get("/tasks/{task_id}")
@web_router.get("/materials")
@web_router.get("/reports")
@web_router.get("/reports/{report_id}")
@web_router.get("/style")
@web_router.get("/settings")
def spa_page(task_id: str = "", report_id: int = 0):
    return _spa_response()
