"""Application factory. Add new internal tools to INSTALLED_APPS."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.apps.flags import APP as FLAGS_APP
from app.apps.refunds import APP as REFUNDS_APP
from app.config import db_path_from_env, seed_demo_from_env
from app.core import authz
from app.core.db import connect, init_db
from app.core.handlers import register_exception_handlers
from app.core.registry import InternalApp
from app.core.routes import router as core_router
from app.core.seed import is_empty, seed_all
from app.core.templating import build_templates

INSTALLED_APPS: list[InternalApp] = [
    REFUNDS_APP,
    FLAGS_APP,
]


def create_app(db_path: str | Path | None = None, seed_demo: bool | None = None,
               apps: list[InternalApp] | None = None) -> FastAPI:
    apps = INSTALLED_APPS if apps is None else apps
    db_path = Path(db_path) if db_path else db_path_from_env()
    seed_demo = seed_demo_from_env() if seed_demo is None else seed_demo

    authz.register_app_permissions(apps)

    init_db(db_path, [spec.schema_sql for spec in apps if spec.schema_sql])
    if seed_demo:
        conn = connect(db_path)
        try:
            if is_empty(conn):
                seed_all(conn, apps)
        finally:
            conn.close()

    app = FastAPI(title="Fintech Internal Tools Starter Kit", docs_url=None, redoc_url=None)
    app.state.db_path = db_path
    app.state.templates = build_templates([spec.templates_dir for spec in apps], apps)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "core" / "static"), name="static")
    register_exception_handlers(app)
    app.include_router(core_router)
    for spec in apps:
        app.include_router(spec.router, prefix=spec.url)
    return app

