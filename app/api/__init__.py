"""API 层:FastAPI 应用工厂。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.api.web import web_router
from app.db import init_db

# web 目录以代码位置定位,不依赖 runtime_root 的相对关系
_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"


class ImmutableStaticFiles(StaticFiles):
    """Serve hashed SPA assets with a durable browser cache."""

    async def get_response(self, path: str, scope: dict):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def create_app() -> FastAPI:
    init_db()
    from app.workflow.queue import reconcile_interrupted_tasks
    from app.interaction import reconcile_interrupted_interactions
    reconcile_interrupted_tasks()
    reconcile_interrupted_interactions()
    application = FastAPI(title="报告整编 Agent")
    application.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
    application.include_router(api_router)
    application.include_router(web_router)
    spa_assets = _WEB_DIR / "spa" / "assets"
    if spa_assets.exists():
        application.mount(
            "/ui-assets/assets", ImmutableStaticFiles(directory=str(spa_assets)), name="ui-assets"
        )
    application.mount(
        "/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static"
    )
    return application


app = create_app()
