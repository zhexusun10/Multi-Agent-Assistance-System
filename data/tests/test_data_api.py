"""Agent contracts, stable cursors and canonical SQL details; all offline."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import ServiceUnavailable
from neo4j.spatial import WGS84Point
from sqlalchemy import create_engine, insert, text

from data.service import app as graph_api
from data.core.contracts import decode_cursor, encode_cursor
from data.graph.source import open_graph_source
from data.storage.models import Base, Property, PropertyImage


class Node(dict):
    def __init__(self, key, label="Listing", **props):
        super().__init__(props);self.element_id=key;self.labels={label}


def row(node):
    return {"l": node, "r": None, "n": None}


@pytest.fixture
def sql_source(tmp_path):
    path=tmp_path / "canonical.db";engine=create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(Property), {"listing_id": 10,"listing_type": "RENT", "price": 3000,
                    "title": "SQL canonical", "district_code": "D05", "agent_phone": "secret", "raw_json": {"secret": True}})
        conn.execute(insert(PropertyImage), [{"listing_id": 10, "image_url": f"https://example.org/{i}",
                    "image_type": "PHOTO", "source_page": "DETAIL_PAGE"} for i in range(3)])
    engine.dispose();source=open_graph_source(sqlite_path=path)
    yield source
    source.dispose()


def test_search_keyset_pages_filter_binding_and_stable_ids(monkeypatch):
    calls=[]
    def read(driver, db, query, **params):
        calls.append((query,params))
        return [row(Node(f"internal-{i}",listing_id=i,title="projection"))
                for i in (10,20,30) if i>params["after"]][:params["limit"]]
    monkeypatch.setattr(graph_api,"read_query",read)
    with TestClient(graph_api.create_app(driver=Mock())) as client:
        body={"filters":{"district":"D05","place_category":"School","max_place_distance_m":1000},"page_size":2}
        first=client.post("/api/v1/listings/search",json=body).json()
        assert [n["entity_id"] for n in first["items"]]==["Listing:10","Listing:20"]
        assert first["has_more"] and first["source"]["store"]=="neo4j"
        second=client.post("/api/v1/listings/search",json={**body,"cursor":first["next_cursor"]}).json()
        assert [n["entity_id"] for n in second["items"]]==["Listing:30"] and second["next_cursor"] is None
        changed={**body,"filters":{"district":"D06"},"cursor":first["next_cursor"]}
        assert client.post("/api/v1/listings/search",json=changed).json()["error"]["code"]=="INVALID_CURSOR"
        injection="x') DELETE n //"
        assert client.post("/api/v1/listings/search",json={"filters":{"q":injection}}).status_code==200
        assert calls[-1][1]["q"]==injection and injection not in calls[-1][0]
        # The text query also matches nearby place names/full/alt names (e.g. BIZ2).
        assert "pl.full_name" in calls[-1][0] and "pl.alt_names" in calls[-1][0]
        assert "NEAR_PLACE" in calls[-1][0] and "geodesic" in calls[-1][0]


@pytest.mark.parametrize("body", [
    {"filters":{"district":"D29"}}, {"filters":{"min_price":20,"max_price":10}},
    {"page_size":0}, {"page_size":1001}, {"filters":{"place_category":"NotAPlace"}},
    {"cypher":"DELETE n"}, {"filters":{"min_bedrooms":-1}}, {"cursor":"not-base64"},
])
def test_invalid_contracts_have_structured_errors(body):
    with TestClient(graph_api.create_app(driver=Mock())) as client:
        response=client.post("/api/v1/listings/search",json=body)
        assert response.status_code==422 and "error" in response.json()


def test_graph_does_not_cap_total_nodes_and_reports_next_cursor(monkeypatch):
    nodes=[row(Node(str(i),listing_id=i)) for i in range(1,705)]
    monkeypatch.setattr(graph_api,"read_query",lambda *a,**kw: [r for r in nodes if r["l"]["listing_id"]>kw["after"]][:kw["limit"]])
    with TestClient(graph_api.create_app(driver=Mock())) as client:
        result=client.post("/api/v1/graph/search",json={"page_size":1000}).json()
        assert len(result["nodes"])==704 and not result["has_more"]  # old renderer stopped at 650
        first=client.post("/api/v1/graph/search",json={"page_size":500}).json()
        second=client.post("/api/v1/graph/search",json={"page_size":500,"cursor":first["next_cursor"]}).json()
        assert len(first["nodes"])+len(second["nodes"])==704
        assert not set(n["id"] for n in first["nodes"]) & set(n["id"] for n in second["nodes"])


def test_sql_canonical_detail_images_no_sensitive_fields_or_writes(sql_source):
    with TestClient(graph_api.create_app(driver=Mock(),sql_engine=sql_source)) as client:
        detail=client.get("/api/v1/listings/10").json()
        assert detail["source"]["store"]=="sqlite" and detail["item"]["properties"]["title"]=="SQL canonical"
        assert detail["item"]["properties"]["coordinate_status"]=="missing"
        assert "secret" not in str(detail)
        first=client.get("/api/v1/listings/10/images?page_size=2").json()
        second=client.get("/api/v1/listings/10/images",params={"page_size":2,"cursor":first["next_cursor"]}).json()
        assert len(first["items"])+len(second["items"])==3 and not second["has_more"]
        assert client.get("/api/v1/listings/0").status_code==422
        assert client.get("/api/v1/listings/99").status_code==404
        assert client.post("/api/v1/listings/10",json={"price":1}).status_code==405
    with sql_source.connect() as conn:
        with pytest.raises(Exception,match="readonly"):
            conn.execute(text("DELETE FROM properties"))


def test_sql_outage_does_not_fall_back_to_local_snapshot(monkeypatch):
    engine=Mock();engine.dialect.name="postgresql"
    from sqlalchemy.exc import OperationalError
    monkeypatch.setattr(graph_api,"listing_detail",Mock(side_effect=OperationalError("SELECT",{},Exception("secret"))))
    with TestClient(graph_api.create_app(driver=Mock(),sql_engine=engine)) as client:
        response=client.get("/api/v1/listings/10")
        assert response.status_code==503 and response.json()["error"]["code"]=="SQL_UNAVAILABLE"
        assert "secret" not in response.text


def test_nearby_place_sort_cursor_and_distance_provenance(monkeypatch):
    all_rows=[{**row(Node(f"p{i}","School",place_id=f"OSM:way:{i}",name=f"School {i}",category="School")),
                "distance_m":d} for i,d in [(1,0.0),(2,0.0),(3,120.1)]]
    def read(driver,db,query,**params):
        if query==graph_api.CENTER_QUERY:
            return [row(Node("l",listing_id=10,coordinate_status="valid",position=WGS84Point((103.8,1.3))))]
        return [r for r in all_rows if (r["distance_m"],r["l"]["place_id"])>(params["after_distance"],params["after_place_id"])][:params["limit"]]
    monkeypatch.setattr(graph_api,"read_query",read)
    with TestClient(graph_api.create_app(driver=Mock())) as client:
        body={"listing_id":10,"categories":["School"],"page_size":1}
        items=[];cursor=None
        for _ in range(3):
            result=client.post("/api/v1/places/nearby",json={**body,"cursor":cursor}).json()
            items.extend(result["items"]);cursor=result["next_cursor"]
        assert [r["place"]["entity_id"] for r in items]==["Place:OSM:way:1","Place:OSM:way:2","Place:OSM:way:3"]
        assert cursor is None and all(r["distance_type"]=="geodesic" and not r["route_verified"] for r in items)
        for invalid in ({"listing_id":10,"latitude":1.3,"longitude":103.8},{"latitude":1.3},{"radius_m":-1},
                        {"listing_id":10,"categories":["wrong"]}):
            assert client.post("/api/v1/places/nearby",json=invalid).status_code==422


def test_unknown_coordinates_and_unavailable_graph_are_explicit(monkeypatch):
    monkeypatch.setattr(graph_api,"read_query",lambda *a,**kw:[row(Node("l",listing_id=10,coordinate_status="missing"))])
    with TestClient(graph_api.create_app(driver=Mock())) as client:
        response=client.post("/api/v1/places/nearby",json={"listing_id":10})
        assert response.status_code==422 and response.json()["error"]["code"]=="COORDINATES_UNKNOWN"
        def unavailable(*a,**kw):raise ServiceUnavailable("secret")
        monkeypatch.setattr(graph_api,"read_query",unavailable)
        response=client.post("/api/v1/listings/search",json={})
        assert response.status_code==503 and "secret" not in response.text


def test_sql_nonfinite_numbers_are_json_safe():
    from decimal import Decimal
    from data.storage.repository import json_value
    assert json_value(Decimal("NaN")) is None
    assert json_value(float("inf")) is None
    assert json_value(Decimal("3000.25")) == 3000.25


def test_cursors_are_resource_specific_and_bounded():
    value=encode_cursor("places",{"category":"School"},"OSM:way:7")
    assert decode_cursor(value,"places",{"category":"School"})=="OSM:way:7"
    with pytest.raises(ValueError):decode_cursor(value,"listings",{"category":"School"})
