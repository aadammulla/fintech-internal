# Fintech Internal Tools Starter Kit

A proof-of-concept foundation for building internal tools with Python, FastAPI, SQLite and
server-rendered HTML, plus two apps built on it: the **Refunds Dashboard** and **Feature Flag
Administration**.

> **Not production software.** Identity is *simulated* (anyone can pick any employee from a
> dropdown), there is no CSRF protection, all data is synthetic, and no payments are processed.
> It is meant to show whether a small reusable foundation can carry many internal workflows.

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/aadammulla/fintech-internal.git
cd fintech-internal
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m app                        # http://127.0.0.1:8000
```

On first start the app creates `data/fintech_internal.db` and seeds synthetic users, refunds and feature flags.

> **Upgrading an existing demo DB?** Seed data only runs when the database has no users, so a DB created
> before an app was added will show that app empty. Run `python -m app.core.seed --reset` once.

| Task | Command |
|---|---|
| Run tests | `python -m pytest` (also runs in CI on every PR and push to `main`: `.github/workflows/tests.yml`, Python 3.12) |
| Reset demo data | `python -m app.core.seed --reset` |
| Dev server with auto-reload | `RELOAD=1 python -m app` (or `uvicorn app.asgi:app --reload`) |
| Different port / DB file | `PORT=8080 FINTECH_DB_PATH=/tmp/demo.db python -m app` |
| Start with an empty DB | `FINTECH_SEED_DEMO=0 python -m app` |

## Demo script (about 3 minutes)

Seeded identities (all fictional, `@example.test`):

| Name | Role | Can do |
|---|---|---|
| Priya Raman, Marcus Chen | agent | view and create refunds; view feature flags |
| Dana Okafor, Luis Ortega | manager | view, create, approve/reject refunds (not their own); view feature flags; view audit log |
| Sam Whitfield | engineer | view refunds; view **and enable/disable** feature flags |

1. Open http://127.0.0.1:8000. Note the yellow **simulated identity** banner and selector.
2. Select **Priya Raman (agent)** → **Refunds Dashboard** → **New refund request**.
   Enter `CUS-246810`, `149.99`, a reason, and submit. You land on the new pending request.
   There are no Approve/Reject buttons for agents. Even if you POST to
   `/apps/refunds/<id>/approve` directly, the server returns **403** and records an `access.denied` audit event:
   `curl -i -X POST -b sim_user_id=1 http://127.0.0.1:8000/apps/refunds/1/approve`
3. Switch to **Dana Okafor (manager)**, open the request, add a note and click **Approve**.
4. Refresh the page (or restart the server). The status stays **approved** because it's stored in SQLite.
5. Open **Audit log** (managers only) and see `refund.created` and `refund.approved` with actor, target, timestamp and details.
6. Separation of duties: as Dana, open `RF-00007` (a pending request Dana created herself).
   The UI tells her another manager must decide. A direct POST returns 403. Switch to Luis Ortega to approve it.
7. Invalid transitions: try to approve an already approved or rejected request (e.g. via curl). The server returns **409**.

## Feature flag demo (about 2 minutes)

Flags are synthetic (nothing reads them). Each row is one flag in one environment
(`development`, `staging`, `production`). Changes are direct toggles by engineers, with no approval step.

1. Select **Sam Whitfield (engineer)** → **Feature Flags**. You see name, description, environment,
   status and last change. Filter by environment if you like.
2. Open `kyc_document_upload_v2` (production, **disabled**). Enter a reason (required, 5–500 characters)
   and click **Enable in production**. Submitting an empty reason re-renders the form with a **400** error and
   changes nothing.
3. Refresh the page (or restart the server). The flag stays **enabled** because it's stored in SQLite.
   The **Change history** table shows `flag.enabled` with actor, timestamp, flag, environment,
   `previous_value`, `new_value` and `reason`. `dispute_auto_triage` (staging) already has one seeded change.
4. Switch to **Priya Raman (agent)** or **Dana Okafor (manager)**. They can view flags and history,
   but there is no form. Direct POSTs are rejected with **403** and recorded as `access.denied`
   (the flag ids below match a freshly seeded DB; `sim_user_id` 1 = Priya, 3 = Dana, 5 = Sam):
   ```bash
   curl -i -X POST -b sim_user_id=1 -d "reason=agent tries anyway" http://127.0.0.1:8000/apps/flags/4/disable
   curl -i -X POST -b sim_user_id=3 -d "reason=manager tries anyway" http://127.0.0.1:8000/apps/flags/4/disable
   ```
5. Invalid requests: enabling an already-enabled flag returns **409**, an unknown flag **404**, and a
   missing reason **400**, e.g. `curl -i -X POST -b sim_user_id=5 http://127.0.0.1:8000/apps/flags/4/disable`.
6. As Dana, open **Audit log** and filter by target type `feature_flag` to see every flag change
   alongside the refund events.

## Architecture

```
app/
  main.py               create_app(): registers apps, inits DB, seeds, mounts routers  ← INSTALLED_APPS lives here
  asgi.py / __main__.py ASGI entrypoint / `python -m app`
  config.py             FINTECH_DB_PATH, FINTECH_SEED_DEMO
  core/                 shared foundation (no app-specific code)
    db.py               connect(), transaction(), get_db dependency, CORE_SCHEMA (users, audit_events + triggers)
    identity.py         SIMULATED identity: cookie → User; current_user / require_user dependencies
    authz.py            permission registry; can(), ensure_permission(), permission_required()
    audit.py            record_event() (insert-only), list_events()
    workflow.py         StatusMachine: declarative allowed status transitions
    errors.py           DomainError → NotAuthenticated 401, PermissionDenied 403, NotFound 404,
                        InvalidTransition 409, ValidationError 400
    handlers.py         maps DomainErrors to HTML error pages; audits denied access
    templating.py       shared Jinja env, money/dt filters, render() helper
    registry.py         InternalApp spec (slug, name, router, schema, permissions, seed, templates)
    routes.py           /, /identity, /audit, /healthz
    seed.py             synthetic users; runs each app's seed; `--reset` CLI
    templates/          base.html (layout + identity selector), macros.html, home, audit, error
    static/style.css
  apps/
    refunds/
      __init__.py       APP = InternalApp(...)
      schema.py         refund_requests table (+ CHECK decided_by <> requested_by)
      service.py        business rules + authorization + audit (the source of truth)
      routes.py         thin HTTP layer
      seed.py           synthetic refunds created through the service (so they're audited too)
      templates/refunds/ list.html, new.html, detail.html
    flags/              same layout: feature_flags table, direct engineer-only toggles with a reason
      __init__.py, schema.py, service.py, routes.py, seed.py, templates/flags/ list.html, detail.html
tests/                  pytest: authorization, separation of duties, transitions, audit, end-to-end,
                        test_flags.py (feature flags)
docs/HOW_TO_ADD_AN_APP.md
```

Key design choices:

- **Authorization is enforced in the service layer** (`ensure_permission` at the top of every
  service function), and routes add `Depends(permission_required(...))` as a second layer.
  Templates use `can()` only to hide buttons. Tests POST directly to prove hidden buttons don't matter.
- **Permissions, not roles, in code.** Each app declares `{"refunds.decide": {"manager"}, ...}`.
  Unknown permission names fail closed (raise).
- **Separation of duties** is checked in `service._decide` and also by a SQLite `CHECK` constraint as a backstop.
- **Status transitions** go through `StatusMachine.ensure_transition` plus a conditional
  `UPDATE ... WHERE status = <expected>`, so a concurrent double-decision becomes a 409 instead of being silently overwritten.
- **Audit events are written in the same transaction** as the change. If either fails, both roll back.
  Actor name and role are snapshotted on the event.
- **Audit log is append-only via the app.** There's no update/delete code path or route, and SQLite
  triggers abort `UPDATE`/`DELETE` on `audit_events`. This is **not tamper-proof**: anyone with
  file access can drop the triggers or edit the DB.
- **Denied attempts are audited** (`access.denied`) from the exception handler.
- Money is stored as integer cents. Amounts must be > 0, ≤ $10,000.00, with at most 2 decimals.

## Limitations and shortcuts

- **Simulated identity only.** A plain cookie holds the user ID. Swap `identity.current_user` for SSO/OIDC before any real use.
- **No CSRF tokens.** The cookie is `SameSite=Lax`, which helps but isn't sufficient for production.
- **Audit log** is append-only through the app, not tamper-evident (no hash chain, no WORM storage, no export).
- **SQLite, one process.** No migrations framework: schemas are `CREATE TABLE IF NOT EXISTS`, so
  changing a table means resetting the DB or adding a migration tool (e.g. Alembic or numbered SQL files).
- **Fixed roles** (agent/manager/engineer) enforced by a `CHECK` constraint. No per-user overrides,
  no user admin UI, and no multi-level approval thresholds.
- Feature flags are synthetic rows only: no SDK, no evaluation, no flag creation/deletion UI, and no
  approval step (engineers toggle directly; every change needs a reason and is audited).
- Lists are capped (200 refunds, 500 audit events) with no pagination. No currency other than USD.
- Seeded records all share the seed timestamp.
- No payment processing or external integrations.
- Not reviewed for security or compliance (PCI, SOC 2, etc.). This doesn't replace Power Apps feature-for-feature.
