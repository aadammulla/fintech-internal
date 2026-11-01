"""HTTP layer for the Refunds Dashboard. Thin: parse input, call service, render/redirect.
Authorization lives in service.py; the route dependencies here are an extra layer."""
from __future__ import annotations

import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.apps.refunds import service
from app.core.audit import list_events
from app.core.authz import permission_required
from app.core.db import get_db
from app.core.errors import ValidationError
from app.core.identity import User, list_users
from app.core.templating import render

router = APIRouter()


def _redirect(url: str, notice: str) -> RedirectResponse:
    return RedirectResponse(f"{url}?notice={quote(notice)}", status_code=303)


@router.get("")
@router.get("/")
def list_page(request: Request, status: str | None = None, customer_id: str | None = None,
              requested_by: str | None = None,
              user: User = Depends(permission_required("refunds.view")),
              conn: sqlite3.Connection = Depends(get_db)):
    requester = int(requested_by) if requested_by and requested_by.isdigit() else None
    refunds = service.list_refunds(conn, user, status=status or None,
                                   customer_id=customer_id or None, requested_by=requester)
    return render(request, "refunds/list.html", {
        "refunds": refunds,
        "counts": service.status_counts(conn, user),
        "statuses": service.STATUSES,
        "users": list_users(conn),
        "filters": {"status": status or "", "customer_id": customer_id or "",
                    "requested_by": requested_by or ""},
    })


@router.get("/new")
def new_page(request: Request, user: User = Depends(permission_required("refunds.create"))):
    return render(request, "refunds/new.html", {"form": {}, "errors": {}})


@router.post("/new")
def create(request: Request, customer_id: str = Form(""), amount: str = Form(""),
           reason: str = Form(""), user: User = Depends(permission_required("refunds.create")),
           conn: sqlite3.Connection = Depends(get_db)):
    try:
        refund = service.create_refund(conn, user, customer_id=customer_id, amount=amount, reason=reason)
    except ValidationError as e:
        return render(request, "refunds/new.html",
                      {"form": {"customer_id": customer_id, "amount": amount, "reason": reason},
                       "errors": e.errors}, status_code=400)
    return _redirect(f"/apps/refunds/{refund.id}", f"Refund {refund.reference} created.")


@router.get("/{refund_id}")
def detail_page(request: Request, refund_id: int,
                user: User = Depends(permission_required("refunds.view")),
                conn: sqlite3.Connection = Depends(get_db)):
    refund = service.get_refund(conn, user, refund_id)
    history = list_events(conn, target_type=service.TARGET_TYPE, target_id=refund_id)
    return render(request, "refunds/detail.html", {
        "refund": refund,
        "history": history,
        "is_own": refund.requested_by == user.id,
        "is_pending": refund.status == service.PENDING,
    })


@router.post("/{refund_id}/approve")
def approve(refund_id: int, note: str = Form(""),
            user: User = Depends(permission_required("refunds.decide")),
            conn: sqlite3.Connection = Depends(get_db)):
    refund = service.approve_refund(conn, user, refund_id, note)
    return _redirect(f"/apps/refunds/{refund.id}", f"Refund {refund.reference} approved.")


@router.post("/{refund_id}/reject")
def reject(refund_id: int, note: str = Form(""),
           user: User = Depends(permission_required("refunds.decide")),
           conn: sqlite3.Connection = Depends(get_db)):
    refund = service.reject_refund(conn, user, refund_id, note)
    return _redirect(f"/apps/refunds/{refund.id}", f"Refund {refund.reference} rejected.")
