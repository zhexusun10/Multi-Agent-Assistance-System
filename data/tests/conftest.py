"""Allow the data package to be imported in all offline tests."""
import sys
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _DATA_DIR.parent

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
