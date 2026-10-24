"""Test isolation from the live runtime (used by recommendation/tests/conftest.py and
tests/test_native_transport.py).

Every test runs in fixture data mode with the scripted controller and a private state file; opening
a StateStore under a live runtime directory, or a database/graph connection, fails the test. Tests
that need other settings set them explicitly after this guard (monkeypatch wins)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE_RUNTIME_DIRS = (ROOT / "data/.runtime", ROOT / "recommendation/.runtime")
# Inherited by subprocesses via os.environ; never needed in fixture mode.
CREDENTIAL_ENV = ("OPENAI_API_KEY", "DATABASE_URL", "TEST_DATABASE_URL", "PROPERTYGURU_DATABASE_URL",
                  "PROPERTYGURU_READ_DATABASE_URL", "NEO4J_AUTH", "NEO4J_USER", "NEO4J_PASSWORD",
                  "NEO4J_URI", "RECOMMENDATION_SERVICE_TOKEN")


def is_live_path(path):
    if str(path) == ":memory:":
        return False
    resolved = Path(path).resolve()
    return any(resolved == d.resolve() or d.resolve() in resolved.parents for d in LIVE_RUNTIME_DIRS)


def isolate_runtime(monkeypatch, tmp_path):
    from recommendation.repositories import connections
    from recommendation.state.store import StateStore

    monkeypatch.setenv("RECOMMENDATION_DATA_MODE", "fixture")
    monkeypatch.setenv("RECOMMENDATION_CONTROLLER", "scripted")
    monkeypatch.setenv("RECOMMENDATION_STATE_PATH", str(tmp_path / "isolated-state.sqlite"))
    for key in ("RECOMMENDATION_CONFIG", "RECOMMENDATION_TRACE_MODE", *CREDENTIAL_ENV):
        monkeypatch.delenv(key, raising=False)
    original = StateStore.__init__

    def guarded(self, path):
        if is_live_path(path):
            raise AssertionError(f"test opened the live recommendation state: {path}")
        original(self, path)

    def refuse(*_args, **_kwargs):
        raise AssertionError("test opened a live database/graph connection (fixture mode only)")

    monkeypatch.setattr(StateStore, "__init__", guarded)
    monkeypatch.setattr(connections, "listing_connection", refuse)
    monkeypatch.setattr(connections, "geographic_connection", refuse)


def subprocess_env(**overrides):
    """Environment for test subprocesses: the isolated os.environ (fixture/scripted/private state,
    no credentials) plus explicit overrides. RECOMMENDATION_* values set here win over the project
    .env in the child (load_environment uses override=False; the app keeps caller selections)."""
    env = {k: v for k, v in os.environ.items() if k not in CREDENTIAL_ENV}
    for key in ("RECOMMENDATION_DATA_MODE", "RECOMMENDATION_CONTROLLER", "RECOMMENDATION_STATE_PATH"):
        assert key in env, f"{key} must be set by isolate_runtime before spawning a subprocess"
    env.update({k: str(v) for k, v in overrides.items()})
    return env
