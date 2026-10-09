"""Acceptance checker detects drift; no service or network required."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from neo4j.spatial import WGS84Point
from sqlalchemy import create_engine, insert

from data.service import validation as data_validation
from data.graph import sync as graph
from data import knowledge_graph
from data.storage.models import Base, Property


@pytest.fixture
def projection(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'source.db').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(Property), [
            {"listing_id": lid, "listing_type": "RENT", "price": 3000, "district_code": "D05",
             "latitude": 1.3 if lid == 10 else None, "longitude": 103.8 if lid == 10 else None,
             "nearest_mrt": "Jurong East", "mrt_distance_m": 250}
            for lid in (10, 20)
        ])
    rows = [r for batch in graph.iter_listing_batches(engine) for r in batch]
    records = []
    for row in rows:
        node = {**row["props"], "coordinate_status": row["coordinate_status"], "source_updated_at": row["source_updated_at"]}
        if row["location"]:
            node["position"] = WGS84Point((row["location"]["longitude"], row["location"]["latitude"]))
        records.append({"l": node, "relationships": [
            {"type": "IN_DISTRICT", "target": {"district_code": "D05"}, "evidence": {}},
            {"type": "NEAR_MRT", "target": row["station_info"], "evidence": deepcopy(row["near_props"])},
        ]})
    def read(driver, database, query, **params):
        if query == data_validation.LISTINGS:
            return records
        if query == data_validation.PLACE_DATASET:
            return [{"radius_m": 1500}]
        if query == data_validation.STALE_PROXIMITY:
            return [{"invalid": 0}]
        if query == data_validation.MISSING_PROXIMITY:
            return [{"missing": 0}]
        if query == data_validation.DUPLICATE_PROXIMITY:
            return [{"duplicates": 0}]
        return [{"count": 1}]
    monkeypatch.setattr(data_validation, "read_query", read)
    yield engine, records
    engine.dispose()


def test_readonly_verification_checks_all_listing_fields_and_coordinates(projection):
    engine, _ = projection
    expected, result = data_validation.verify_graph(engine, Mock(), "test")
    assert set(expected) == {10, 20}
    assert result["valid_coordinates"] == 1 and result["unknown_coordinates"] == 1
    assert result["stale_proximity_edges"] == 0
    assert result["missing_proximity_edges"] == result["duplicate_proximity_edges"] == 0
    assert result["place_radius_m"] == 1500


@pytest.mark.parametrize("fault", ["price", "source", "coordinate", "dimension", "ids", "duplicate"])
def test_same_count_is_not_enough_to_pass_verification(projection, fault):
    engine, records = projection
    if fault == "price":
        records[0]["l"]["price"] = 1
    elif fault == "source":
        records[0]["l"]["source_updated_at"] = "another snapshot"
    elif fault == "coordinate":
        records[1]["l"]["position"] = WGS84Point((103.8, 1.3))
    elif fault == "dimension":
        records[0]["relationships"][0]["target"]["district_code"] = "D06"
    elif fault == "ids":
        records[0]["l"]["listing_id"] = 99
    else:
        records.append(deepcopy(records[0]))
    with pytest.raises(ValueError):
        data_validation.verify_graph(engine, Mock(), "test")


def test_stale_distances_require_full_rebuild(projection, monkeypatch):
    engine, records = projection
    original = data_validation.read_query
    monkeypatch.setattr(data_validation, "read_query", lambda d, db, query, **kw:
                        [{"invalid": 1}] if query == data_validation.STALE_PROXIMITY else original(d, db, query, **kw))
    with pytest.raises(ValueError, match="stale/invalid"):
        data_validation.verify_graph(engine, Mock(), "test")


@pytest.mark.parametrize("query,result,message", [
    (data_validation.MISSING_PROXIMITY, [{"missing": 3}], "distance edges are missing"),
    (data_validation.DUPLICATE_PROXIMITY, [{"duplicates": 2}], "duplicate"),
    (data_validation.PLACE_DATASET, [], "metadata is missing"),
    (data_validation.PLACE_DATASET, [{"radius_m": float("nan")}], "enrichment radius"),
    (data_validation.PLACE_DATASET, [{"radius_m": 10001}], "enrichment radius"),
])
def test_place_projection_completeness_and_configuration(projection, monkeypatch, query, result, message):
    engine, _ = projection
    original = data_validation.read_query
    monkeypatch.setattr(data_validation, "read_query", lambda d, db, q, **kw:
                        result if q == query else original(d, db, q, **kw))
    with pytest.raises(ValueError, match=message):
        data_validation.verify_graph(engine, Mock(), "test")


def test_shared_projection_rebuilds_places_with_same_driver(monkeypatch):
    from data.graph import places
    driver, engine = Mock(), Mock()
    basic = Mock(return_value=2)
    enrichment = Mock(return_value={"places": 1})
    monkeypatch.setattr(graph, "sync_graph", basic)
    monkeypatch.setattr(places, "acquire_places", lambda: ([{"place_id": "test"}], {"dataset_id": "test"}))
    monkeypatch.setattr(places, "sync_places", enrichment)
    result = graph.sync_projection(engine, driver=driver, database="test")
    assert result == {"listings": 2, "place_enrichment": {"places": 1}}
    assert basic.call_args.kwargs["driver"] is driver and enrichment.call_args.args[0] is driver
    driver.close.assert_not_called()
    enrichment.reset_mock()
    assert graph.sync_projection(engine, driver=driver, include_places=False)["place_enrichment"] is None
    enrichment.assert_not_called()


def test_verifier_cli_validation_and_failure_are_nonzero(monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge_graph, "open_api_source", Mock(side_effect=ValueError("No canonical source")))
    report = str(tmp_path / "result.json")
    assert knowledge_graph.main(["verify", "--report", report]) == 1
    import json
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "failed"
    assert knowledge_graph.main(["verify", "--api-url", "http://user:secret@localhost", "--report", report]) == 1
    assert "secret" not in (tmp_path / "result.json").read_text()
    with pytest.raises(SystemExit):
        knowledge_graph.main(["verify", "--sqlite", "x", "--database-url", "postgresql://localhost/db"])
