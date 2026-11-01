import sqlite3

import pytest

from app.apps.refunds import service
from app.core.errors import PermissionDenied
from tests.conftest import MANAGER, MANAGER_2, audit_count, refund_status

VALID = {"customer_id": "CUS-555000", "amount": "980.00", "reason": "Goodwill credit for outage"}


def test_manager_cannot_approve_own_request_via_http(client_as, conn):
    mgr = client_as(MANAGER)
    r = mgr.post("/apps/refunds/new", data=VALID, follow_redirects=False)
    refund_id = int(r.headers["location"].split("/")[-1].split("?")[0])
    assert mgr.post(f"/apps/refunds/{refund_id}/approve").status_code == 403
    assert mgr.post(f"/apps/refunds/{refund_id}/reject").status_code == 403
    assert refund_status(conn, refund_id) == "pending"
    assert audit_count(conn, "refund.approved", refund_id) == 0


def test_manager_cannot_approve_own_request_via_service(conn, user):
    refund = service.create_refund(conn, user(MANAGER), **VALID)
    with pytest.raises(PermissionDenied, match="Separation of duties"):
        service.approve_refund(conn, user(MANAGER), refund.id)
    with pytest.raises(PermissionDenied):
        service.reject_refund(conn, user(MANAGER), refund.id)
    assert refund_status(conn, refund.id) == "pending"


def test_different_manager_can_approve(conn, user, client_as):
    refund = service.create_refund(conn, user(MANAGER), **VALID)
    r = client_as(MANAGER_2).post(f"/apps/refunds/{refund.id}/approve", follow_redirects=False)
    assert r.status_code == 303
    assert refund_status(conn, refund.id) == "approved"


def test_database_constraint_backstops_self_approval(conn, user):
    refund = service.create_refund(conn, user(MANAGER), **VALID)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE refund_requests SET status='approved', decided_by=requested_by WHERE id=?",
                     (refund.id,))
