"""API 层:FastAPI 应用工厂。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.api.web import web_router
from app.db import init_db

# web 目录以代码位置定位,不依赖 runtime_root 的相对关系
_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"


def create_app() -> FastAPI:
    init_db()
    application = FastAPI(title="报告整编 Agent")
    application.include_router(api_router)
    application.include_router(web_router)
    spa_assets = _WEB_DIR / "spa" / "assets"
    if spa_assets.exists():
        application.mount(
            "/ui-assets/assets", StaticFiles(directory=str(spa_assets)), name="ui-assets"
        )
    application.mount(
        "/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static"
    )
    return application


app = create_app()
