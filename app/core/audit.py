"""Shared audit trail.

record_event() is the only write path and only ever INSERTs. There is no update or
delete function, no route that edits events, and SQLite triggers (see db.CORE_SCHEMA)
abort UPDATE/DELETE. This is append-only *through the application*; it is not
tamper-proof against someone with direct access to the database file.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.core.db import utcnow_iso
from app.core.identity import User


@dataclass(frozen=True)
class AuditEvent:
    id: int
    occurred_at: str
    actor_id: int | None
    actor_name: str
    actor_role: str
    action: str
    target_type: str
    target_id: str
    details: dict[str, Any]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "AuditEvent":
        return cls(
            id=row["id"], occurred_at=row["occurred_at"], actor_id=row["actor_id"],
            actor_name=row["actor_name"], actor_role=row["actor_role"], action=row["action"],
            target_type=row["target_type"], target_id=row["target_id"],
            details=json.loads(row["details"] or "{}"),
        )


def record_event(conn: sqlite3.Connection, *, actor: User | None, action: str,
                 target_type: str, target_id: str | int, details: dict[str, Any] | None = None) -> int:
    """Insert one audit event. Call inside the same transaction() as the change it describes.

    `action` convention: "<app>.<verb>", e.g. "refund.approved", "kyc.case_escalated".
    Actor name/role are snapshotted so the log stays readable if the user record changes.
    """
    cur = conn.execute(
        """INSERT INTO audit_events
           (occurred_at, actor_id, actor_name, actor_role, action, target_type, target_id, details)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            utcnow_iso(),
            actor.id if actor else None,
            actor.name if actor else "anonymous",
            actor.role if actor else "none",
            action,
            target_type,
            str(target_id),
            json.dumps(details or {}, sort_keys=True, default=str),
        ),
    )
    return cur.lastrowid  # type: ignore[return-value]


def list_events(conn: sqlite3.Connection, *, action: str | None = None, actor_id: int | None = None,
                target_type: str | None = None, target_id: str | int | None = None,
                limit: int = 500) -> list[AuditEvent]:
    clauses, params = [], []
    if action:
        clauses.append("action = ?")
        params.append(action)
    if actor_id is not None:
        clauses.append("actor_id = ?")
        params.append(actor_id)
    if target_type:
        clauses.append("target_type = ?")
        params.append(target_type)
    if target_id is not None:
        clauses.append("target_id = ?")
        params.append(str(target_id))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM audit_events {where} ORDER BY id DESC LIMIT ?", (*params, limit)
    ).fetchall()
    return [AuditEvent.from_row(r) for r in rows]


def distinct_actions(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT action FROM audit_events ORDER BY action")]
