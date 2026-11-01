import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "fintech_internal.db"


def db_path_from_env() -> Path:
    return Path(os.environ.get("FINTECH_DB_PATH", DEFAULT_DB_PATH))


def seed_demo_from_env() -> bool:
    return os.environ.get("FINTECH_SEED_DEMO", "1") != "0"
