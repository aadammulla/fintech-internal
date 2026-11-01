"""Feature flag rules. Direct, authorized toggles (no approval workflow): engineers flip a flag
with a reason; everyone else can only look. Authorization is checked HERE, not just in routes."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.core.audit import record_event
from app.core.authz import ensure_permission
from app.core.db import transaction, utcnow_iso
from app.core.errors import InvalidTransition, NotFound, ValidationError
from app.core.identity import User

PERMISSIONS = {
    "flags.view": {"agent", "manager", "engineer"},
    "flags.toggle": {"engineer"},
}

ENVIRONMENTS = ("development", "staging", "production")
TARGET_TYPE = "feature_flag"
MIN_REASON, MAX_REASON = 5, 500


@dataclass(frozen=True)
class Flag:
    id: int
    name: str
    description: str
    environment: str
    enabled: bool
    updated_at: str
    updated_by: int | None
    updated_by_name: str | None

    @property
    def status(self) -> str:
        return "enabled" if self.enabled else "disabled"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Flag":
        data = {k: row[k] for k in row.keys()}
        data["enabled"] = bool(data["enabled"])
        return cls(**data)


_SELECT = """
SELECT f.*, u.name AS updated_by_name
FROM feature_flags f
LEFT JOIN users u ON u.id = f.updated_by
"""


def get_flag(conn: sqlite3.Connection, actor: User, flag_id: int) -> Flag:
    ensure_permission(actor, "flags.view", target_type=TARGET_TYPE, target_id=flag_id)
    row = conn.execute(f"{_SELECT} WHERE f.id = ?", (flag_id,)).fetchone()
    if row is None:
        raise NotFound(f"Feature flag {flag_id} does not exist.")
    return Flag.from_row(row)


def list_flags(conn: sqlite3.Connection, actor: User, *, environment: str | None = None) -> list[Flag]:
    ensure_permission(actor, "flags.view")
    if environment and environment not in ENVIRONMENTS:
        raise ValidationError({"environment": f"Unknown environment {environment!r}"})
    where, params = ("WHERE f.environment = ?", (environment,)) if environment else ("", ())
    rows = conn.execute(f"{_SELECT} {where} ORDER BY f.name, f.environment", params).fetchall()
    return [Flag.from_row(r) for r in rows]


def validate_reason(reason: str) -> str:
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON:
        raise ValidationError({"reason": f"Give a reason (at least {MIN_REASON} characters)"})
    if len(reason) > MAX_REASON:
        raise ValidationError({"reason": f"Reason must be {MAX_REASON} characters or fewer"})
    return reason


def set_flag(conn: sqlite3.Connection, actor: User, flag_id: int, enabled: bool, reason: str) -> Flag:
    ensure_permission(actor, "flags.toggle", target_type=TARGET_TYPE, target_id=flag_id)
    if not isinstance(enabled, bool):
        raise ValidationError({"enabled": "Must be true or false"})
    reason = validate_reason(reason)
    with transaction(conn):
        flag = get_flag(conn, actor, flag_id)
        if flag.enabled == enabled:
            raise InvalidTransition(f"Flag '{flag.name}' is already {flag.status} in {flag.environment}.")
        cur = conn.execute(
            "UPDATE feature_flags SET enabled = ?, updated_at = ?, updated_by = ? WHERE id = ? AND enabled = ?",
            (int(enabled), utcnow_iso(), actor.id, flag_id, int(flag.enabled)),
        )
        if cur.rowcount != 1:  # changed underneath us
            raise InvalidTransition("Flag was modified by someone else; reload and try again.")
        record_event(conn, actor=actor, action="flag.enabled" if enabled else "flag.disabled",
                     target_type=TARGET_TYPE, target_id=flag_id,
                     details={"flag": flag.name, "environment": flag.environment,
                              "previous_value": flag.enabled, "new_value": enabled, "reason": reason})
    return get_flag(conn, actor, flag_id)
