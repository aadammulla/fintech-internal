"""Feature Flag Administration: authorization, validation, persistence and audit."""
import json

import pytest
from fastapi.testclient import TestClient

from app.apps.flags import service
from app.core.errors import InvalidTransition, NotFound, PermissionDenied, ValidationError
from app.main import create_app
from tests.conftest import AGENT, ENGINEER, MANAGER, MANAGER_2, audit_count

REASON = {"reason": "Rolling out to all merchants after staging soak"}


def flag_id(conn, name, environment):
    return conn.execute("SELECT id FROM feature_flags WHERE name = ? AND environment = ?",
                        (name, environment)).fetchone()[0]


def is_enabled(conn, fid):
    return bool(conn.execute("SELECT enabled FROM feature_flags WHERE id = ?", (fid,)).fetchone()[0])


def flag_event_count(conn, fid=None):
    return audit_count(conn, "flag.enabled", fid) + audit_count(conn, "flag.disabled", fid)


@pytest.fixture
def off_flag(conn):
    return flag_id(conn, "kyc_document_upload_v2", "production")


@pytest.fixture
def on_flag(conn):
    return flag_id(conn, "instant_payouts", "production")


# --- viewing -----------------------------------------------------------------------------

@pytest.mark.parametrize("email", [AGENT, MANAGER, ENGINEER])
def test_every_role_can_view_list_and_detail(client_as, off_flag, email):
    c = client_as(email)
    page = c.get("/apps/flags")
    assert page.status_code == 200
    for text in ("kyc_document_upload_v2", "New document-upload flow", "production", "badge-disabled"):
        assert text in page.text
    assert c.get(f"/apps/flags/{off_flag}").status_code == 200


@pytest.mark.parametrize("email,has_form", [(AGENT, False), (MANAGER, False), (ENGINEER, True)])
def test_toggle_form_only_rendered_for_engineers(client_as, off_flag, email, has_form):
    page = client_as(email).get(f"/apps/flags/{off_flag}").text
    assert (f'action="/apps/flags/{off_flag}/enable"' in page) is has_form


def test_environment_filter(client_as):
    c = client_as(AGENT)
    dev = c.get("/apps/flags", params={"environment": "development"}).text
    assert "fx_rate_cache" in dev and "instant_payouts" not in dev
    assert c.get("/apps/flags", params={"environment": "moon"}).status_code == 400


@pytest.mark.parametrize("method,path", [
    ("get", "/apps/flags"), ("get", "/apps/flags/1"),
    ("post", "/apps/flags/1/enable"), ("post", "/apps/flags/1/disable"),
])
def test_anonymous_requests_are_rejected(client_as, method, path):
    assert getattr(client_as(None), method)(path, **({"data": REASON} if method == "post" else {})).status_code == 401


# --- engineers can change flags ----------------------------------------------------------

def test_engineer_can_enable_and_disable_via_http(client_as, conn, off_flag):
    eng = client_as(ENGINEER)
    r = eng.post(f"/apps/flags/{off_flag}/enable", data=REASON, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/apps/flags/{off_flag}?notice=")
    assert is_enabled(conn, off_flag)

    r = eng.post(f"/apps/flags/{off_flag}/disable", data={"reason": "Elevated error rate on upload"})
    assert r.status_code == 200 and "badge-disabled" in r.text
    assert not is_enabled(conn, off_flag)
    assert flag_event_count(conn, off_flag) == 2


def test_engineer_can_change_via_service(conn, user, on_flag):
    flag = service.set_flag(conn, user(ENGINEER), on_flag, False, "Incident INC-1234 mitigation")
    assert flag.enabled is False and flag.updated_by == user(ENGINEER).id


# --- agents and managers cannot ----------------------------------------------------------

@pytest.mark.parametrize("email", [AGENT, MANAGER, MANAGER_2])
@pytest.mark.parametrize("action", ["enable", "disable"])
def test_agents_and_managers_cannot_change_via_direct_http(client_as, conn, user, off_flag, on_flag,
                                                           email, action):
    fid = off_flag if action == "enable" else on_flag
    before = is_enabled(conn, fid)
    r = client_as(email).post(f"/apps/flags/{fid}/{action}", data=REASON)
    assert r.status_code == 403
    assert is_enabled(conn, fid) == before
    assert flag_event_count(conn, fid) == 0
    denied = conn.execute("SELECT * FROM audit_events WHERE action = 'access.denied' ORDER BY id DESC").fetchone()
    assert denied["actor_id"] == user(email).id and "flags.toggle" in denied["details"]


@pytest.mark.parametrize("email", [AGENT, MANAGER])
def test_agents_and_managers_cannot_change_via_service(conn, user, off_flag, email):
    with pytest.raises(PermissionDenied):
        service.set_flag(conn, user(email), off_flag, True, REASON["reason"])
    assert not is_enabled(conn, off_flag)


def test_permission_checked_before_validation(client_as, conn, off_flag):
    # An unauthorized caller learns nothing from validation errors.
    assert client_as(AGENT).post(f"/apps/flags/{off_flag}/enable", data={"reason": ""}).status_code == 403
    assert client_as(AGENT).post("/apps/flags/99999/enable", data=REASON).status_code == 403


# --- invalid requests --------------------------------------------------------------------

@pytest.mark.parametrize("reason", ["", "   ", "abc", "x" * 501])
def test_reason_is_required_and_bounded(client_as, conn, off_flag, reason):
    r = client_as(ENGINEER).post(f"/apps/flags/{off_flag}/enable", data={"reason": reason})
    assert r.status_code == 400 and "field-error" in r.text
    assert not is_enabled(conn, off_flag)
    assert flag_event_count(conn, off_flag) == 0


def test_missing_reason_field_is_rejected(client_as, conn, off_flag):
    assert client_as(ENGINEER).post(f"/apps/flags/{off_flag}/enable").status_code == 400
    assert not is_enabled(conn, off_flag)


def test_reason_required_at_service_level(conn, user, off_flag):
    with pytest.raises(ValidationError):
        service.set_flag(conn, user(ENGINEER), off_flag, True, "")


def test_non_boolean_value_rejected_at_service_level(conn, user, off_flag):
    with pytest.raises(ValidationError):
        service.set_flag(conn, user(ENGINEER), off_flag, "yes", REASON["reason"])  # type: ignore[arg-type]
    assert not is_enabled(conn, off_flag)


def test_no_op_change_is_a_conflict(client_as, conn, on_flag, off_flag):
    eng = client_as(ENGINEER)
    assert eng.post(f"/apps/flags/{on_flag}/enable", data=REASON).status_code == 409
    assert eng.post(f"/apps/flags/{off_flag}/disable", data=REASON).status_code == 409
    assert flag_event_count(conn, on_flag) == flag_event_count(conn, off_flag) == 0


def test_unknown_flag_is_404(client_as, conn, user):
    eng = client_as(ENGINEER)
    assert eng.get("/apps/flags/99999").status_code == 404
    assert eng.post("/apps/flags/99999/enable", data=REASON).status_code == 404
    with pytest.raises(NotFound):
        service.set_flag(conn, user(ENGINEER), 99999, True, REASON["reason"])


def test_malformed_flag_id_is_rejected(client_as):
    assert client_as(ENGINEER).post("/apps/flags/abc/enable", data=REASON).status_code == 422


def test_stale_write_is_a_conflict(conn, user, off_flag, monkeypatch):
    # Simulate another engineer flipping the flag between our read and our UPDATE.
    real_get = service.get_flag

    def racing_get(c, actor, fid):
        flag = real_get(c, actor, fid)
        c.execute("UPDATE feature_flags SET enabled = 1 WHERE id = ?", (fid,))
        return flag

    monkeypatch.setattr(service, "get_flag", racing_get)
    with pytest.raises(InvalidTransition):
        service.set_flag(conn, user(ENGINEER), off_flag, True, REASON["reason"])
    assert flag_event_count(conn, off_flag) == 0


# --- persistence -------------------------------------------------------------------------

def test_change_persists_after_refresh_and_restart(client_as, conn, db_path, user, off_flag):
    client_as(ENGINEER).post(f"/apps/flags/{off_flag}/enable", data=REASON)

    # Refresh: a plain GET on the same client.
    assert "badge-enabled" in client_as(AGENT).get(f"/apps/flags/{off_flag}").text

    # Restart: brand-new app instance + client over the same SQLite file.
    fresh = TestClient(create_app(db_path=db_path, seed_demo=False))
    fresh.post("/identity", data={"user_id": user(MANAGER).id})
    page = fresh.get(f"/apps/flags/{off_flag}")
    assert "badge-enabled" in page.text and REASON["reason"] in page.text and "Sam Whitfield" in page.text
    listing = fresh.get("/apps/flags", params={"environment": "production"}).text
    row = listing.split("kyc_document_upload_v2", 1)[1].split("</tr>", 1)[0]
    assert "badge-enabled" in row


# --- audit -------------------------------------------------------------------------------

def test_successful_change_records_exactly_one_complete_audit_event(conn, user, off_flag):
    eng = user(ENGINEER)
    service.set_flag(conn, eng, off_flag, True, "  Launch approved in CAB-77  ")
    assert audit_count(conn, "flag.enabled", off_flag) == 1
    ev = conn.execute("SELECT * FROM audit_events WHERE action = 'flag.enabled' ORDER BY id DESC").fetchone()
    assert (ev["actor_id"], ev["actor_name"], ev["actor_role"]) == (eng.id, eng.name, "engineer")
    assert (ev["target_type"], ev["target_id"]) == ("feature_flag", str(off_flag))
    assert ev["occurred_at"]
    assert json.loads(ev["details"]) == {
        "flag": "kyc_document_upload_v2", "environment": "production",
        "previous_value": False, "new_value": True, "reason": "Launch approved in CAB-77",
    }


def test_audit_write_failure_rolls_back_flag_change(conn, user, off_flag, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("audit store unavailable")
    monkeypatch.setattr(service, "record_event", boom)
    with pytest.raises(RuntimeError):
        service.set_flag(conn, user(ENGINEER), off_flag, True, REASON["reason"])
    assert not is_enabled(conn, off_flag)


def test_history_shown_on_flag_page_and_in_audit_log(client_as, conn, off_flag):
    client_as(ENGINEER).post(f"/apps/flags/{off_flag}/enable", data=REASON)
    client_as(ENGINEER).post(f"/apps/flags/{off_flag}/disable", data={"reason": "Rollback: upload latency"})
    page = client_as(AGENT).get(f"/apps/flags/{off_flag}").text
    history = page.split("Change history", 1)[1]
    for text in ("flag.enabled", "flag.disabled", REASON["reason"], "Rollback: upload latency",
                 "previous_value", "new_value", "Sam Whitfield"):
        assert text in history
    assert history.index("flag.disabled") < history.index("flag.enabled")  # newest first

    audit = client_as(MANAGER).get("/audit", params={"target_type": "feature_flag", "target_id": off_flag})
    assert "flag.enabled" in audit.text and "flag.disabled" in audit.text


def test_history_is_per_flag(client_as, conn, off_flag, on_flag):
    client_as(ENGINEER).post(f"/apps/flags/{off_flag}/enable", data=REASON)
    other = client_as(ENGINEER).get(f"/apps/flags/{on_flag}").text.split("Change history", 1)[1]
    assert REASON["reason"] not in other


def test_seeded_change_is_audited(conn):
    fid = flag_id(conn, "dispute_auto_triage", "staging")
    assert not is_enabled(conn, fid)
    assert audit_count(conn, "flag.disabled", fid) == 1
