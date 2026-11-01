"""Refunds Dashboard: agents request refunds, managers approve or reject them."""
from pathlib import Path

from app.apps.refunds.routes import router
from app.apps.refunds.schema import SCHEMA_SQL
from app.apps.refunds.seed import seed
from app.apps.refunds.service import PERMISSIONS
from app.core.registry import InternalApp

APP = InternalApp(
    slug="refunds",
    name="Refunds Dashboard",
    description="Create, review, approve and reject customer refund requests.",
    router=router,
    templates_dir=Path(__file__).parent / "templates",
    schema_sql=SCHEMA_SQL,
    permissions=PERMISSIONS,
    view_permission="refunds.view",
    seed=seed,
)
