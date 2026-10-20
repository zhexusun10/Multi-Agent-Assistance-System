"""Module-local data paths, independent of the caller's working directory.

Only explicit user paths are resolved against the caller's cwd. Defaults always
stay in data/; runtime reports live in dataPreprocessing/.runtime/.
Shared .env and Python stay at project root.
"""
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = CORE_DIR.parent.parent
DATA_DIR = PROJECT_DIR / "data"
ENV_PATH = PROJECT_DIR / ".env"

SQLITE_PATH = DATA_DIR / "propertyguru.db"
EXPORT_DIR = DATA_DIR / "exports"
RUNTIME_DIR = PROJECT_DIR / "dataPreprocessing" / ".runtime"
PLACE_SNAPSHOT = DATA_DIR / "datasets" / "singapore_places.json"
