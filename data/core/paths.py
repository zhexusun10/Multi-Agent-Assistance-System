"""Module-local data paths, independent of the caller's working directory.

Only explicit user paths are resolved against the caller's cwd. Defaults always
stay in this data module; shared .env and the Python environment stay at project root.
"""
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
DATA_DIR = CORE_DIR.parent
PROJECT_DIR = DATA_DIR.parent
ENV_PATH = PROJECT_DIR / ".env"

SQLITE_PATH = DATA_DIR / "propertyguru.db"
EXPORT_DIR = DATA_DIR / "exports"
RUNTIME_DIR = DATA_DIR / ".runtime"
PLACE_SNAPSHOT = DATA_DIR / "datasets" / "singapore_places.json"
