"""Opt-in real PostgreSQL + Neo4j + HTTP contract acceptance, entirely read-only.

PROPERTYGURU_VERIFY_LIVE=1 uses the prepared fixed snapshot, not mock drivers.
It does not clear/rebuild Neo4j or create SQL data. A partial/mismatched install fails.
"""
import os

import pytest
from fastapi.testclient import TestClient

from data.service.validation import verify_data
from data.graph.sync import create_graph_driver
from data.service.app import create_app
from data.graph.source import load_graph_env, open_api_source


def test_live_fixed_snapshot_sql_neo4j_and_http():
    load_graph_env()
    if os.getenv("PROPERTYGURU_VERIFY_LIVE") != "1":
        pytest.skip("set PROPERTYGURU_VERIFY_LIVE=1 after preparing the real fixed snapshot")
    engine = open_api_source()
    try:
        assert engine.dialect.name == "postgresql", "Live acceptance requires PostgreSQL, not SQLite"
        database = os.getenv("NEO4J_DATABASE", "neo4j")
        with create_graph_driver() as driver:
            with TestClient(create_app(driver=driver, sql_engine=engine, database=database)) as client:
                result = verify_data(engine, driver, database, client=client)
        assert result["status"] == "ok" and result["read_only"]
        assert result["http"]["paginated_listings"] == 21431
        assert result["graph"]["valid_coordinates"] == 8631
        assert result["graph"]["unknown_coordinates"] == 12800
        assert result["graph"]["missing_proximity_edges"] == 0
        assert result["graph"]["duplicate_proximity_edges"] == 0
    finally:
        engine.dispose()
