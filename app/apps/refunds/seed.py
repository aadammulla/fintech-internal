"""Synthetic refund requests, created through the service layer so the audit trail is consistent."""
from __future__ import annotations

import sqlite3

from app.apps.refunds import service
from app.core.identity import User


def _user(conn: sqlite3.Connection, email: str) -> User:
    return User.from_row(conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())


SEED_REFUNDS = [
    # requester email, customer, amount, reason, decision (approver email, status, note) or None
    ("priya.raman@example.test", "CUS-104233", "42.99",
     "Duplicate charge for monthly Premium plan (two captures on the same day).",
     ("dana.okafor@example.test", "approved", "Confirmed duplicate capture in ledger.")),
    ("marcus.chen@example.test", "CUS-208871", "1250.00",
     "Wire fee charged on a transfer that was cancelled before settlement.",
     ("luis.ortega@example.test", "approved", "")),
    ("priya.raman@example.test", "CUS-310045", "19.00",
     "Customer requests refund of card replacement fee; card arrived damaged.",
     ("dana.okafor@example.test", "rejected", "Replacement already reissued free of charge.")),
    ("marcus.chen@example.test", "CUS-417302", "305.40",
     "FX markup applied twice on a EUR purchase; customer provided statement.", None),
    ("priya.raman@example.test", "CUS-552190", "8.50",
     "Overdraft fee assessed during a documented service outage.", None),
    ("marcus.chen@example.test", "CUS-660718", "74.25",
     "Subscription renewed after customer cancelled in-app (cancellation logged).", None),
    ("dana.okafor@example.test", "CUS-771504", "2200.00",
     "Goodwill refund approved verbally by escalations team; needs second approver.", None),
]


def seed(conn: sqlite3.Connection) -> None:
    for requester, customer, amount, reason, decision in SEED_REFUNDS:
        refund = service.create_refund(conn, _user(conn, requester), customer_id=customer,
                                       amount=amount, reason=reason)
        if decision:
            approver, status, note = decision
            fn = service.approve_refund if status == "approved" else service.reject_refund
            fn(conn, _user(conn, approver), refund.id, note)
