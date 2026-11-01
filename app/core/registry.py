"""Registry of internal applications. Each app exposes one InternalApp spec."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from fastapi import APIRouter


@dataclass(frozen=True)
class InternalApp:
    slug: str                      # URL segment: mounted at /apps/<slug>
    name: str                      # shown on home page and nav
    description: str
    router: APIRouter
    templates_dir: Path            # contains a <slug>/ subfolder of templates
    schema_sql: str = ""           # CREATE TABLE IF NOT EXISTS ... for this app
    permissions: dict[str, set[str]] = field(default_factory=dict)
    view_permission: str | None = None  # used only to badge/hide the nav entry
    seed: Callable[[sqlite3.Connection], None] | None = None  # demo data, runs once on empty DB

    @property
    def url(self) -> str:
        return f"/apps/{self.slug}"
