import json
import sqlite3

import pytest

from app.apps.refunds import service
from app.core import audit
from tests.conftest import AGENT, MANAGER, audit_count

VALID = {"customer_id": "CUS-900100", "amount": "250.00", "reason": "Chargeback pre-empt"}


def latest(conn, action):
    return conn.execute("SELECT * FROM audit_events WHERE action = ? ORDER BY id DESC", (action,)).fetchone()


def test_create_approve_reject_each_record_one_event(conn, user):
    agent, mgr = user(AGENT), user(MANAGER)
    r1 = service.create_refund(conn, agent, **VALID)
    assert audit_count(conn, "refund.created", r1.id) == 1
    ev = latest(conn, "refund.created")
    assert (ev["actor_id"], ev["actor_name"], ev["actor_role"]) == (agent.id, agent.name, "agent")
    assert (ev["target_type"], ev["target_id"]) == ("refund_request", str(r1.id))
    assert ev["occurred_at"]
    assert json.loads(ev["details"])["amount_cents"] == 25000

    service.approve_refund(conn, mgr, r1.id, "looks fine")
    ev = latest(conn, "refund.approved")
    assert ev["actor_id"] == mgr.id and ev["target_id"] == str(r1.id)
    d = json.loads(ev["details"])
    assert d["from_status"] == "pending" and d["to_status"] == "approved" and d["note"] == "looks fine"

    r2 = service.create_refund(conn, agent, **VALID)
    service.reject_refund(conn, mgr, r2.id)
    assert audit_count(conn, "refund.rejected", r2.id) == 1


def test_http_flow_records_events(client_as, conn):
    r = client_as(AGENT).post("/apps/refunds/new", data=VALID, follow_redirects=False)
    refund_id = r.headers["location"].split("/")[-1].split("?")[0]
    client_as(MANAGER).post(f"/apps/refunds/{refund_id}/reject", data={"note": "No evidence"})
    assert audit_count(conn, "refund.created", refund_id) == 1
    assert audit_count(conn, "refund.rejected", refund_id) == 1


def test_failed_operations_record_no_refund_events(conn, user, client_as):
    refund = service.create_refund(conn, user(AGENT), **VALID)
    before = audit_count(conn, "refund.approved") + audit_count(conn, "refund.rejected")
    client_as(AGENT).post(f"/apps/refunds/{refund.id}/approve")       # unauthorized
    client_as(AGENT).post("/apps/refunds/new", data=dict(VALID, amount="-1"))  # invalid
    assert audit_count(conn, "refund.approved") + audit_count(conn, "refund.rejected") == before


def test_audit_write_failure_rolls_back_business_change(conn, user, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("audit store unavailable")
    monkeypatch.setattr(service, "record_event", boom)
    before = conn.execute("SELECT COUNT(*) FROM refund_requests").fetchone()[0]
    with pytest.raises(RuntimeError):
        service.create_refund(conn, user(AGENT), **VALID)
    assert conn.execute("SELECT COUNT(*) FROM refund_requests").fetchone()[0] == before


def test_audit_events_cannot_be_updated_or_deleted_via_sqlite(conn, user):
    service.create_refund(conn, user(AGENT), **VALID)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE audit_events SET action = 'tampered'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM audit_events")


def test_no_edit_or_delete_endpoints(client_as):
    mgr = client_as(MANAGER)
    for method in ("post", "put", "patch", "delete"):
        assert getattr(mgr, method)("/audit").status_code == 405
    assert mgr.delete("/audit/1").status_code in (404, 405)


def test_audit_module_exposes_no_mutation_helpers():
    public = {n for n in dir(audit) if not n.startswith("_")}
    assert not any(w in n for n in public for w in ("update", "delete", "edit", "remove"))


def test_manager_audit_page_lists_and_filters(client_as, conn, user):
    refund = service.create_refund(conn, user(AGENT), **VALID)
    page = client_as(MANAGER).get("/audit", params={"action": "refund.created", "target_id": refund.id})
    assert page.status_code == 200
    assert "refund.created" in page.text and "CUS-900100" in page.text
