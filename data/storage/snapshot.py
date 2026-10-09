"""The immutable PropertyGuru listing snapshot, not a live availability feed."""
from sqlalchemy import func, inspect, select

from data.core.paths import SQLITE_PATH
from data.graph.source import open_graph_source
from data.storage.models import Agent, PriceHistory, Property, PropertyImage
from data.storage.repository import readonly_connection

SNAPSHOT_DATE = "2026-10-09"
SNAPSHOT_COUNTS = {"properties": 21431, "agents": 4657, "images": 418700, "price_history": 0}
TABLES = {"properties": Property, "agents": Agent, "images": PropertyImage, "price_history": PriceHistory}


def snapshot_info():
    # A declared dataset contract; actual installation is checked by `verify`.
    return {"date": SNAPSHOT_DATE, "immutable": True, "expected_counts": dict(SNAPSHOT_COUNTS),
            "valid_coordinates": 8631, "unknown_coordinates": 12800,
            "current_availability_verified": False}


def sql_counts(engine, *, allow_missing=False):
    with readonly_connection(engine) as connection:
        catalog = inspect(connection)
        return {key: (connection.scalar(select(func.count()).select_from(model))
                      if not allow_missing or catalog.has_table(model.__tablename__) else 0)
                for key, model in TABLES.items()}


def assert_snapshot(counts, expected=None):
    expected = SNAPSHOT_COUNTS if expected is None else expected
    if counts != expected:
        raise ValueError(f"Not the fixed {SNAPSHOT_DATE} snapshot: expected {expected}, found {counts}. "
                         "Use the team's snapshot/backup; do not crawl new listings into this database.")


def prepare_postgres_snapshot(engine, *, sqlite_path=SQLITE_PATH, batch_size=500):
    """Initialize an EMPTY dedicated PG database; never overwrite a populated one."""
    if engine.dialect.name != "postgresql":
        raise ValueError("prepare-postgres requires a dedicated PostgreSQL database")
    if engine.url.database in {"postgres", "template0", "template1", "multi_agent_assistance", "multi_agent_assistance_test"}:
        raise ValueError("Refusing to import listings into an administrative or Agent Runtime database")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    counts = sql_counts(engine, allow_missing=True)
    if counts == SNAPSHOT_COUNTS:
        return {"status": "already_prepared", "snapshot": snapshot_info(), "counts": counts}
    if any(counts.values()):
        assert_snapshot(counts)  # Fail closed; manual migration can resume a known partial import.
    source = open_graph_source(sqlite_path=sqlite_path)
    try:
        assert_snapshot(sql_counts(source))
    finally:
        source.dispose()
    from data.storage.migration import migrate_sqlite_to_target
    migrate_sqlite_to_target(source_sqlite_path=str(sqlite_path),
                             target_db_url=engine.url.render_as_string(hide_password=False), batch_size=batch_size)
    counts = sql_counts(engine)
    assert_snapshot(counts)
    return {"status": "imported", "snapshot": snapshot_info(), "counts": counts}
