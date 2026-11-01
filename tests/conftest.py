import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("FINTECH_SEED_DEMO", "0")

from app.core.db import connect  # noqa: E402
from app.core.identity import User  # noqa: E402
from app.main import create_app  # noqa: E402

AGENT = "priya.raman@example.test"
AGENT_2 = "marcus.chen@example.test"
MANAGER = "dana.okafor@example.test"
MANAGER_2 = "luis.ortega@example.test"
ENGINEER = "sam.whitfield@example.test"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def app(db_path):
    return create_app(db_path=db_path, seed_demo=True)


@pytest.fixture
def conn(app, db_path) -> sqlite3.Connection:
    c = connect(db_path)
    yield c
    c.close()


@pytest.fixture
def user(conn):
    def _get(email: str) -> User:
        return User.from_row(conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())
    return _get


@pytest.fixture
def client_as(app, user):
    """client_as(email) -> TestClient with that simulated identity selected via the real /identity route."""
    def _make(email: str | None = None) -> TestClient:
        c = TestClient(app)
        if email:
            r = c.post("/identity", data={"user_id": user(email).id, "next": "/"}, follow_redirects=False)
            assert r.status_code == 303
        return c
    return _make


def refund_status(conn, refund_id):
    return conn.execute("SELECT status FROM refund_requests WHERE id = ?", (refund_id,)).fetchone()[0]


def audit_count(conn, action=None, target_id=None):
    sql, params = "SELECT COUNT(*) FROM audit_events WHERE 1=1", []
    if action:
        sql += " AND action = ?"
        params.append(action)
    if target_id is not None:
        sql += " AND target_id = ?"
        params.append(str(target_id))
    return conn.execute(sql, params).fetchone()[0]
