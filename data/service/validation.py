"""Read-only, repeatable SQL -> Neo4j -> HTTP acceptance checks.

Never migrate, delete nodes, repair graphs, or fetch public data here. A mismatch
is an error, not permission to overwrite a populated database.
"""
import json
import math
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import select

from data.graph.sync import LISTING_FIELDS, iter_listing_batches
from data.service.app import read_query
from data.storage.models import PropertyImage
from data.storage.repository import listing_detail, readonly_connection
from data.storage.snapshot import assert_snapshot, snapshot_info, sql_counts

LISTINGS = """
MATCH (l:Listing)
OPTIONAL MATCH (l)-[r:IN_PROJECT|LISTED_BY|IN_DISTRICT|NEAR_MRT]->(n)
RETURN l, collect({type: type(r), target: properties(n), evidence: properties(r)}) AS relationships
"""
PLACE_DATASET = "MATCH (d:Dataset {dataset_id:'osm_sg_places'}) RETURN d.radius_m AS radius_m"
STALE_PROXIMITY = """
MATCH (l:Listing)-[r:NEAR_PLACE {source:'osm_sg_proximity'}]->(p:Place)
WHERE l.position IS NULL OR coalesce(l.coordinate_status,'') <> 'valid'
   OR p.position IS NULL OR coalesce(p.active,false) <> true
   OR l.position.srid <> 4326 OR p.position.srid <> 4326
   OR coalesce(r.distance_type,'') <> 'geodesic' OR coalesce(r.route_verified,true) <> false
   OR coalesce(r.distance_unit,'') <> 'm' OR coalesce(r.radius_m,-1) <> $radius_m
   OR r.distance_m IS NULL OR r.distance_m < 0 OR r.distance_m > $radius_m + 0.011
   OR point.distance(l.position,p.position) > $radius_m
   OR abs(r.distance_m - point.distance(l.position,p.position)) > 0.011
   OR r.listing_source_updated_at IS NULL OR r.listing_source_updated_at <> l.source_updated_at
RETURN count(r) AS invalid
"""
MISSING_PROXIMITY = """
MATCH (l:Listing {source:'propertyguru'})
WHERE l.coordinate_status = 'valid' AND l.position IS NOT NULL
CALL (l) {
    MATCH (p:Place {source:'openstreetmap', active:true})
    WHERE point.distance(p.position,l.position) <= $radius_m
      AND NOT EXISTS { MATCH (l)-[:NEAR_PLACE {source:'osm_sg_proximity'}]->(p) }
    RETURN count(p) AS missing
}
RETURN coalesce(sum(missing),0) AS missing
"""
DUPLICATE_PROXIMITY = """
MATCH (l:Listing)-[r:NEAR_PLACE {source:'osm_sg_proximity'}]->(p:Place)
WITH l, p, count(r) AS relationships WHERE relationships > 1
RETURN coalesce(sum(relationships-1),0) AS duplicates
"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_graph(engine, driver, database):
    expected = {r["listing_id"]: r for batch in iter_listing_batches(engine) for r in batch}
    require(bool(expected), "SQL source is empty; no snapshot to verify")
    records = read_query(driver, database, LISTINGS)
    actual = {r["l"]["listing_id"]: r for r in records}
    require(len(records) == len(actual), "Neo4j has duplicate listing IDs")
    require(set(expected) == set(actual),
            f"SQL/Neo4j listing IDs differ: missing={len(set(expected)-set(actual))}, extra={len(set(actual)-set(expected))}")
    valid = 0
    for lid, row in expected.items():
        node = actual[lid]["l"]
        for field in ("listing_id",) + LISTING_FIELDS:
            require(node.get(field) == row["props"].get(field), f"Listing {lid}: SQL/Neo4j field differs: {field}")
        require(node.get("coordinate_status") == row["coordinate_status"], f"Listing {lid}: coordinate status differs")
        require(node.get("source_updated_at") == row["source_updated_at"], f"Listing {lid}: graph is from another SQL source")
        position = node.get("position")
        if row["location"] is None:
            require(position is None, f"Listing {lid}: fabricated coordinates")
        else:
            valid += 1
            require(position is not None and position.srid == 4326
                    and math.isclose(position.latitude, row["location"]["latitude"], abs_tol=1e-9)
                    and math.isclose(position.longitude, row["location"]["longitude"], abs_tol=1e-9),
                    f"Listing {lid}: WGS-84 position differs")
        dimensions = {"IN_PROJECT": ("project_id", row["project_id"]),
                      "LISTED_BY": ("agent_id", row["agent_id"]),
                      "IN_DISTRICT": ("district_code", row["district_code"]),
                      "NEAR_MRT": ("station_id", row["station_info"]["station_id"] if row["station_info"] else None)}
        for kind, (key, value) in dimensions.items():
            edges = [r for r in actual[lid]["relationships"] if r["type"] == kind]
            require([r["target"].get(key) for r in edges] == ([] if value is None else [value]),
                    f"Listing {lid}: {kind} differs")
            if kind == "NEAR_MRT" and edges:
                require(all(edges[0]["evidence"].get(k) == v for k, v in row["near_props"].items()),
                        f"Listing {lid}: platform distance evidence differs")
    dataset = read_query(driver, database, PLACE_DATASET)
    require(len(dataset) == 1, "Public-place dataset metadata is missing/ambiguous; run a full import")
    radius = dataset[0].get("radius_m")
    require(isinstance(radius, (int, float)) and not isinstance(radius, bool)
            and math.isfinite(radius) and 1 <= radius <= 10000, "Invalid public-place enrichment radius")
    stale = read_query(driver, database, STALE_PROXIMITY, radius_m=radius)[0]["invalid"]
    require(stale == 0, f"{stale} stale/invalid public-place distance edges; rerun a full import")
    places = read_query(driver, database, "MATCH (p:Place {source:'openstreetmap', active:true}) RETURN count(p) AS count")[0]["count"]
    require(places > 0, "Public places were not imported; rerun import without --skip-places")
    missing = read_query(driver, database, MISSING_PROXIMITY, radius_m=radius)[0]["missing"]
    require(missing == 0, f"{missing} public-place distance edges are missing within the configured radius; rerun a full import")
    duplicates = read_query(driver, database, DUPLICATE_PROXIMITY)[0]["duplicates"]
    require(duplicates == 0, f"{duplicates} duplicate public-place distance edges; rerun a full import")
    coverage = read_query(driver, database, "MATCH (l:Listing) WHERE l.position IS NOT NULL "
        "AND NOT EXISTS { MATCH (l)-[:NEAR_PLACE]->(:Place {active:true}) } RETURN count(l) AS count")[0]["count"]
    return expected, {"listings": len(expected), "valid_coordinates": valid,
                      "unknown_coordinates": len(expected)-valid, "active_places": places,
                      "coordinate_listings_without_places": coverage, "place_radius_m": radius,
                      "stale_proximity_edges": stale, "missing_proximity_edges": missing,
                      "duplicate_proximity_edges": duplicates}


def http_reader(api_url=None, client=None):
    """Support real network HTTP and TestClient with the exact same checks."""
    def request(path, body=None):
        if client is not None:
            response = client.get(path) if body is None else client.post(path, json=body)
            return response.status_code, response.json()
        data = None if body is None else json.dumps(body).encode()
        req = Request(api_url.rstrip('/') + path, data=data, headers={"Content-Type": "application/json"})
        try:
            response = urlopen(req, timeout=30)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)
    return request


def verify_http(engine, expected, *, api_url=None, client=None):
    request = http_reader(api_url, client)
    def ok(path, body=None):
        status, data = request(path, body)
        require(status == 200, f"HTTP acceptance failed: {path} -> {status}")
        return data
    require(ok('/ready')["sql_source"] == engine.dialect.name, "HTTP API uses another SQL source")
    capabilities = ok('/api/v1/capabilities')
    require(capabilities["listing_snapshot"] == snapshot_info(), "HTTP API snapshot contract differs")
    require(capabilities["sql_source"] == engine.dialect.name, "HTTP API uses another SQL source")
    body, ids, cursors = {"filters": {}, "page_size": 1000}, [], set()
    while True:
        page = ok('/api/v1/listings/search', body)
        require(page["source"]["store"] == 'neo4j', "Search must use Neo4j")
        for item in page["items"]:
            lid = item["properties"]["listing_id"]
            require(item["entity_id"] == f'Listing:{lid}', "Unstable HTTP entity ID")
            ids.append(lid)
        cursor = page["next_cursor"]
        if cursor is None:
            break
        require(cursor not in cursors and bool(page["items"]), "HTTP pagination does not advance")
        cursors.add(cursor)
        body["cursor"] = cursor
    require(ids == sorted(expected), "HTTP pagination omits/duplicates listings or uses another graph")
    samples = sorted(expected)[:3]
    for status in ('valid', 'missing', 'invalid'):
        lid = next((lid for lid, row in expected.items() if row["coordinate_status"] == status), None)
        if lid is not None and lid not in samples:
            samples.append(lid)
    for lid in samples:
        detail = ok(f'/api/v1/listings/{lid}')
        require(detail["source"]["store"] == engine.dialect.name, "Detail must use the selected SQL source")
        require(detail["item"]["properties"] == listing_detail(engine, lid), f"HTTP SQL detail differs for {lid}")
        context = ok(f'/api/v1/listings/{lid}/context')
        require(context["listing"]["entity_id"] == f'Listing:{lid}', "Context uses another graph")
        for field in ("listing_id",) + LISTING_FIELDS:
            require(context["listing"]["properties"].get(field) == expected[lid]["props"].get(field),
                    f"HTTP graph context differs for {lid}: {field}")
        images, seen = [], set()
        path = f'/api/v1/listings/{lid}/images?page_size=1000'
        while True:
            page = ok(path)
            require(page["source"]["store"] == engine.dialect.name, "Images use another SQL source")
            images.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
            require(cursor not in seen and bool(page["items"]), "Image pagination does not advance")
            seen.add(cursor)
            path = f'/api/v1/listings/{lid}/images?' + urlencode({"page_size": 1000, "cursor": cursor})
        with readonly_connection(engine) as connection:
            expected_images = [dict(r) for r in connection.execute(select(
                PropertyImage.id, PropertyImage.source_page, PropertyImage.image_type,
                PropertyImage.image_url, PropertyImage.caption, PropertyImage.display_order)
                .where(PropertyImage.listing_id == lid).order_by(PropertyImage.id)).mappings()]
        require(images == expected_images, f"HTTP image pagination/content differs for {lid}")
        if expected[lid]["coordinate_status"] != 'valid':
            status, data = request('/api/v1/places/nearby', {"listing_id": lid})
            require(status == 422 and data["error"]["code"] == 'COORDINATES_UNKNOWN', "Unknown coordinates were hidden")
        else:
            nearby = ok('/api/v1/places/nearby', {"listing_id": lid, "radius_m": 1500, "page_size": 1})
            require(all(p["distance_type"] == 'geodesic' and p["route_verified"] is False for p in nearby["items"]),
                    "Nearby places claim route-verified distances")
    status, data = request('/api/v1/listings/search', {"cursor": 'not-a-valid-cursor'})
    require(status == 422 and data["error"]["code"] == 'INVALID_CURSOR', "Invalid cursor was accepted")
    return {"paginated_listings": len(ids), "detail_image_context_samples": len(samples), "sql_source": engine.dialect.name}


def verify_data(engine, driver, database, *, api_url=None, client=None, expected_counts=None):
    counts = sql_counts(engine)
    assert_snapshot(counts, expected_counts)
    expected, graph = verify_graph(engine, driver, database)
    if expected_counts is None:
        require(graph["valid_coordinates"] == snapshot_info()["valid_coordinates"]
                and graph["unknown_coordinates"] == snapshot_info()["unknown_coordinates"],
                "Coordinate coverage differs from the fixed snapshot")
    report = {"status": "ok", "read_only": True, "snapshot": snapshot_info(), "sql": counts, "graph": graph}
    if api_url is not None or client is not None:
        report["http"] = verify_http(engine, expected, api_url=api_url, client=client)
    report["verified_at"] = datetime.now(timezone.utc).isoformat()
    return report
