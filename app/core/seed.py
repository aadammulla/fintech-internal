"""Synthetic demo data. All names, emails and customer ids are fictional.

Usage:  python -m app.core.seed --reset   # delete the DB file and reseed
"""
from __future__ import annotations

import argparse
import sqlite3

from app.core import authz
from app.core.identity import create_user

DEMO_USERS = [
    # name, email, role, title
    ("Priya Raman", "priya.raman@example.test", "agent", "Customer Support Agent"),
    ("Marcus Chen", "marcus.chen@example.test", "agent", "Customer Support Agent"),
    ("Dana Okafor", "dana.okafor@example.test", "manager", "Support Operations Manager"),
    ("Luis Ortega", "luis.ortega@example.test", "manager", "Payments Risk Manager"),
    ("Sam Whitfield", "sam.whitfield@example.test", "engineer", "Platform Engineer"),
]


def is_empty(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def seed_users(conn: sqlite3.Connection) -> None:
    for name, email, role, title in DEMO_USERS:
        create_user(conn, name=name, email=email, role=role, title=title)


def seed_all(conn: sqlite3.Connection, apps) -> None:
    # App seeds go through service functions, which check permissions, so the
    # registry must be populated even when running outside create_app().
    authz.register_app_permissions(apps)
    seed_users(conn)
    for spec in apps:
        if spec.seed:
            spec.seed(conn)


def main(argv: list[str] | None = None) -> None:
    from app.config import db_path_from_env
    from app.core.db import connect, init_db
    from app.main import INSTALLED_APPS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete the database file first")
    args = parser.parse_args(argv)
    path = db_path_from_env()
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            p = path.with_name(path.name + suffix)
            if p.exists():
                p.unlink()
    init_db(path, [a.schema_sql for a in INSTALLED_APPS])
    conn = connect(path)
    try:
        if is_empty(conn):
            seed_all(conn, INSTALLED_APPS)
            print(f"Seeded demo data into {path}")
        else:
            print(f"{path} already has data; use --reset to start over")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
