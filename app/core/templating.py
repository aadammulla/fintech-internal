"""Shared Jinja2 environment and the render() helper every page uses."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

from app.core import authz
from app.core.db import connect
from app.core.identity import list_users, load_user_from_request

CORE_TEMPLATES_DIR = Path(__file__).parent / "templates"


def format_money(cents: int | None, currency: str = "USD") -> str:
    if cents is None:
        return ""
    symbol = "$" if currency == "USD" else f"{currency} "
    return f"{symbol}{cents / 100:,.2f}"


def format_dt(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value


def build_templates(app_template_dirs: list[Path], apps: list) -> Jinja2Templates:
    env = Environment(
        loader=ChoiceLoader([FileSystemLoader(str(CORE_TEMPLATES_DIR))]
                            + [FileSystemLoader(str(d)) for d in app_template_dirs]),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["money"] = format_money
    env.filters["dt"] = format_dt
    env.globals["can"] = authz.can
    env.globals["installed_apps"] = apps
    return Jinja2Templates(env=env)


def render(request: Request, template: str, context: dict[str, Any] | None = None,
           status_code: int = 200):
    """Render a page with the shared layout context (current user, identity list, notices)."""
    conn = connect(request.app.state.db_path)
    try:
        user = load_user_from_request(request, conn)
        users = list_users(conn)
    finally:
        conn.close()
    ctx = {
        "current_user": user,
        "all_users": users,
        "notice": request.query_params.get("notice"),
        "current_path": request.url.path,
    }
    ctx.update(context or {})
    return request.app.state.templates.TemplateResponse(request, template, ctx, status_code=status_code)
