"""HTTP layer for Feature Flag Administration. Thin: parse input, call service, render/redirect.
Authorization lives in service.py; the route dependencies here are an extra layer."""
from __future__ import annotations

import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.apps.flags import service
from app.core.audit import list_events
from app.core.authz import permission_required
from app.core.db import get_db
from app.core.errors import ValidationError
from app.core.identity import User
from app.core.templating import render

router = APIRouter()


@router.get("")
@router.get("/")
def list_page(request: Request, environment: str | None = None,
              user: User = Depends(permission_required("flags.view")),
              conn: sqlite3.Connection = Depends(get_db)):
    return render(request, "flags/list.html", {
        "flags": service.list_flags(conn, user, environment=environment or None),
        "environment_options": [(e, e) for e in service.ENVIRONMENTS],
        "filters": {"environment": environment or ""},
    })


def _detail(request: Request, conn: sqlite3.Connection, user: User, flag_id: int,
            errors: dict | None = None, reason: str = "", status_code: int = 200):
    flag = service.get_flag(conn, user, flag_id)
    return render(request, "flags/detail.html", {
        "flag": flag,
        "history": list_events(conn, target_type=service.TARGET_TYPE, target_id=flag_id),
        "errors": errors or {},
        "form": {"reason": reason},
    }, status_code=status_code)


@router.get("/{flag_id}")
def detail_page(request: Request, flag_id: int,
                user: User = Depends(permission_required("flags.view")),
                conn: sqlite3.Connection = Depends(get_db)):
    return _detail(request, conn, user, flag_id)


def _change(request: Request, conn: sqlite3.Connection, user: User, flag_id: int,
            enabled: bool, reason: str):
    try:
        flag = service.set_flag(conn, user, flag_id, enabled, reason)
    except ValidationError as e:
        return _detail(request, conn, user, flag_id, errors=e.errors, reason=reason, status_code=400)
    notice = f"{flag.name} ({flag.environment}) {flag.status}."
    return RedirectResponse(f"/apps/flags/{flag.id}?notice={quote(notice)}", status_code=303)


@router.post("/{flag_id}/enable")
def enable(request: Request, flag_id: int, reason: str = Form(""),
           user: User = Depends(permission_required("flags.toggle")),
           conn: sqlite3.Connection = Depends(get_db)):
    return _change(request, conn, user, flag_id, True, reason)


@router.post("/{flag_id}/disable")
def disable(request: Request, flag_id: int, reason: str = Form(""),
            user: User = Depends(permission_required("flags.toggle")),
            conn: sqlite3.Connection = Depends(get_db)):
    return _change(request, conn, user, flag_id, False, reason)
