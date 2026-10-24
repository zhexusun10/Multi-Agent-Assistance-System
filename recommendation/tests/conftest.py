import pytest
from .isolation import isolate_runtime


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch, tmp_path):
    """Fixture data mode, scripted controller, private state file; live state/connections refused."""
    isolate_runtime(monkeypatch, tmp_path)
