"""End-to-end over HTTP: agent creates -> manager approves -> persists across refresh and restart -> audited."""
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import AGENT, MANAGER


def test_refund_lifecycle_end_to_end(client_as, db_path, user):
    agent = client_as(AGENT)
    r = agent.post("/apps/refunds/new", follow_redirects=True, data={
        "customer_id": "cus-246810", "amount": "$1,049.99", "reason": "Merchant confirmed double capture"})
    assert r.status_code == 200
    assert "created" in r.text and "pending" in r.text
    refund_id = int(str(r.url).split("/")[-1].split("?")[0])

    # Agent cannot approve, even by POSTing directly.
    assert agent.post(f"/apps/refunds/{refund_id}/approve").status_code == 403
    assert "pending" in agent.get(f"/apps/refunds/{refund_id}").text

    manager = client_as(MANAGER)
    r = manager.post(f"/apps/refunds/{refund_id}/approve", data={"note": "Verified in ledger"})
    assert r.status_code == 200 and "approved" in r.text

    # "Refresh": brand-new app instance + client over the same SQLite file.
    fresh = TestClient(create_app(db_path=db_path, seed_demo=False))
    fresh.post("/identity", data={"user_id": user(AGENT).id})
    page = fresh.get(f"/apps/refunds/{refund_id}")
    assert "badge-approved" in page.text and "CUS-246810" in page.text and "$1,049.99" in page.text
    assert "Verified in ledger" in page.text
    listing = fresh.get("/apps/refunds", params={"status": "approved", "customer_id": "246810"})
    assert f"RF-{refund_id:05d}" in listing.text

    fresh.post("/identity", data={"user_id": user(MANAGER).id})
    audit = fresh.get("/audit", params={"target_type": "refund_request", "target_id": refund_id})
    assert "refund.created" in audit.text and "refund.approved" in audit.text
    assert "access.denied" in fresh.get("/audit", params={"action": "access.denied"}).text


def test_filters(client_as):
    c = client_as(AGENT)
    pending = c.get("/apps/refunds", params={"status": "pending"}).text
    assert "badge-pending" in pending and "badge-approved\">approved</span></td>" not in pending
    assert c.get("/apps/refunds", params={"status": "bogus"}).status_code == 400
    assert "CUS-104233" in c.get("/apps/refunds", params={"customer_id": "1042"}).text
