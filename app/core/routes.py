"""Shared pages: home, simulated identity switcher, audit log."""
from __future__ import annotations

import sqlite3
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.core.audit import distinct_actions, list_events
from app.core.authz import permission_required
from app.core.db import get_db
from app.core.errors import ValidationError
from app.core.identity import IDENTITY_COOKIE, User, get_user, list_users
from app.core.templating import render

router = APIRouter()


def safe_next(url: str | None) -> str:
    """Only allow local redirects."""
    if not url:
        return "/"
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc or not url.startswith("/"):
        return "/"
    return url


@router.get("/")
def home(request: Request):
    return render(request, "home.html")


@router.get("/healthz")
def healthz():
    return {"ok": True}


@router.post("/identity")
def switch_identity(user_id: int = Form(...), next: str = Form("/"),
                    conn: sqlite3.Connection = Depends(get_db)):
    if get_user(conn, user_id) is None:
        raise ValidationError({"user_id": "Unknown user"})
    resp = RedirectResponse(safe_next(next), status_code=303)
    resp.set_cookie(IDENTITY_COOKIE, str(user_id), httponly=True, samesite="lax")
    return resp


@router.post("/identity/clear")
def clear_identity():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(IDENTITY_COOKIE)
    return resp


@router.get("/audit")
def audit_log(request: Request, action: str | None = None, actor_id: str | None = None,
              target_type: str | None = None, target_id: str | None = None,
              user: User = Depends(permission_required("audit.view")),
              conn: sqlite3.Connection = Depends(get_db)):
    actor = int(actor_id) if actor_id and actor_id.isdigit() else None
    events = list_events(conn, action=action or None, actor_id=actor,
                         target_type=target_type or None, target_id=target_id or None)
    return render(request, "audit.html", {
        "events": events,
        "actions": distinct_actions(conn),
        "users": list_users(conn),
        "filters": {"action": action or "", "actor_id": actor_id or "",
                    "target_type": target_type or "", "target_id": target_id or ""},
    })
