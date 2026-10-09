"""Offline tests for distance semantics, exact station IDs and spatial API safety."""
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from neo4j.spatial import WGS84Point

from data.graph import sync as graph
from data.service import app as graph_api
from data.graph.semantics import location_for, normalize_station, nonnegative_number, source_timestamp


@pytest.mark.parametrize("left,right,expected", [
    (" CC22 Buona Vista MRT ", "EW21 Buona Vista MRT Station", "SG:MRT:buona-vista"),
    ("EW21/CC22 Buona Vista MRT", "Buona Vista", "SG:MRT:buona-vista"),
    ("NE1 HarbourFront MRT", "CC29 Harbour Front MRT", "SG:MRT:harbourfront"),
    ("CC23 One-North MRT", "one north MRT Station", "SG:MRT:one-north"),
    ("DT37 Sungei Bedok MRT (U/C)", "TE31 Sungei Bedok MRT", "SG:MRT:sungei-bedok"),
])
def test_exact_station_aliases_share_local_ids(left, right, expected):
    assert normalize_station(left)["station_id"] == normalize_station(right)["station_id"] == expected
    assert normalize_station(left)["identity_method"] == "normalized_name_and_mode"


def test_normalization_keeps_original_unicode_and_inner_whitespace_as_evidence():
    raw = "ＣＣ２２   Buona Vista MRT"
    normalized = normalize_station(raw)
    assert normalized["station_id"] == "SG:MRT:buona-vista"
    assert normalized["codes"] == ["CC22"]
    assert normalized["alias"] == raw


def test_normalization_preserves_codes_raw_alias_and_source_status():
    station = normalize_station("DT37 / TE31 Sungei Bedok MRT (U/C)")
    assert station["alias"] == "DT37 / TE31 Sungei Bedok MRT (U/C)"
    assert station["codes"] == ["DT37", "TE31"]
    assert station["station_status"] == "under_construction"
    assert normalize_station("TE31 Sungei Bedok MRT")["station_status"] == "unspecified"
    assert normalize_station("BP6 Bukit Panjang LRT")["station_id"] != normalize_station("DT1 Bukit Panjang MRT")["station_id"]
    assert normalize_station("STC Sengkang")["transport_mode"] == "LRT"
    assert normalize_station("Pasir Ris East MRT")["station_id"] != normalize_station("Pasir Ris MRT")["station_id"]


@pytest.mark.parametrize("name", [None, "", "  ", 0, "DT17", "DT17 MRT"])
def test_missing_station_name_cannot_create_placeholder_node(name):
    assert normalize_station(name) is None


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), Decimal("NaN"), True, "oops", None])
def test_invalid_distance_is_unknown_not_zero(bad):
    assert nonnegative_number(bad) is None


def test_valid_zero_and_decimal_metrics_are_preserved():
    assert nonnegative_number(0) == 0
    assert nonnegative_number(Decimal("250.5")) == 250.5


@pytest.mark.parametrize("lat,lon,status", [
    (None, 103.8, "missing"), (1.3, None, "missing"),
    (0, 0, "invalid"), (103.8, 1.3, "invalid"), (90, 180, "invalid"),
    (float("nan"), 103.8, "invalid"), (1.3, float("inf"), "invalid"), (True, 103.8, "invalid"),
])
def test_invalid_coordinates_are_never_imputed(lat, lon, status):
    assert location_for(lat, lon) == (None, status)


def test_valid_location_and_source_timezone_preserved():
    assert location_for(Decimal("1.3"), Decimal("103.8")) == ({"latitude": 1.3, "longitude": 103.8, "srid": 4326}, "valid")
    assert source_timestamp(datetime(2026, 1, 1)) == "2026-01-01T00:00:00"
    assert source_timestamp(datetime(2026, 1, 1, tzinfo=timezone.utc)).endswith("+00:00")
    assert source_timestamp("not-a-time") is None


def test_mapping_carries_provenance_validation_and_no_private_data():
    row = {field: None for field in graph.LISTING_FIELDS}
    row.update(listing_id=10, project_id=756, agent_id=9, agent_name="Agent", agency_name="Agency",
               district_code="D05", nearest_mrt="CC22 Buona Vista MRT", mrt_distance_m=0,
               mrt_walking_mins=-3, latitude="1.3", longitude="103.8", updated_at=datetime(2026, 1, 1),
               agent_phone="private", raw_json="private")
    mapped = graph.map_listing(row)
    assert mapped["coordinate_status"] == "valid" and mapped["location"]["srid"] == 4326
    assert mapped["props"]["latitude"] == 1.3
    assert mapped["near_props"]["distance_m"] == 0
    assert mapped["near_props"]["walking_mins"] is None
    assert mapped["near_props"]["walking_time_status"] == "invalid"
    assert "mrt_walking_mins" not in mapped["props"]
    assert mapped["near_props"]["distance_type"] == "platform_reported"
    assert mapped["near_props"]["observation_basis"] == "source_row_updated_at"
    assert mapped["near_props"]["observed_at"] == "2026-01-01T00:00:00"
    assert not mapped["near_props"]["route_verified"]
    assert "private" not in str(mapped)
    row.update(latitude=0, longitude=0, mrt_distance_m=None)
    invalid = graph.map_listing(row)
    assert invalid["location"] is None and invalid["coordinate_status"] == "invalid"
    assert "latitude" not in invalid["props"] and "longitude" not in invalid["props"]
    assert invalid["near_props"]["distance_status"] == "missing"


class Node(dict):
    def __init__(self, element_id, **props):
        super().__init__(props)
        self.element_id = element_id
        self.labels = {"Listing"}


def test_nearby_is_bounded_parameterized_and_returns_ephemeral_geodesic_edges(monkeypatch):
    center = Node("center", listing_id=10, latitude=1.3, longitude=103.8, coordinate_status="valid",
                  position=WGS84Point((103.8, 1.3)), source_updated_at="2026-01-01T00:00:00")
    others = [Node(f"n{i}", listing_id=i, latitude=1.3, longitude=103.8, coordinate_status="valid",
                   raw_json="private") for i in (11, 12, 13)]
    calls = []
    def read(driver, db, query, **params):
        calls.append((query, params))
        if query == graph_api.CENTER_QUERY:
            return [{"l": center, "r": None, "n": None}]
        return [{"n": n, "distance_m": float(i)} for i, n in enumerate(others)]
    monkeypatch.setattr(graph_api, "read_query", read)
    with TestClient(graph_api.create_app(driver=Mock())) as client:
        response = client.get("/api/nearby", params={"listing_id": 10, "radius_m": 500, "limit": 2})
        assert response.status_code == 200
        result = response.json()
        assert len(result["nodes"]) == 3 and len(result["edges"]) == 2
        assert result["truncated"] and not result["persisted"]
        edge = result["edges"][0]
        assert edge["type"] == "NEAR_LISTING" and edge["properties"]["distance_m"] == 0
        assert edge["properties"]["distance_type"] == "geodesic"
        assert edge["properties"]["is_virtual"] and not edge["properties"]["route_verified"]
        assert edge["properties"]["distance_unit"] == "m"
        assert "private" not in str(result)
        assert calls[-1][1] == {"listing_id": 10, "latitude": 1.3, "longitude": 103.8, "radius_m": 500.0, "limit": 3,
                                "after_distance": -1, "after_listing_id": 0}
        assert result["next_cursor"] is not None
        assert "$radius_m" in calls[-1][0] and "CREATE" not in calls[-1][0] and "MERGE" not in calls[-1][0]
        for params in ({"listing_id": 0}, {"listing_id": 10, "radius_m": 0},
                       {"listing_id": 10, "radius_m": 10001}, {"listing_id": 10, "limit": 1001},
                       {"listing_id": "1 DELETE n"}, {"listing_id": 10, "radius_m": "nan"}):
            assert client.get("/api/nearby", params=params).status_code == 422


@pytest.mark.parametrize("status", ["missing", "invalid"])
def test_nearby_rejects_missing_or_invalid_coordinates(monkeypatch, status):
    center = Node("center", listing_id=10, coordinate_status=status)
    read = Mock(return_value=[{"l": center, "r": None, "n": None}])
    monkeypatch.setattr(graph_api, "read_query", read)
    with TestClient(graph_api.create_app(driver=Mock())) as client:
        assert client.get("/api/nearby", params={"listing_id": 10}).status_code == 422
        assert read.call_count == 1
        read.return_value = []
        assert client.get("/api/nearby", params={"listing_id": 10}).status_code == 404


def test_distance_filters_are_bound_not_interpolated(monkeypatch):
    read = Mock(return_value=[])
    monkeypatch.setattr(graph_api, "read_query", read)
    with TestClient(graph_api.create_app(driver=Mock())) as client:
        injection = "SG:MRT:x') DETACH DELETE n //"
        filters = {"mrt_station_id": injection, "max_mrt_distance_m": 500,
                   "include_planned": False, "coordinates_only": True}
        assert client.post("/api/v1/graph/search", json={"filters": filters}).status_code == 200
        query = read.call_args.args[2]
        bound = read.call_args.kwargs
        assert injection not in query and bound["mrt_station_id"] == injection
        assert bound["max_mrt_distance_m"] == 500 and not bound["include_planned"] and bound["coordinates_only"]
        assert "platform_reported" in query and "under_construction" in query
        assert client.post("/api/v1/graph/search", json={"filters": {"max_mrt_distance_m": -1}}).status_code == 422


def test_schema_migration_only_prunes_observed_legacy_orphans():
    assert "mrt_station_id_unique" in " ".join(graph.CONSTRAINTS)
    assert any("CREATE POINT INDEX" in statement for statement in graph.INDEXES)
    assert "legacy_projection" in graph.UPSERT_LISTINGS and "legacy_projection" in graph.LEGACY_MRT_CLEANUP
    assert "NOT (m)--()" in graph.LEGACY_MRT_CLEANUP
    assert "DETACH DELETE" not in graph.LEGACY_MRT_CLEANUP
