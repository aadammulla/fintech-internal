import pytest

from app.apps.refunds import service
from app.core.errors import InvalidTransition, ValidationError
from app.core.workflow import StatusMachine
from tests.conftest import AGENT, MANAGER, MANAGER_2, audit_count, refund_status

VALID = {"customer_id": "CUS-777001", "amount": "12.34", "reason": "Fee charged in error"}


@pytest.fixture
def pending(conn, user):
    return service.create_refund(conn, user(AGENT), **VALID)


@pytest.mark.parametrize("first,second", [
    ("approve", "approve"), ("approve", "reject"), ("reject", "approve"), ("reject", "reject"),
])
def test_decided_refunds_cannot_change_again_http(client_as, conn, pending, first, second):
    mgr = client_as(MANAGER)
    assert mgr.post(f"/apps/refunds/{pending.id}/{first}", follow_redirects=False).status_code == 303
    final = refund_status(conn, pending.id)
    events_before = audit_count(conn)
    r = client_as(MANAGER_2).post(f"/apps/refunds/{pending.id}/{second}")
    assert r.status_code == 409
    assert refund_status(conn, pending.id) == final
    assert audit_count(conn) == events_before


def test_decided_refunds_cannot_change_again_service(conn, user, pending):
    service.reject_refund(conn, user(MANAGER), pending.id)
    with pytest.raises(InvalidTransition):
        service.approve_refund(conn, user(MANAGER_2), pending.id)
    assert refund_status(conn, pending.id) == "rejected"


def test_unknown_refund_is_404(client_as):
    assert client_as(MANAGER).post("/apps/refunds/424242/approve").status_code == 404


def test_status_machine():
    sm = StatusMachine({"pending": {"approved", "rejected"}})
    assert sm.can_transition("pending", "approved")
    assert not sm.can_transition("approved", "pending")
    assert not sm.can_transition("pending", "bogus")
    assert sm.is_terminal("approved")
    with pytest.raises(InvalidTransition):
        sm.ensure_transition("rejected", "approved")


def test_refund_status_machine_matches_rules():
    sm = service.STATUS_MACHINE
    assert sm.transitions["pending"] == {"approved", "rejected"}
    assert sm.is_terminal("approved") and sm.is_terminal("rejected")


@pytest.mark.parametrize("field,value", [
    ("customer_id", "12345"), ("customer_id", ""), ("amount", "0"), ("amount", "-5"),
    ("amount", "abc"), ("amount", "1.234"), ("amount", "10000.01"), ("amount", "NaN"),
    ("reason", ""), ("reason", "x" * 501),
])
def test_invalid_input_rejected(client_as, conn, user, field, value):
    data = dict(VALID, **{field: value})
    before = conn.execute("SELECT COUNT(*) FROM refund_requests").fetchone()[0]
    assert client_as(AGENT).post("/apps/refunds/new", data=data).status_code == 400
    with pytest.raises(ValidationError):
        service.create_refund(conn, user(AGENT), **data)
    assert conn.execute("SELECT COUNT(*) FROM refund_requests").fetchone()[0] == before
