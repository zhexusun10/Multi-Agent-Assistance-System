"""Graph projection tests: no PostgreSQL or Neo4j service required."""
import argparse
from decimal import Decimal
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import graph
import main


def record(listing_id, **overrides):
    row = {field: None for field in graph.LISTING_FIELDS}
    row.update(listing_id=listing_id, project_id=None, agent_id=None,
               agent_name=None, agency_name=None, district_code=None, nearest_mrt=None)
    row.update(overrides)
    return row


class FakeResult:
    def consume(self):
        return None


class FakeConnection:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, stmt):
        self.engine.queries.append(str(stmt))
        # SQLAlchemy keyset + LIMIT: read bound parameters rather than assuming
        # any particular rendered parameter name.
        cursor = stmt._where_criteria[0].right.value
        size = stmt._limit_clause.value
        rows = [r for r in self.engine.records if r["listing_id"] > cursor][:size]
        if rows:
            self.engine.cursor = rows[-1]["listing_id"]
        return FakeRows(rows)


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self.rows


class FakeEngine:
    def __init__(self, records):
        self.records = records
        self.cursor = 0
        self.queries = []

    def connect(self):
        return FakeConnection(self)


class FakeSession:
    def __init__(self):
        self.constraints = []
        self.batches = []
        self.nodes = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def run(self, cypher, **kwargs):
        if "rows" in kwargs:
            return self.run_batch(cypher, kwargs["rows"])
        self.constraints.append(cypher)
        return FakeResult()

    def execute_write(self, callback, rows):
        callback(self, rows)

    def run_batch(self, cypher, rows):
        assert "$rows" in cypher and "UNWIND $rows" in cypher
        assert "DELETE old" in cypher and "SET l = row.props" in cypher
        self.batches.append(rows)
        for row in rows:
            # Model MERGE + replacement of the four owned relationships.
            self.nodes[row["listing_id"]] = row
        return FakeResult()


class FakeDriver:
    def __init__(self):
        self.graph = FakeSession()
        self.databases = []
        self.verified = False

    def verify_connectivity(self):
        self.verified = True

    def session(self, *, database):
        self.databases.append(database)
        return self.graph


def test_mapping_whitelist_and_missing_identifiers():
    row = record(42, price=Decimal("1000000.25"), floor_area_sqft=Decimal("812.50"),
                 url="https://example.org/42", listing_type="SALE", project_id=0,
                 agent_id=None, nearest_mrt="  ", district_code=" ")
    row.update(agent_phone="secret", raw_json={"private": "secret"}, detail_images=["huge"])
    mapped = graph.map_listing(row)
    assert mapped["props"] == {"listing_id": 42, "price": 1000000.25,
                                "floor_area_sqft": 812.5, "url": "https://example.org/42",
                                "listing_type": "SALE"}
    assert all(mapped[key] is None for key in ("project_id", "agent_id", "station", "district_code"))
    assert graph.map_listing(record(None)) is None
    assert "agent_phone" not in graph.UPSERT_LISTINGS


def test_batches_replay_updates_and_clears_relationships():
    original = [record(i, title="old", price=Decimal("12"), project_id=7,
                       agent_id=9, district_code="D05", nearest_mrt="Jurong East") for i in (1, 2, 3, 4, 5)]
    driver = FakeDriver()
    engine = FakeEngine(original)
    assert graph.sync_graph(engine, driver=driver, batch_size=2, limit=3, database="test") == 3
    assert [len(b) for b in driver.graph.batches] == [2, 1]
    assert len(engine.queries) == 2
    assert driver.verified and driver.databases == ["test"]
    assert len(driver.graph.constraints) == 5
    assert all("IF NOT EXISTS" in c for c in driver.graph.constraints)
    first = driver.graph.nodes[1]
    assert first["project_id"] == 7 and first["agent_id"] == 9
    changed = record(1, title="new", price=None, project_id=None,
                     agent_id=0, district_code=None, nearest_mrt="")
    assert graph.sync_graph(FakeEngine([changed]), driver=driver, batch_size=1) == 1
    assert driver.graph.nodes[1]["props"] == {"listing_id": 1, "title": "new"}
    assert all(driver.graph.nodes[1][key] is None for key in
               ("project_id", "agent_id", "district_code", "station"))
    assert graph.sync_graph(FakeEngine([changed]), driver=driver) == 1
    assert driver.graph.nodes[1] == graph.map_listing(changed)


def test_empty_and_invalid_limits():
    driver = FakeDriver()
    assert graph.sync_graph(FakeEngine([]), driver=driver, limit=0) == 0
    assert not driver.graph.batches
    with pytest.raises(ValueError):
        graph.sync_graph(FakeEngine([]), driver=driver, batch_size=0)
    with pytest.raises(ValueError):
        graph.sync_graph(FakeEngine([]), driver=driver, limit=-1)


def test_cli_graph_failure_is_nonzero_and_run_does_not_sync_on_crawl_error(monkeypatch):
    with patch("graph.sync_graph", side_effect=OSError("bolt offline")):
        with pytest.raises(SystemExit) as exc:
            main.handle_sync_graph(argparse.Namespace(limit=None, batch_size=2))
    assert exc.value.code == 1

    from pipeline import PipelineStats
    class FailedPipeline:
        def run_sync(self, **kwargs):
            return PipelineStats(total_errors=1)
    monkeypatch.setattr(main, "IngestionPipeline", lambda: FailedPipeline())
    args = argparse.Namespace(verbose=False, skip_details=True, skip_price_history=True,
                              districts="D05", postal_range=None, all_pages=False, pages=1,
                              type="sale", start_page=1, concurrency=1, sync_graph=True)
    with patch.object(main, "handle_sync_graph") as sync:
        with pytest.raises(SystemExit):
            main.handle_run(args)
    sync.assert_not_called()


def test_run_syncs_only_after_successful_ingestion(monkeypatch):
    from pipeline import PipelineStats
    class SuccessfulPipeline:
        def run_sync(self, **kwargs):
            return PipelineStats(total_upserted=2)
    monkeypatch.setattr(main, "IngestionPipeline", lambda: SuccessfulPipeline())
    args = argparse.Namespace(verbose=False, skip_details=True, skip_price_history=True,
                              districts="D05", postal_range=None, all_pages=False, pages=1,
                              type="sale", start_page=1, concurrency=1, sync_graph=True)
    with patch.object(main, "handle_sync_graph") as sync:
        main.handle_run(args)
    sync.assert_called_once_with(args)
    args.sync_graph = False
    with patch.object(main, "handle_sync_graph") as sync:
        main.handle_run(args)
    sync.assert_not_called()
