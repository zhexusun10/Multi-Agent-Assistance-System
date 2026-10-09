"""POI acquisition, evidence and schema tests without network or live databases."""
import json
from unittest.mock import Mock

import pytest

from data.graph import places


def element(oid=1, **tags):
    return {"id": oid, "type": "node", "lat": 1.30, "lon": 103.80,
            "tags": {"name": "Example public place", **tags}}


@pytest.mark.parametrize("tags,category", [
    ({"amenity": "school"}, "School"), ({"amenity": "university"}, "School"),
    ({"amenity": "food_court"}, "FoodCourt"), ({"shop": "mall"}, "Mall"),
    ({"amenity": "marketplace", "name": "Maxwell Food Centre"}, "FoodCourt"),
    ({"amenity": "marketplace"}, "Market"), ({"leisure": "park"}, "Park"),
    ({"amenity": "hospital"}, "Hospital"), ({"shop": "supermarket"}, "Supermarket"),
])
def test_categories_identity_and_provenance(tags, category):
    row = places.map_osm_element(element(**tags), "2026-01-01T00:00:00+00:00")
    assert row["place_id"] == "OSM:node:1" and row["category"] == category
    assert row["license"] == "ODbL-1.0" and row["source_url"].endswith("/node/1")
    assert row["coordinate_method"] == "osm_node" and row["source"] == "openstreetmap"
    assert "route_verified" not in row  # a POI is not a route


def test_reject_unnamed_invalid_and_false_transit_tags():
    for tags in ({"amenity": "school", "name": ""}, {"shop": "mall", "highway": "bus_stop"},
                 {"amenity": "school", "disused": "yes"}, {"shop": "clothes"}):
        assert places.map_osm_element(element(**tags), "now") is None
    e = element(amenity="school"); e["lat"] = 0
    assert places.map_osm_element(e, "now") is None
    e = element(amenity="school"); e["type"] = "way"; e["center"] = {"lat": 1.3, "lon": 103.8}
    assert places.map_osm_element(e, "now")["coordinate_method"] == "osm_geometry_bbox_center"


def test_campus_classification_requires_named_building_inside_grounds():
    # Grounds themselves stay ordinary School POIs; campus membership is separate.
    assert places.category_for({"amenity": "university"}, in_campus=True) is None
    assert places.category_for({"amenity": "university"}) == "School"
    assert places.category_for({"building": "university"}, in_campus=True) == "CampusBuilding"
    assert places.category_for({"building": "university", "amenity": "college"}, in_campus=True) == "CampusBuilding"
    # Plain building=yes school footprints and unnamed shapes are never campus rows.
    assert places.category_for({"building": "yes", "amenity": "school"}, in_campus=True) is None
    row = places.map_osm_element(element(building="university"), "now", in_campus=True)
    assert row["category"] == "CampusBuilding" and "campus_ids" not in row
    with pytest.raises(ValueError, match="mapped campus grounds"):
        places.validate_places([row])


def test_membership_evidence_is_point_in_grounds_polygon_only():
    from data.graph import campus as campus_places
    ring = [[103.774, 1.292], [103.777, 1.292], [103.777, 1.295], [103.774, 1.295], [103.774, 1.292]]
    grounds = [campus_places.campus_feature("way", 54519165, {"name": "National University of Singapore"},
                                             {"type": "Polygon", "coordinates": [ring]}, "2026-01-01")]
    row = places.map_osm_element(element(building="university"), "now", in_campus=True)
    row["latitude"], row["longitude"] = 1.2935, 103.775  # inside the grounds
    def fresh(**overrides):
        copy = {k: v for k, v in row.items() if not k.startswith("campus_")}
        copy.update(overrides)
        return copy
    inside = campus_places.attach_membership(fresh(), grounds)
    assert inside["campus_ids"] == ["OSM:way:54519165"]
    assert inside["campus_membership_method"] == "reference_point_in_osm_campus_polygon"
    assert "ownership" not in str(inside) and inside["campus_membership_source_urls"][0].endswith("/way/54519165")
    assert "campus_ids" not in campus_places.attach_membership(fresh(latitude=1.320, longitude=103.850), grounds)
    # The grounds feature itself is never its own member.
    assert "campus_ids" not in campus_places.attach_membership(fresh(place_id="OSM:way:54519165"), grounds)


def test_grounds_detection_and_incomplete_overpass_geometry():
    from data.graph import campus as campus_places
    assert campus_places.campus_ground({"amenity": "university", "name": "NUS"})
    for tags in ({"amenity": "university", "building": "yes", "name": "NUS"},
                 {"amenity": "school", "name": "X"}, {"amenity": "university"}):
        assert not campus_places.campus_ground(tags)
    open_ring = [[0, 0], [1, 0], [1, 1], [0, 1]]  # not closed
    assert campus_places.overpass_geometry({"type": "way", "geometry": [{"lat": 0, "lon": 0}]}) is None
    assert campus_places.overpass_geometry({"type": "way", "geometry":
        [{"lat": p[1], "lon": p[0]} for p in open_ring]}) is None
    # A relation with unassembled members must not invent a polygon.
    assert campus_places.overpass_geometry({"type": "relation", "members": [
        {"type": "way", "role": "outer", "geometry": [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 1}]},
        {"type": "way", "role": "outer", "geometry": [{"lat": 1, "lon": 1}, {"lat": 1, "lon": 2}]},
    ]}) is None


def test_overpass_campus_requires_complete_refresh():
    from data.graph import campus as campus_places
    element(building="university")
    grounds = [campus_places.campus_feature("way", 1, {"name": "Campus"},
                 {"type": "Polygon", "coordinates": [[[103.7, 1.2], [103.9, 1.2], [103.9, 1.4], [103.7, 1.4], [103.7, 1.2]]]}, "now")]
    building = {"type": "way", "id": 2, "tags": {"name": "BIZ2", "building": "university"},
                 "geometry": [{"lat": 1.293, "lon": 103.775}] * 5, "timestamp": "now"}
    rows = campus_places.map_overpass_campus([building], grounds, "now")
    assert rows[0]["place_id"] == "OSM:way:2" and rows[0]["campus_ids"] == ["OSM:way:1"]
    with pytest.raises(ValueError, match="no campus buildings"):
        campus_places.map_overpass_campus([], grounds, "now")


def test_corroboration_keeps_osm_identity_and_drops_stale_checks(tmp_path):
    from data.graph import campus as campus_places
    row = places.map_osm_element(element(building="university"), "now", in_campus=True)
    row["latitude"], row["longitude"] = 1.2935, 103.775
    check = {"place_id": "OSM:node:1", "osm_name": row["name"], "postal_code": row.get("postal_code"),
             "campus_id": "OSM:way:9"}
    for key in ("official_source_url", "official_api_url", "official_record_id", "official_name",
                "official_campus_name", "official_reference_latitude", "official_reference_longitude",
                "official_verified_at", "official_verification_method", "official_source_license"):
        check[key] = f"value-{key}"
    path = tmp_path / "corroborations.json"
    path.write_text(json.dumps({"checks": [check]}), encoding="utf-8")
    # Without campus membership evidence the check is never applied.
    assert "official_record_id" not in campus_places.apply_corroborations([row], path)[0]
    row["campus_ids"] = ["OSM:way:9"]
    applied = campus_places.apply_corroborations([row], path)[0]
    assert applied["official_record_id"] == "value-official_record_id"
    assert applied["latitude"] == row["latitude"] and applied["name"] == row["name"]  # OSM identity kept
    # A stale identity check (moved address) must be dropped, not silently reused.
    fresh = {k: v for k, v in row.items() if not k.startswith("official_")}
    dropped = campus_places.apply_corroborations([dict(fresh, postal_code="999999")], path)[0]
    assert "official_record_id" not in dropped


def test_country_polygon_holes_not_bbox():
    boundary = {"features": [{"geometry": {"type": "Polygon", "coordinates": [
        [[103.7, 1.2], [103.9, 1.2], [103.9, 1.4], [103.7, 1.4], [103.7, 1.2]],
        [[103.78, 1.28], [103.82, 1.28], [103.82, 1.32], [103.78, 1.32], [103.78, 1.28]],
    ]}}]}
    assert places.inside_boundary(103.85, 1.35, boundary)
    assert not places.inside_boundary(103.8, 1.3, boundary)  # hole
    assert not places.inside_boundary(103.8, 1.45, boundary)  # still in loose SG bbox


def test_default_snapshot_is_valid_and_has_real_key_places():
    rows, metadata = places.load_snapshot()
    assert len(rows) == metadata["place_count"] and len({r["place_id"] for r in rows}) == len(rows)
    assert {"School", "FoodCourt", "Mall", "CampusBuilding"} <= {r["category"] for r in rows}
    assert any(r["name"] == "Westgate" and r["place_id"] == "OSM:way:158234014" for r in rows)
    assert all(1.15 <= r["latitude"] <= 1.5 for r in rows)
    assert metadata["boundary_method"] in ("country_polygon", "osm_country_area")
    # Every campus building carries grounds evidence, never a bare assertion.
    for row in rows:
        if row["category"] == "CampusBuilding":
            assert row["campus_ids"] and row["campus_membership_method"] == "reference_point_in_osm_campus_polygon"


def test_snapshot_contains_reviewed_biz2_with_official_cross_check():
    rows, metadata = places.load_snapshot()
    biz2 = next(r for r in rows if r["place_id"] == "OSM:way:54619697")
    assert biz2["name"] == "BIZ2" and biz2["full_name"] == "Business 2"
    assert biz2["category"] == "CampusBuilding" and biz2["campus_ids"] == ["OSM:way:54519165"]
    assert biz2["postal_code"] == "117592"
    # Cross-source check is kept separate from OSM identity fields.
    assert biz2["official_record_id"] == "53" and biz2["official_campus_name"] == "Kent Ridge Campus"
    assert biz2["official_verification_method"] == "nus_campus_map_api_lookup"
    assert (biz2["official_reference_latitude"], biz2["official_reference_longitude"]) == (1.29337, 103.77557)
    assert metadata["campus_membership"] == "point-in-grounds-polygon evidence only; not ownership or an entrance"


def test_cache_requires_no_network_and_invalid_snapshot_is_rejected(tmp_path, monkeypatch):
    rows = [places.map_osm_element(element(shop="mall"), "now")]
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"metadata": {"place_count": 1}, "places": rows}), encoding="utf-8")
    network = Mock(side_effect=AssertionError("must not fetch"));monkeypatch.setattr(places, "urlopen", network)
    assert places.acquire_places(output=path)[0] == rows
    network.assert_not_called()
    with pytest.raises(ValueError, match="Empty"):
        places.validate_places([])
    with pytest.raises(ValueError, match="Invalid"):
        places.validate_places([dict(rows[0], latitude=90)])
    driver = Mock()
    with pytest.raises(ValueError):
        places.sync_places(driver, [], {})
    driver.session.assert_not_called()


def test_reclassification_is_whitelisted_and_evidence_is_honest():
    tx = Mock()
    places._upsert_places(tx, [], "School")
    query = tx.run.call_args.args[0]
    assert "MERGE (p:Place" in query and "SET p:School" in query and "REMOVE p:School:FoodCourt" in query
    with pytest.raises(ValueError):
        places._upsert_places(tx, [], "School) DELETE p")
    assert places.CAMPUS_CATEGORIES == ("CampusBuilding",)
    tx.run.reset_mock()
    places._link_campus(tx, [{"place_id": "OSM:way:54619697", "campus_ids": ["OSM:way:54519165"],
                              "source_updated_at": "now"}], "now")
    link = tx.run.call_args.args[0]
    assert "MERGE (b)-[r:PART_OF_CAMPUS]->(c)" in link and "is_administrative_membership = false" in link
    assert "reference_point_in_osm_campus_polygon" in link
    assert "geodesic" in places.RELATE_LISTINGS and "route_verified = false" in places.RELATE_LISTINGS
    assert "DELETE old" in places.RELATE_LISTINGS and "osm_sg_proximity" in places.RELATE_LISTINGS
    assert "is_administrative_membership = false" in places.DISTRICT_PLACES
    assert "DETACH DELETE" not in places.DISTRICT_PLACES
