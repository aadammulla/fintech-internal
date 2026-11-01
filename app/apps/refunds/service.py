"""Refund business rules. Every protected operation checks authorization HERE, so the
rules hold no matter which route (or test, or future API) calls them."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.core.audit import record_event
from app.core.authz import ensure_permission
from app.core.db import transaction, utcnow_iso
from app.core.errors import InvalidTransition, NotFound, PermissionDenied, ValidationError
from app.core.identity import User
from app.core.workflow import StatusMachine

PERMISSIONS = {
    "refunds.view": {"agent", "manager", "engineer"},
    "refunds.create": {"agent", "manager"},
    "refunds.decide": {"manager"},
}

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
STATUSES = (PENDING, APPROVED, REJECTED)
STATUS_MACHINE = StatusMachine({PENDING: {APPROVED, REJECTED}, APPROVED: set(), REJECTED: set()})

MAX_REFUND_CENTS = 10_000_00
CUSTOMER_ID_RE = re.compile(r"^CUS-[0-9]{6}$")
TARGET_TYPE = "refund_request"


@dataclass(frozen=True)
class Refund:
    id: int
    customer_id: str
    amount_cents: int
    currency: str
    reason: str
    status: str
    requested_by: int
    requested_by_name: str
    created_at: str
    decided_by: int | None
    decided_by_name: str | None
    decided_at: str | None
    decision_note: str | None

    @property
    def reference(self) -> str:
        return f"RF-{self.id:05d}"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Refund":
        return cls(**{k: row[k] for k in row.keys()})


_SELECT = """
SELECT r.*, req.name AS requested_by_name, dec.name AS decided_by_name
FROM refund_requests r
JOIN users req ON req.id = r.requested_by
LEFT JOIN users dec ON dec.id = r.decided_by
"""


def parse_amount(raw: str) -> int:
    """'125.50' -> 12550 cents. Raises ValidationError."""
    try:
        value = Decimal(str(raw).strip().replace(",", "").lstrip("$"))
    except InvalidOperation:
        raise ValidationError({"amount": "Enter a number like 125.50"})
    if not value.is_finite() or value != value.quantize(Decimal("0.01")):
        raise ValidationError({"amount": "Use at most two decimal places"})
    cents = int(value * 100)
    if cents <= 0:
        raise ValidationError({"amount": "Amount must be greater than zero"})
    if cents > MAX_REFUND_CENTS:
        raise ValidationError({"amount": f"Amount cannot exceed ${MAX_REFUND_CENTS / 100:,.2f}"})
    return cents


def validate_new_refund(customer_id: str, amount: str, reason: str) -> tuple[str, int, str]:
    errors: dict[str, str] = {}
    customer_id = (customer_id or "").strip().upper()
    reason = (reason or "").strip()
    if not CUSTOMER_ID_RE.match(customer_id):
        errors["customer_id"] = "Customer ID must look like CUS-123456"
    cents = 0
    try:
        cents = parse_amount(amount)
    except ValidationError as e:
        errors.update(e.errors)
    if len(reason) < 5:
        errors["reason"] = "Give a reason (at least 5 characters)"
    elif len(reason) > 500:
        errors["reason"] = "Reason must be 500 characters or fewer"
    if errors:
        raise ValidationError(errors)
    return customer_id, cents, reason


def get_refund(conn: sqlite3.Connection, actor: User, refund_id: int) -> Refund:
    ensure_permission(actor, "refunds.view", target_type=TARGET_TYPE, target_id=refund_id)
    row = conn.execute(f"{_SELECT} WHERE r.id = ?", (refund_id,)).fetchone()
    if row is None:
        raise NotFound(f"Refund request {refund_id} does not exist.")
    return Refund.from_row(row)


def list_refunds(conn: sqlite3.Connection, actor: User, *, status: str | None = None,
                 customer_id: str | None = None, requested_by: int | None = None,
                 limit: int = 200) -> list[Refund]:
    ensure_permission(actor, "refunds.view")
    clauses, params = [], []
    if status:
        if status not in STATUSES:
            raise ValidationError({"status": f"Unknown status {status!r}"})
        clauses.append("r.status = ?")
        params.append(status)
    if customer_id:
        clauses.append("r.customer_id LIKE ?")
        params.append(f"%{customer_id.strip().upper()}%")
    if requested_by is not None:
        clauses.append("r.requested_by = ?")
        params.append(requested_by)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"{_SELECT} {where} ORDER BY r.id DESC LIMIT ?", (*params, limit)).fetchall()
    return [Refund.from_row(r) for r in rows]


def status_counts(conn: sqlite3.Connection, actor: User) -> dict[str, int]:
    ensure_permission(actor, "refunds.view")
    counts = {s: 0 for s in STATUSES}
    for row in conn.execute("SELECT status, COUNT(*) FROM refund_requests GROUP BY status"):
        counts[row[0]] = row[1]
    return counts


def create_refund(conn: sqlite3.Connection, actor: User, *, customer_id: str, amount: str,
                  reason: str) -> Refund:
    ensure_permission(actor, "refunds.create", target_type=TARGET_TYPE, target_id="new")
    customer_id, cents, reason = validate_new_refund(customer_id, amount, reason)
    with transaction(conn):
        cur = conn.execute(
            """INSERT INTO refund_requests (customer_id, amount_cents, currency, reason, status,
                                            requested_by, created_at)
               VALUES (?, ?, 'USD', ?, ?, ?, ?)""",
            (customer_id, cents, reason, PENDING, actor.id, utcnow_iso()),
        )
        refund_id = cur.lastrowid
        record_event(conn, actor=actor, action="refund.created", target_type=TARGET_TYPE,
                     target_id=refund_id,
                     details={"customer_id": customer_id, "amount_cents": cents, "currency": "USD",
                              "reason": reason, "status": PENDING})
    return get_refund(conn, actor, refund_id)


def _decide(conn: sqlite3.Connection, actor: User, refund_id: int, new_status: str,
            note: str) -> Refund:
    ensure_permission(actor, "refunds.decide", target_type=TARGET_TYPE, target_id=refund_id)
    note = (note or "").strip()
    if len(note) > 500:
        raise ValidationError({"note": "Note must be 500 characters or fewer"})
    with transaction(conn):
        refund = get_refund(conn, actor, refund_id)
        if refund.requested_by == actor.id:
            raise PermissionDenied(
                "Separation of duties: you cannot approve or reject a refund you requested.",
                permission="refunds.decide", target_type=TARGET_TYPE, target_id=refund_id,
            )
        STATUS_MACHINE.ensure_transition(refund.status, new_status)
        cur = conn.execute(
            """UPDATE refund_requests
               SET status = ?, decided_by = ?, decided_at = ?, decision_note = ?
               WHERE id = ? AND status = ?""",
            (new_status, actor.id, utcnow_iso(), note or None, refund_id, refund.status),
        )
        if cur.rowcount != 1:  # status changed underneath us
            raise InvalidTransition("Refund was modified by someone else; reload and try again.")
        record_event(conn, actor=actor, action=f"refund.{new_status}", target_type=TARGET_TYPE,
                     target_id=refund_id,
                     details={"from_status": refund.status, "to_status": new_status,
                              "amount_cents": refund.amount_cents, "customer_id": refund.customer_id,
                              "requested_by": refund.requested_by, "note": note})
    return get_refund(conn, actor, refund_id)


def approve_refund(conn: sqlite3.Connection, actor: User, refund_id: int, note: str = "") -> Refund:
    return _decide(conn, actor, refund_id, APPROVED, note)


def reject_refund(conn: sqlite3.Connection, actor: User, refund_id: int, note: str = "") -> Refund:
    return _decide(conn, actor, refund_id, REJECTED, note)
