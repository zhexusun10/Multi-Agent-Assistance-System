"""Fixed snapshot, explicit source routing, preparation safety and dual readiness."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from data.service import app as graph_api
from data.graph import source as graph_source
from data.storage import snapshot
from data.storage.models import Base, Property, PropertyImage


@pytest.fixture
def canonical(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'canonical.db').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(Property), {"listing_id": 10, "listing_type": "RENT", "price": 3000})
        connection.execute(insert(PropertyImage), {"listing_id": 10, "source_page": "DETAIL_PAGE",
                                                  "image_type": "PHOTO", "image_url": "https://example.org/photo"})
    yield engine
    engine.dispose()


def test_fixed_snapshot_is_a_declaration_not_current_availability(canonical):
    info = snapshot.snapshot_info()
    assert info["date"] == "2026-10-09" and info["immutable"]
    assert info["current_availability_verified"] is False
    info["expected_counts"]["properties"] = -1
    assert snapshot.SNAPSHOT_COUNTS["properties"] == 21431
    counts = snapshot.sql_counts(canonical)
    assert counts == {"properties": 1, "agents": 0, "images": 1, "price_history": 0}
    with pytest.raises(ValueError, match="fixed 2026-10-09"):
        snapshot.assert_snapshot(counts)


def pg_engine():
    return Mock(dialect=Mock(name="postgresql"), url=make_url("postgresql+psycopg2://writer:secret@localhost/propertyguru"))


def test_prepared_pg_does_not_require_sqlite_or_overwrite(monkeypatch):
    engine = pg_engine()
    engine.dialect.name = "postgresql"
    monkeypatch.setattr(snapshot, "sql_counts", lambda *a, **kw: dict(snapshot.SNAPSHOT_COUNTS))
    source = Mock(side_effect=AssertionError("must not require local snapshot"))
    monkeypatch.setattr(snapshot, "open_graph_source", source)
    assert snapshot.prepare_postgres_snapshot(engine, sqlite_path="missing.db")["status"] == "already_prepared"
    source.assert_not_called()


def test_empty_pg_imports_only_a_valid_snapshot(monkeypatch):
    from data.storage import migration as migrate_pg
    engine = pg_engine()
    engine.dialect.name = "postgresql"
    source = Mock()
    counts = Mock(side_effect=[dict.fromkeys(snapshot.SNAPSHOT_COUNTS, 0),
                               dict(snapshot.SNAPSHOT_COUNTS), dict(snapshot.SNAPSHOT_COUNTS)])
    monkeypatch.setattr(snapshot, "sql_counts", counts)
    monkeypatch.setattr(snapshot, "open_graph_source", Mock(return_value=source))
    migration = Mock()
    monkeypatch.setattr(migrate_pg, "migrate_sqlite_to_target", migration)
    result = snapshot.prepare_postgres_snapshot(engine, sqlite_path="team.db", batch_size=100)
    assert result["status"] == "imported" and "secret" not in str(result)
    assert migration.call_args.kwargs["source_sqlite_path"] == "team.db"
    assert migration.call_args.kwargs["batch_size"] == 100
    source.dispose.assert_called_once()


def test_nonempty_wrong_pg_is_never_overwritten(monkeypatch):
    engine = pg_engine()
    engine.dialect.name = "postgresql"
    monkeypatch.setattr(snapshot, "sql_counts", lambda *a, **kw: {"properties": 1, "agents": 0, "images": 0, "price_history": 0})
    source = Mock(side_effect=AssertionError("must not migrate over nonempty data"))
    monkeypatch.setattr(snapshot, "open_graph_source", source)
    with pytest.raises(ValueError, match="Not the fixed"):
        snapshot.prepare_postgres_snapshot(engine)
    source.assert_not_called()


@pytest.mark.parametrize("database", ["postgres", "multi_agent_assistance", "multi_agent_assistance_test"])
def test_preparation_refuses_admin_and_agent_databases(database):
    engine = pg_engine()
    engine.dialect.name = "postgresql"
    engine.url = engine.url.set(database=database)
    with pytest.raises(ValueError, match="Refusing"):
        snapshot.prepare_postgres_snapshot(engine)


def test_legacy_sqlite_env_cannot_override_pg(monkeypatch):
    monkeypatch.setenv("PROPERTYGURU_API_SQLITE", "wrong-or-missing.db")
    monkeypatch.setenv("PROPERTYGURU_READ_DATABASE_URL", "postgresql://reader@localhost/propertyguru")
    source = Mock()
    monkeypatch.setattr(graph_source, "open_graph_source", source)
    graph_source.open_api_source()
    source.assert_called_once_with(sqlite_path=None, database_url="postgresql://reader@localhost/propertyguru")
    graph_source.open_api_source(sqlite_path="explicit.db")
    assert source.call_args.kwargs == {"sqlite_path": "explicit.db", "database_url": None}
    with pytest.raises(ValueError, match="either"):
        graph_source.open_api_source(sqlite_path="explicit.db", database_url="postgresql://localhost/pg")


def test_ready_probes_both_databases_and_snapshot_contract(canonical, monkeypatch):
    monkeypatch.setattr(graph_api, "read_query", lambda *a, **kw: [{"ok": 1}])
    with TestClient(graph_api.create_app(driver=Mock(), sql_engine=canonical)) as client:
        result = client.get("/ready")
        assert result.status_code == 200 and result.json()["sql_source"] == "sqlite"
        assert result.json()["listing_snapshot"] == snapshot.snapshot_info()
        capabilities = client.get("/api/v1/capabilities").json()
        assert capabilities["readiness"] == "/ready" and capabilities["listing_snapshot"]["immutable"]
        monkeypatch.setattr(graph_api, "read_query", lambda *a, **kw: [])
        assert client.get("/ready").json()["detail"]["code"] == "GRAPH_NOT_SYNCED"


def test_graph_health_does_not_hide_sql_failure(canonical, monkeypatch):
    monkeypatch.setattr(graph_api, "read_query", lambda *a, **kw: [{"ok": 1}])
    monkeypatch.setattr(graph_api, "sql_readiness", Mock(side_effect=OperationalError("private query", {}, Exception("secret"))))
    with TestClient(graph_api.create_app(driver=Mock(), sql_engine=canonical)) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/ready")
        assert response.status_code == 503 and response.json()["detail"]["code"] == "SQL_UNAVAILABLE"
        assert "secret" not in response.text and "private" not in response.text
