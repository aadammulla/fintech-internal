"""Synthetic feature flags. Flags are inserted directly (creating flags is out of scope for this
app); the demo change is made through the service so it appears in the audit trail."""
from __future__ import annotations

import sqlite3

from app.apps.flags import service
from app.core.db import transaction, utcnow_iso
from app.core.identity import User

SEED_FLAGS = [
    # name, description, environment, enabled
    ("instant_payouts", "Let eligible merchants receive payouts within minutes instead of T+1.", "staging", True),
    ("instant_payouts", "Let eligible merchants receive payouts within minutes instead of T+1.", "production", True),
    ("kyc_document_upload_v2", "New document-upload flow for identity verification.", "staging", True),
    ("kyc_document_upload_v2", "New document-upload flow for identity verification.", "production", False),
    ("card_freeze_self_service", "Customers can freeze and unfreeze their own cards in the app.", "production", True),
    ("fx_rate_cache", "Serve FX quotes from the new in-memory rate cache.", "development", False),
    ("dispute_auto_triage", "Route new card disputes to queues automatically by reason code.", "staging", True),
]

SEED_CHANGES = [
    # actor email, flag name, environment, enabled, reason
    ("sam.whitfield@example.test", "dispute_auto_triage", "staging", False,
     "Misrouting chargebacks in staging tests; pausing until the reason-code mapping is fixed."),
]


def _user(conn: sqlite3.Connection, email: str) -> User:
    return User.from_row(conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())


def seed(conn: sqlite3.Connection) -> None:
    now = utcnow_iso()
    with transaction(conn):
        conn.executemany(
            "INSERT INTO feature_flags (name, description, environment, enabled, updated_at) VALUES (?, ?, ?, ?, ?)",
            [(n, d, e, int(on), now) for n, d, e, on in SEED_FLAGS],
        )
    for email, name, env, enabled, reason in SEED_CHANGES:
        flag_id = conn.execute("SELECT id FROM feature_flags WHERE name = ? AND environment = ?",
                               (name, env)).fetchone()[0]
        service.set_flag(conn, _user(conn, email), flag_id, enabled, reason)
