"""Feature Flag Administration: engineers enable/disable synthetic flags with a reason; others view."""
from pathlib import Path

from app.apps.flags.routes import router
from app.apps.flags.schema import SCHEMA_SQL
from app.apps.flags.seed import seed
from app.apps.flags.service import PERMISSIONS
from app.core.registry import InternalApp

APP = InternalApp(
    slug="flags",
    name="Feature Flags",
    description="View synthetic feature flags; engineers enable or disable them with a recorded reason.",
    router=router,
    templates_dir=Path(__file__).parent / "templates",
    schema_sql=SCHEMA_SQL,
    permissions=PERMISSIONS,
    view_permission="flags.view",
    seed=seed,
)
