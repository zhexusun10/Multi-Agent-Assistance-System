"""Optional audit of the local SQLite snapshot, never the configured production PG."""
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from data.graph.source import open_graph_source
from data.storage.models import Property


@pytest.fixture
def snapshot():
    path = Path(__file__).resolve().parents[1] / "propertyguru.db"
    if not path.is_file():
        pytest.skip("Local snapshot not present; unit tests do not require crawled data")
    engine = open_graph_source(sqlite_path=path)
    try:
        yield engine
    finally:
        engine.dispose()


def test_database_has_crawled_data(snapshot):
    with snapshot.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Property)) >= 20000


def test_sparsity_calculation(snapshot):
    with snapshot.connect() as connection:
        rows = connection.execute(text("SELECT listing_id, listing_type, price, bedrooms, bathrooms FROM properties LIMIT 100")).mappings().all()
    assert len(rows) == 100
    assert all(row[field] is not None for row in rows for field in ("listing_id", "price", "bedrooms"))
