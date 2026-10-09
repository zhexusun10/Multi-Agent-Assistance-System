"""Offline SQLite, API, and CLI tests for standalone graph import/visualization."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import ServiceUnavailable
from sqlalchemy import create_engine, insert, text
from sqlalchemy.exc import OperationalError

from data.graph import sync as graph
from data.service import app as graph_api
from data import knowledge_graph
from data.graph.source import open_graph_source
from data.storage.models import Agent, Base, Property


@pytest.fixture
def source_path(tmp_path):
    path = tmp_path / "房源 data #1.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(Agent), [{"agent_id": 9, "name": "Fallback agent", "agency_name": "Agency"}])
        connection.execute(insert(Property), [
            {"listing_id": lid, "listing_type": "RENT", "title": f"Listing {lid}",
             "price": 1000 + lid, "project_id": 0 if lid == 1 else 7, "agent_id": 9,
             "agent_name": "Listing agent" if lid == 3 else None,
             "district_code": "D05", "nearest_mrt": "  Jurong East  ",
             "mrt_distance_m": 125, "agent_phone": "private"}
            for lid in (1, 3, 8, 20)
        ])
    engine.dispose()
    return path


def test_sqlite_read_only_keyset_and_agent_fallback(source_path):
    source = open_graph_source(sqlite_path=source_path)
    try:
        batches = list(graph.iter_listing_batches(source, batch_size=2, limit=3))
        assert [[r["listing_id"] for r in batch] for batch in batches] == [[1, 3], [8]]
        first, second = batches[0]
        assert first["project_id"] is None
        assert first["station"] == "Jurong East"
        assert first["agent_name"] == "Fallback agent"
        assert second["agent_name"] == "Listing agent"
        assert first["props"]["price"] == 1001.0
        assert first["props"]["mrt_distance_m"] == 125
        assert "agent_phone" not in first["props"]
        with source.begin() as connection:
            with pytest.raises(OperationalError, match="readonly"):
                connection.execute(text("DELETE FROM properties"))
    finally:
        source.dispose()


def test_source_selection_is_explicit_and_never_creates_empty_file(tmp_path, monkeypatch):
    missing = tmp_path / "typo.db"
    with pytest.raises(FileNotFoundError):
        open_graph_source(sqlite_path=missing)
    assert not missing.exists()
    monkeypatch.delenv("PROPERTYGURU_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="Specify"):
        open_graph_source()
    with pytest.raises(ValueError, match="either"):
        open_graph_source(sqlite_path=missing, database_url="postgresql://localhost/db")
    with pytest.raises(ValueError, match="read-only"):
        open_graph_source(database_url="sqlite:///not-allowed.db")


def test_graph_progress_only_reports_committed_batches(monkeypatch):
    progress = []
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.execute_write.side_effect = [None, ServiceUnavailable("offline")]
    monkeypatch.setattr(graph, "iter_listing_batches", lambda *a, **kw: iter([
        [{"listing_id": 1}, {"listing_id": 2}], [{"listing_id": 3}, {"listing_id": 4}],
    ]))
    with pytest.raises(ServiceUnavailable):
        graph.sync_graph(Mock(), driver=driver, batch_size=2, progress_callback=progress.append)
    assert progress == [2]


def test_no_implicit_neo4j_password(monkeypatch):
    monkeypatch.delenv("NEO4J_AUTH", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="NEO4J_PASSWORD"):
        graph.create_graph_driver()


@pytest.mark.parametrize("uri", ["bolt://127.0.0.1:7687", "neo4j://localhost:7687", "bolt://[::1]:7687"])
def test_explicit_local_no_auth_needs_no_password(monkeypatch, uri):
    monkeypatch.setenv("NEO4J_AUTH", "none")
    monkeypatch.setenv("NEO4J_URI", uri)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    factory = Mock()
    monkeypatch.setattr("neo4j.GraphDatabase.driver", factory)
    assert graph.create_graph_driver() is factory.return_value
    assert factory.call_args.kwargs["auth"] is None
    assert factory.call_args.args[0] == uri


@pytest.mark.parametrize("uri", ["bolt://0.0.0.0:7687", "bolt://192.168.1.10:7687", "neo4j+s://example.com"])
def test_no_auth_is_refused_for_non_loopback_servers(monkeypatch, uri):
    monkeypatch.setenv("NEO4J_AUTH", "none")
    monkeypatch.setenv("NEO4J_URI", uri)
    with pytest.raises(ValueError, match="loopback"):
        graph.graph_auth()


def test_explicit_and_legacy_password_auth(monkeypatch):
    monkeypatch.delenv("NEO4J_AUTH", raising=False)
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-password")
    assert graph.graph_auth() == ("neo4j", "test-password")
    monkeypatch.setenv("NEO4J_AUTH", "custom/another/password")
    assert graph.graph_auth() == ("custom", "another/password")


@pytest.mark.parametrize("configured", ["false", "user/", "/password"])
def test_malformed_auth_config_is_not_an_auth_bypass(monkeypatch, configured):
    monkeypatch.setenv("NEO4J_AUTH", configured)
    with pytest.raises(ValueError, match="NEO4J_AUTH"):
        graph.graph_auth()


class Node(dict):
    def __init__(self, element_id, label, **properties):
        super().__init__(properties)
        self.element_id = element_id
        self.labels = {label}


class Relationship(dict):
    def __init__(self, element_id, kind, start, end, **props):
        super().__init__(props)
        self.element_id, self.type = element_id, kind
        self.start_node, self.end_node = start, end


def graph_records():
    listing = Node("l1", "Listing", listing_id=1, title="<script>not HTML</script>",
                   price=1000.0, listing_type="RENT", agent_phone="private", raw_json="private")
    mrt = Node("m1", "MRT", name="Station", license="private")
    relation = Relationship("r1", "NEAR_MRT", listing, mrt, distance_m=125, walking_mins=3, secret="private")
    return [{"l": listing, "r": relation, "n": mrt}, {"l": listing, "r": relation, "n": mrt}]


def test_graph_serialization_is_deduplicated_and_whitelisted():
    data = graph_api.serialize_graph(graph_records())
    assert len(data["nodes"]) == 2 and len(data["edges"]) == 1
    assert data["nodes"][0]["properties"] == {
        "listing_id": 1, "title": "<script>not HTML</script>", "price": 1000.0, "listing_type": "RENT"}
    assert data["edges"][0]["properties"] == {"distance_m": 125, "walking_mins": 3}
    assert "private" not in str(data)
    isolated = graph_api.serialize_graph([{"l": Node("x", "Listing", listing_id=8), "r": None, "n": None}])
    assert len(isolated["nodes"]) == 1 and not isolated["edges"]


def test_versioned_graph_routes_and_parameter_binding(monkeypatch):
    calls = []
    def read(driver, db, query, **params):
        calls.append((query, params))
        if query == graph_api.GRAPH_QUERY or query == graph_api.EXPAND_QUERY:
            return graph_records()
        return [{"ok": 1}]
    monkeypatch.setattr(graph_api, "read_query", read)
    borrowed_driver = Mock()
    with TestClient(graph_api.create_app(driver=borrowed_driver, database="test")) as client:
        assert client.get("/health").json() == {"status": "ok", "database": "test"}
        injection = "x') DETACH DELETE n //"
        response = client.post("/api/v1/graph/search", json={
            "filters": {"q": injection, "district": "D05"}, "page_size": 2})
        assert response.status_code == 200
        assert response.json()["listing_count"] == 1
        assert calls[-1][1]["q"] == injection
        assert injection not in calls[-1][0] and "$q" in calls[-1][0]
        expanded = client.post("/api/v1/graph/expand", json={"entity_id": "Listing:1", "page_size": 1})
        assert expanded.status_code == 200 and expanded.json()["has_more"]
        assert expanded.json()["next_cursor"] is not None
        assert calls[-1][1]["node_id"] == "Listing:1" and calls[-1][1]["limit"] == 2
        for body in ({"page_size": 1001}, {"page_size": 0}, {"filters": {"district": "D29"}},
                     {"filters": {"listing_type": "delete"}}, {"filters": {"min_price": -1}},
                     {"filters": {"min_price": 20, "max_price": 10}}, {"cypher": "DELETE n"}):
            assert client.post("/api/v1/graph/search", json=body).status_code == 422
        assert client.post("/api/v1/graph/expand", json={"entity_id": "x" * 201}).status_code == 422
        assert client.get("/api/v1/graph/search").status_code == 405
    borrowed_driver.close.assert_not_called()


def test_api_connection_errors_and_missing_nodes(monkeypatch):
    monkeypatch.setattr(graph_api, "read_query", lambda *a, **kw: [])
    with TestClient(graph_api.create_app(driver=Mock())) as client:
        assert client.post("/api/v1/graph/search", json={}).json()["nodes"] == []
        missing = client.post("/api/v1/graph/expand", json={"entity_id": "Listing:999"})
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "HTTP_404"
        def unavailable(*args, **kwargs):
            raise ServiceUnavailable("server secret details")
        monkeypatch.setattr(graph_api, "read_query", unavailable)
        response = client.get("/health")
        assert response.status_code == 503 and "secret" not in response.text
        assert client.get("/api/stats").status_code == 503
        assert client.get("/api/v1/stats").status_code == 503


def test_owned_api_driver_is_closed(monkeypatch):
    driver = Mock()
    monkeypatch.setattr(graph_api, "create_graph_driver", lambda: driver)
    with TestClient(graph_api.create_app()):
        pass
    driver.close.assert_called_once()


def test_cli_import_failure_is_nonzero_and_limit_validation(monkeypatch):
    monkeypatch.setattr(knowledge_graph, "import_graph", Mock(side_effect=ServiceUnavailable("offline")))
    assert knowledge_graph.main(["import", "--sqlite", "data/propertyguru.db"]) == 1
    for args in (["import", "--limit", "-1"], ["import", "--batch-size", "0"],
                 ["import", "--sqlite", "x.db", "--database-url", "postgresql://localhost/db"]):
        with pytest.raises(SystemExit):
            knowledge_graph.main(args)
