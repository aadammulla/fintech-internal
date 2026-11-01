# How to add an internal app

This guide walks through adding a new tool, using a **KYC Review Queue** as the example. The
Refunds Dashboard (`app/apps/refunds/`) is the reference implementation. Copy its structure.

Expect to write about five small files and change one line in `app/main.py`. You shouldn't need to
touch anything in `app/core/`.

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
| Status rules | `StatusMachine({...}).ensure_transition(old, new)` | `app/core/workflow.py` |
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

Conventions:
- Permission names: `<app>.<capability>`. Audit actions: `<entity>.<past_tense_verb>`.
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
re-render the form with `e.errors` and `status_code=400` (see `routes.create` in refunds).

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

`ui.status_badge` styles any status. Add a `.badge-<status>` rule in `style.css` for colours.

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
functions** (see `app/apps/refunds/seed.py`) so the demo audit trail is consistent. Use fictional
customers (`CUS-######`) and the seeded users from `app/core/seed.py::DEMO_USERS`.

If you need a new role (e.g. `compliance`), add it to `ROLES` in `app/core/identity.py`, the `CHECK`
on `users.role` in `app/core/db.py`, and `DEMO_USERS`. Then reset the DB.

## 8. Tests

Reuse the fixtures in `tests/conftest.py`:
- `client_as(email)` gives a `TestClient` with a simulated identity selected through the real `/identity` route
- `conn` and `user(email)` for calling service functions directly
- `audit_count(conn, action, target_id)` and `refund_status`-style helpers

Minimum tests to copy from `tests/test_authorization.py`, `test_separation_of_duties.py`,
`test_transitions.py` and `test_audit.py`:
1. Each protected route returns 401 anonymous and 403 for wrong roles, **by POSTing directly**.
2. Each SoD rule is rejected (HTTP and service level).
3. Every disallowed transition returns 409 and leaves state and audit unchanged.
4. Every successful write produces exactly one audit event with the right actor, target and details.

## Example: feature-flag admin panel

The same pieces apply: a `feature_flags` table and a `flag_change_requests` table; permissions
`flags.view` (all), `flags.request_change` (engineer), `flags.approve_change` (manager); a
`StatusMachine({"requested": {"approved","rejected"}, "approved": {"applied"}})`; SoD "requester ≠ approver";
and audit actions `flag_change.requested/approved/applied` with before/after values in `details`.
