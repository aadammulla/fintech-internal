"""`python -m app.core.seed --reset` must work standalone, without the web app having started."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.core.seed import DEMO_USERS

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_seed(db_path, *args):
    # A fresh interpreter, so the permission registry starts empty exactly as it does
    # from the shell (in-process, other tests' create_app() calls would have filled it).
    env = {**os.environ, "FINTECH_DB_PATH": str(db_path)}
    return subprocess.run([sys.executable, "-m", "app.core.seed", *args], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


def counts(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("users", "refund_requests", "feature_flags", "audit_events")}
    finally:
        conn.close()


def test_seed_reset_cli_reseeds_from_scratch(tmp_path):
    db_path = tmp_path / "cli.db"

    r = run_seed(db_path, "--reset")
    assert r.returncode == 0, r.stderr
    assert "Seeded demo data" in r.stdout
    seeded = counts(db_path)
    assert seeded["users"] == len(DEMO_USERS)
    assert seeded["refund_requests"] > 0 and seeded["feature_flags"] > 0 and seeded["audit_events"] > 0

    # Without --reset an existing DB is left alone.
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO users (name, email, role, title) VALUES ('Extra', 'extra@example.test', 'agent', 'x')")
    conn.commit()
    conn.close()
    r = run_seed(db_path)
    assert r.returncode == 0, r.stderr
    assert "already has data" in r.stdout
    assert counts(db_path)["users"] == len(DEMO_USERS) + 1

    # --reset deletes the file and reseeds to the same state as a fresh DB.
    r = run_seed(db_path, "--reset")
    assert r.returncode == 0, r.stderr
    assert "Seeded demo data" in r.stdout
    assert counts(db_path) == seeded
