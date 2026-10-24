"""Serve recommendation/app.py from THIS tree for the P7 live smoke (never the running 8010/8091/8092 services).

Required environment (set by live_smoke.py): P7_SMOKE_PORT, P7_SMOKE_TOKEN, RECOMMENDATION_STATE_PATH (a temporary
file), RECOMMENDATION_CONTROLLER (scripted | live), RECOMMENDATION_DATA_MODE=database. Runtime selections set
before the import win over the project .env (app.load_project_environment); the service token is a credential that
the import loads from .env, so it is replaced here with the smoke token AFTER the import (authorize() reads it per
request). Refuses to start when the state path is the live state file.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
for key in ("P7_SMOKE_PORT", "P7_SMOKE_TOKEN", "RECOMMENDATION_STATE_PATH", "RECOMMENDATION_CONTROLLER"):
    if not os.environ.get(key):
        raise SystemExit(f"{key} is required")
import uvicorn  # noqa: E402
import recommendation.app as app  # noqa: E402

state = Path(app.state_path()).resolve()
if state == (ROOT / "data/.runtime/recommendation/state.sqlite").resolve() or "data/.runtime" in str(state):
    raise SystemExit("refusing to serve the live state file")
os.environ["RECOMMENDATION_SERVICE_TOKEN"] = os.environ["P7_SMOKE_TOKEN"]
uvicorn.run(app.app, host="127.0.0.1", port=int(os.environ["P7_SMOKE_PORT"]), log_level="warning")
