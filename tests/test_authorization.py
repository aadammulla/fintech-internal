import pytest

from app.apps.refunds import service
from app.core import authz
from app.core.errors import PermissionDenied
from tests.conftest import AGENT, ENGINEER, MANAGER, audit_count, refund_status

VALID = {"customer_id": "CUS-123456", "amount": "50.00", "reason": "Duplicate card charge"}


def make_pending(conn, user, email=AGENT):
    return service.create_refund(conn, user(email), **VALID)


@pytest.mark.parametrize("method,path", [
    ("get", "/apps/refunds"), ("get", "/apps/refunds/1"), ("get", "/apps/refunds/new"),
    ("post", "/apps/refunds/new"), ("post", "/apps/refunds/1/approve"),
    ("post", "/apps/refunds/1/reject"), ("get", "/audit"),
])
def test_anonymous_requests_are_rejected(client_as, method, path):
    r = getattr(client_as(None), method)(path)
    assert r.status_code == 401


def test_agent_cannot_approve_or_reject_via_http(client_as, conn, user):
    refund = make_pending(conn, user, email="marcus.chen@example.test")
    agent = client_as(AGENT)
    # The button is hidden for agents, but we POST directly to prove the backend blocks it.
    assert agent.post(f"/apps/refunds/{refund.id}/approve").status_code == 403
    assert agent.post(f"/apps/refunds/{refund.id}/reject").status_code == 403
    assert refund_status(conn, refund.id) == "pending"
    assert audit_count(conn, "refund.approved", refund.id) == 0
    assert audit_count(conn, "refund.rejected", refund.id) == 0


def test_agent_cannot_approve_via_service(conn, user):
    refund = make_pending(conn, user, email="marcus.chen@example.test")
    with pytest.raises(PermissionDenied):
        service.approve_refund(conn, user(AGENT), refund.id)
    assert refund_status(conn, refund.id) == "pending"


def test_engineer_can_view_but_not_create_or_decide(client_as, conn, user):
    refund = make_pending(conn, user)
    eng = client_as(ENGINEER)
    assert eng.get("/apps/refunds").status_code == 200
    assert eng.get(f"/apps/refunds/{refund.id}").status_code == 200
    assert eng.get("/apps/refunds/new").status_code == 403
    before = conn.execute("SELECT COUNT(*) FROM refund_requests").fetchone()[0]
    assert eng.post("/apps/refunds/new", data=VALID).status_code == 403
    assert conn.execute("SELECT COUNT(*) FROM refund_requests").fetchone()[0] == before
    assert eng.post(f"/apps/refunds/{refund.id}/approve").status_code == 403
    with pytest.raises(PermissionDenied):
        service.create_refund(conn, user(ENGINEER), **VALID)


def test_agent_and_manager_can_create(client_as):
    for email in (AGENT, MANAGER):
        r = client_as(email).post("/apps/refunds/new", data=VALID, follow_redirects=False)
        assert r.status_code == 303


@pytest.mark.parametrize("email,expected", [(AGENT, 403), (ENGINEER, 403), (MANAGER, 200)])
def test_audit_log_is_manager_only(client_as, email, expected):
    assert client_as(email).get("/audit").status_code == expected


def test_denied_attempts_are_audited(client_as, conn, user):
    refund = make_pending(conn, user, email="marcus.chen@example.test")
    client_as(AGENT).post(f"/apps/refunds/{refund.id}/approve")
    row = conn.execute("SELECT * FROM audit_events WHERE action = 'access.denied' ORDER BY id DESC").fetchone()
    assert row["actor_id"] == user(AGENT).id
    assert "refunds.decide" in row["details"]


def test_unknown_permission_fails_closed(user):
    with pytest.raises(KeyError):
        authz.can(user(MANAGER), "refunds.delete_everything")


def test_tampered_identity_cookie_is_treated_as_anonymous(app):
    from fastapi.testclient import TestClient
    c = TestClient(app, cookies={"sim_user_id": "99999"})
    assert c.get("/apps/refunds").status_code == 401
