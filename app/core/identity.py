"""SIMULATED employee identity. This is NOT authentication.

The selected user id is stored in a plain cookie that any browser user can change.
It exists only so the demo can exercise role-based authorization. Replace
`current_user()` with your SSO/OIDC integration before any real use.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fastapi import Depends, Request

from app.core.db import get_db
from app.core.errors import NotAuthenticated

IDENTITY_COOKIE = "sim_user_id"
ROLES = ("agent", "manager", "engineer")


@dataclass(frozen=True)
class User:
    id: int
    name: str
    email: str
    role: str
    title: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        return cls(id=row["id"], name=row["name"], email=row["email"], role=row["role"], title=row["title"])


def get_user(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User.from_row(row) if row else None


def list_users(conn: sqlite3.Connection) -> list[User]:
    rows = conn.execute("SELECT * FROM users ORDER BY role, name").fetchall()
    return [User.from_row(r) for r in rows]


def create_user(conn: sqlite3.Connection, *, name: str, email: str, role: str, title: str = "") -> User:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    cur = conn.execute(
        "INSERT INTO users (name, email, role, title) VALUES (?, ?, ?, ?)", (name, email, role, title)
    )
    return get_user(conn, cur.lastrowid)  # type: ignore[return-value]


def load_user_from_request(request: Request, conn: sqlite3.Connection) -> User | None:
    """Resolve the simulated identity cookie to a user (cached on request.state)."""
    if getattr(request.state, "user_loaded", False):
        return request.state.user
    user = None
    raw = request.cookies.get(IDENTITY_COOKIE)
    if raw and raw.isdigit():
        user = get_user(conn, int(raw))
    request.state.user = user
    request.state.user_loaded = True
    return user


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> User | None:
    """FastAPI dependency: the selected identity, or None."""
    return load_user_from_request(request, conn)


def require_user(user: User | None = Depends(current_user)) -> User:
    """FastAPI dependency: the selected identity, or 401."""
    if user is None:
        raise NotAuthenticated("Select a simulated employee identity to continue.")
    return user
