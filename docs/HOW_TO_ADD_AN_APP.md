# How to add an internal app

This guide walks through adding a new tool, using a **KYC Review Queue** as the example. There are two
reference implementations. Copy whichever is closer to your tool:

- **Refunds Dashboard** (`app/apps/refunds/`): a request → approve/reject workflow with a `StatusMachine`
  and separation of duties.
- **Feature Flags** (`app/apps/flags/`): direct, authorized edits of existing records (no workflow). Engineers
  toggle a flag with a required reason; everyone else views. Shows re-rendering a detail page with
  validation errors, a no-op change as `409`, and a per-record history page.

Expect to write about five small files plus templates, and add two lines to `app/main.py` (import + list entry).
The only shared files you'd normally touch are cosmetic: a `.badge-<status>` rule in
`app/core/static/style.css` and the "Coming later" card in `app/core/templates/home.html`.

Setup: `pip install -r requirements.txt` in your venv (it includes `pytest` and `httpx`), then
`python -m pytest` should pass before you start.

## 0. What you get for free

| Need | Reuse | Where |
|---|---|---|
| DB connection per request | `Depends(get_db)` | `app/core/db.py` |
| Atomic write + audit together | `with transaction(conn):` | `app/core/db.py` |
| Timestamps | `utcnow_iso()` | `app/core/db.py` |
| Who is acting | `User` dataclass, `Depends(require_user)` | `app/core/identity.py` |
| Role checks | `ensure_permission(user, "kyc.decide", target_type=..., target_id=...)` | `app/core/authz.py` |
| Route-level role checks | `Depends(permission_required("kyc.view"))` | `app/core/authz.py` |
| UI-only show/hide | `{% if can(current_user, "kyc.decide") %}` (Jinja global) | `app/core/templating.py` |
| Audit trail | `record_event(conn, actor=, action=, target_type=, target_id=, details=)` | `app/core/audit.py` |
| Per-record history | `list_events(conn, target_type=..., target_id=...)` | `app/core/audit.py` |
| Status rules (optional: only if your records have a lifecycle) | `StatusMachine({...}).ensure_transition(old, new)` | `app/core/workflow.py` |
| Errors → HTTP | raise `PermissionDenied` (403), `NotFound` (404), `InvalidTransition` (409), `ValidationError({field: msg})` (400) | `app/core/errors.py` (mapped in `handlers.py`) |
| Page rendering | `render(request, "kyc/queue.html", {...})` adds `current_user`, `all_users`, `notice` | `app/core/templating.py` |
| Layout, nav, identity selector | `{% extends "base.html" %}` with `{% block title %}` / `{% block content %}` | `app/core/templates/base.html` |
| UI components | `{% import "macros.html" as ui %}` → `ui.status_badge`, `ui.role_badge`, `ui.field_error`, `ui.empty_state`, `ui.select_filter`, `ui.audit_table` | `app/core/templates/macros.html` |
| Formatting | `|money(currency)` (cents → `$1,234.50`), `|dt` (ISO → `2026-11-01 17:50 UTC`) | `app/core/templating.py` |
| CSS classes | `.table`, `.filters`, `.form`, `.kv`, `.badge-*`, `.callout`, `.button`, `.page-head`, `.stats` | `app/core/static/style.css` |
| Home page card + top nav link | automatic once registered | `home.html`, `base.html` |
| Audit log page with filters | automatic: your events appear at `/audit` | `app/core/routes.py` |
| Demo data on first start | `seed` callable on your spec | `app/core/seed.py` |

## 1. Create the package

```
app/apps/kyc/
  __init__.py      # APP = InternalApp(...)
  schema.py        # SCHEMA_SQL
  service.py       # PERMISSIONS, STATUS_MACHINE, business functions
  routes.py        # router = APIRouter()
  seed.py          # optional synthetic data
  templates/kyc/   # queue.html, detail.html   (subfolder name = slug, avoids name clashes)
```

## 2. Schema: `schema.py`

Use `CREATE TABLE IF NOT EXISTS`. Reference `users(id)` for actors. Put cheap invariants in `CHECK`
constraints as a backstop (see the `decided_by <> requested_by` check in `app/apps/refunds/schema.py`).

```python
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kyc_cases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  TEXT NOT NULL,
    risk_level   TEXT NOT NULL CHECK (risk_level IN ('low','medium','high')),
    status       TEXT NOT NULL DEFAULT 'open'
                 CHECK (status IN ('open','in_review','approved','rejected','escalated')),
    assigned_to  INTEGER REFERENCES users(id),
    created_at   TEXT NOT NULL,
    decided_by   INTEGER REFERENCES users(id),
    decided_at   TEXT
);
"""
```

`init_db()` in `app/core/db.py` runs it on startup. There are no migrations, so delete
`data/fintech_internal.db` (or run `python -m app.core.seed --reset`) after changing a table.

## 3. Rules: `service.py`

Model it on `app/apps/refunds/service.py`. The pattern for every write is:

1. `ensure_permission(actor, "<perm>", target_type=..., target_id=...)` **first**
2. validate input → `ValidationError({...})`
3. `with transaction(conn):` load the row, check domain rules (ownership/SoD), `STATUS_MACHINE.ensure_transition(...)`,
   do a conditional `UPDATE ... WHERE id = ? AND status = ?` and treat `rowcount != 1` as `InvalidTransition`
4. `record_event(...)` **inside the same transaction**

```python
from app.core.audit import record_event
from app.core.authz import ensure_permission
from app.core.db import transaction, utcnow_iso
from app.core.errors import InvalidTransition, NotFound, PermissionDenied
from app.core.identity import User
from app.core.workflow import StatusMachine

PERMISSIONS = {
    "kyc.view":   {"agent", "manager"},
    "kyc.review": {"agent", "manager"},
    "kyc.decide": {"manager"},
}
STATUS_MACHINE = StatusMachine({
    "open":      {"in_review"},
    "in_review": {"approved", "rejected", "escalated"},
    "escalated": {"approved", "rejected"},
})
TARGET_TYPE = "kyc_case"

def decide(conn, actor: User, case_id: int, new_status: str):
    ensure_permission(actor, "kyc.decide", target_type=TARGET_TYPE, target_id=case_id)
    with transaction(conn):
        row = conn.execute("SELECT * FROM kyc_cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise NotFound(f"KYC case {case_id} not found")
        if row["assigned_to"] == actor.id:
            raise PermissionDenied("Reviewer cannot also make the final decision",
                                   permission="kyc.decide", target_type=TARGET_TYPE, target_id=case_id)
        STATUS_MACHINE.ensure_transition(row["status"], new_status)
        cur = conn.execute("UPDATE kyc_cases SET status=?, decided_by=?, decided_at=? "
                           "WHERE id=? AND status=?",
                           (new_status, actor.id, utcnow_iso(), case_id, row["status"]))
        if cur.rowcount != 1:
            raise InvalidTransition("Case changed underneath you; reload")
        record_event(conn, actor=actor, action=f"kyc.{new_status}", target_type=TARGET_TYPE,
                     target_id=case_id, details={"from_status": row["status"], "to_status": new_status})
```

Not every app is a workflow. For direct edits (see `app/apps/flags/service.py::set_flag`) skip the
`StatusMachine` and SoD, but keep the rest: permission first, validate, load + conditional
`UPDATE ... WHERE id = ? AND <old value>` inside `transaction()`, and `record_event` with the previous and
new values in `details`. Raise `InvalidTransition` (409) for a no-op or stale change so the audit log
only contains real changes.

Conventions:
- Permission names: `<app>.<capability>`. Audit actions: `<entity>.<past_tense_verb>`
  (e.g. `refund.approved`, `flag.enabled`; the older `"<app>.<verb>"` wording in `audit.py` means the same thing).
- `record_event` fills in actor id/name/role and the timestamp. Put everything else a reviewer needs
  (old/new values, reason, human-readable names) in `details`.
- Keep authorization in the service even if the route also checks. Tests and future APIs call the service directly.
- Never write to `audit_events` except through `record_event`.

## 4. HTTP: `routes.py`

Copy `app/apps/refunds/routes.py`. Keep routes thin:

```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from app.core.authz import permission_required
from app.core.db import get_db
from app.core.templating import render
from app.apps.kyc import service

router = APIRouter()

@router.get("")
def queue(request: Request, user=Depends(permission_required("kyc.view")), conn=Depends(get_db)):
    return render(request, "kyc/queue.html", {"cases": service.list_cases(conn, user)})

@router.post("/{case_id}/approve")
def approve(case_id: int, user=Depends(permission_required("kyc.decide")), conn=Depends(get_db)):
    service.decide(conn, user, case_id, "approved")
    return RedirectResponse(f"/apps/kyc/{case_id}?notice=Approved", status_code=303)
```

Use POST + `303` redirect for every state change, so the browser refresh is safe. Raise the domain errors
and let `app/core/handlers.py` render them. For form validation errors, catch `ValidationError` and
re-render the form with `e.errors` and `status_code=400` (see `routes.create` in refunds, or
`routes._change` in flags for a form that lives on a detail page). An uncaught `ValidationError` still
becomes a plain 400 error page.

Things the foundation does for you, and its gaps:
- **Denied requests are audited** as `access.denied`. When the route dependency
  `permission_required(...)` rejects, the event's target is `route:<path>` (it doesn't know the record),
  so per-record history pages filtered by `target_type`/`target_id` won't show denials. Use `/audit?action=access.denied`.
- **Malformed path params** (e.g. `/apps/kyc/abc` for an `int` id) return FastAPI's **422 JSON**, not the
  HTML error page, and aren't audited. Unauthorized callers still get 403 first because the permission
  dependency runs before the handler.

## 5. Templates: `templates/kyc/*.html`

```jinja
{% extends "base.html" %}
{% import "macros.html" as ui %}
{% block title %}KYC queue{% endblock %}
{% block content %}
  <h1>KYC review queue</h1>
  <table class="table">
    {% for c in cases %}<tr><td>{{ c.customer_id }}</td><td>{{ ui.status_badge(c.status) }}</td></tr>{% endfor %}
  </table>
  {% if can(current_user, "kyc.decide") %}…buttons (cosmetic only)…{% endif %}
  <h2>History</h2>{{ ui.audit_table(history, show_target=False) }}
{% endblock %}
```

`ui.status_badge` styles any status. Add a `.badge-<status>` rule in `app/core/static/style.css` for colours
(e.g. `.badge-enabled`/`.badge-disabled` were added for flags). For filters, `ui.select_filter` takes a list of
`(value, label)` tuples, so build that in the route.

## 6. Register: `__init__.py` and `app/main.py`

```python
# app/apps/kyc/__init__.py
from pathlib import Path
from app.core.registry import InternalApp
from app.apps.kyc.routes import router
from app.apps.kyc.schema import SCHEMA_SQL
from app.apps.kyc.service import PERMISSIONS
from app.apps.kyc.seed import seed

APP = InternalApp(slug="kyc", name="KYC Review Queue",
                  description="Review and decide customer identity verification cases.",
                  router=router, templates_dir=Path(__file__).parent / "templates",
                  schema_sql=SCHEMA_SQL, permissions=PERMISSIONS,
                  view_permission="kyc.view", seed=seed)
```

```python
# app/main.py
from app.apps.kyc import APP as KYC_APP
INSTALLED_APPS = [REFUNDS_APP, KYC_APP]
```

`create_app()` then registers permissions, creates tables, seeds (on an empty DB), mounts the router
at `/apps/kyc`, adds the template folder, and lists the app on the home page and nav.

## 7. Seed data: `seed.py` (optional)

Signature: `def seed(conn: sqlite3.Connection) -> None`. Create records **through your service
functions** (see `app/apps/refunds/seed.py`) so the demo audit trail is consistent. If your app has no
"create" action (flags), insert the base rows directly and make demo *changes* through the service
(see `app/apps/flags/seed.py`).

**Seeds run only when the `users` table is empty** (`app/core/seed.py::is_empty`). Adding an app to an
existing database creates its tables but leaves them empty. Run `python -m app.core.seed --reset` after
installing a new app, and say so in the README. Seeds also run for every test (the `app` fixture seeds),
so count audit events filtered by action/target, not globally. Use fictional
customers (`CUS-######`) and the seeded users from `app/core/seed.py::DEMO_USERS`.

If you need a new role (e.g. `compliance`), add it to `ROLES` in `app/core/identity.py`, the `CHECK`
on `users.role` in `app/core/db.py`, and `DEMO_USERS`. Then reset the DB.

## 8. Tests

Reuse the fixtures in `tests/conftest.py`:
- `client_as(email)` gives a `TestClient` with a simulated identity selected through the real `/identity` route
- `conn` and `user(email)` for calling service functions directly
- `audit_count(conn, action, target_id)`. `refund_status` is refunds-specific; write the equivalent
  helper for your table in your own test module (see `is_enabled` in `tests/test_flags.py`)

Minimum tests to copy from `tests/test_authorization.py`, `test_separation_of_duties.py`,
`test_transitions.py` and `test_audit.py`:
1. Each protected route returns 401 anonymous and 403 for wrong roles, **by POSTing directly**.
2. Each SoD rule is rejected (HTTP and service level).
3. Every disallowed transition returns 409 and leaves state and audit unchanged.
4. Every successful write produces exactly one audit event with the right actor, target and details.
5. Persistence: build a second `create_app(db_path=db_path, seed_demo=False)` over the same file and read
   the record back (see `tests/test_end_to_end.py` and `test_change_persists_after_refresh_and_restart`
   in `tests/test_flags.py`).
6. An audit-write failure rolls back the business change (monkeypatch `service.record_event` to raise).

Put your tests in `tests/test_<slug>.py`, then run the whole suite. The refunds tests must still pass.

## 9. Checklist

- [ ] `app/apps/<slug>/` package + templates, registered in `INSTALLED_APPS`
- [ ] `.badge-<status>` CSS for any new statuses
- [ ] `tests/test_<slug>.py`; full `python -m pytest` green
- [ ] README: roles table, a short demo script, architecture tree, limitations
- [ ] Remove your app from the "Coming later" card in `app/core/templates/home.html`
- [ ] `python -m app.core.seed --reset` and click through each role once

## Example: feature-flag admin panel (implemented)

See `app/apps/flags/`. It deliberately does **not** reuse the refund approval workflow: permissions are
`flags.view` (agent, manager, engineer) and `flags.toggle` (engineer); `service.set_flag` is a direct,
reasoned toggle audited as `flag.enabled` / `flag.disabled` with `flag`, `environment`, `previous_value`,
`new_value` and `reason` in `details`. If you later need a change-request flow, add a
`flag_change_requests` table with a `StatusMachine` and SoD like refunds, and keep `set_flag` as the
"apply" step.
