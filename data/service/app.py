"""Read-only PropertyGuru HTTP API with paged graph and spatial endpoints.

The visualization entry point is the official Neo4j Browser. This module
provides versioned JSON contracts and parameterized read-only queries only.
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from neo4j import Query as CypherQuery, READ_ACCESS
from neo4j.exceptions import Neo4jError, DriverError
from sqlalchemy.exc import SQLAlchemyError

from data.core.contracts import (Entity, GraphExpand, GraphPage, Item, ListingContext, ListingSearch, NearbyPlaces, Page,
                            PlaceSearch, decode_cursor, encode_cursor)
from data.graph.sync import LISTING_FIELDS, create_graph_driver
from data.graph.source import load_graph_env, open_api_source
from data.graph.places import ALL_CATEGORIES, CATEGORIES
from data.storage.repository import listing_detail, listing_images, sql_readiness
from data.storage.snapshot import SNAPSHOT_DATE, snapshot_info

logger = logging.getLogger(__name__)
BASE_LABELS = ("Listing", "Project", "Agent", "District", "MRT", "Place")
LABELS = BASE_LABELS + ALL_CATEGORIES
REL_TYPES = ("IN_PROJECT", "LISTED_BY", "IN_DISTRICT", "NEAR_MRT", "NEAR_PLACE", "HAS_NEARBY_PLACE", "PART_OF_CAMPUS")
REL_PATTERN = "|".join(REL_TYPES)
REL_FIELDS = ("distance_m", "walking_mins", "distance_type", "distance_unit", "walking_time_unit",
              "measurement_method", "source", "route_verified", "observed_at", "observation_basis", "distance_status",
              "walking_time_status", "distance_label", "raw_station_label", "station_status",
              "computed_at", "radius_m", "listing_source_updated_at", "place_observed_at",
              "membership_method", "is_administrative_membership", "evidence_count", "minimum_distance_m",
              "evidence", "verification", "building_source_updated_at")
PLACE_FIELDS = ("place_id", "name", "name_local", "full_name", "alt_names", "category", "subcategory", "latitude", "longitude",
                "postal_code", "street_name", "house_number", "opening_hours", "website", "wikidata", "education_level",
                "operator", "source", "dataset_id", "source_url", "source_object_type", "source_object_id", "observed_at",
                "source_updated_at", "coordinate_method", "coordinate_status", "license", "attribution",
                "identity_method", "active", "campus_ids", "campus_membership_method", "campus_membership_source_urls",
                "official_source_url", "official_api_url", "official_record_id", "official_name", "official_campus_name",
                "official_reference_latitude", "official_reference_longitude", "official_verified_at",
                "official_verification_method", "official_source_license")
NODE_FIELDS = {
    "Listing": ("listing_id",) + LISTING_FIELDS + ("coordinate_status", "coordinate_validation",
                                                "location_source", "source_updated_at", "source"),
    "Project": ("project_id", "display_name", "identity_method", "name_source"),
    "Agent": ("agent_id", "name", "agency_name", "identity_method"),
    "District": ("district_code",),
    "MRT": ("station_id", "name", "transport_mode", "codes", "aliases", "identity_method", "normalization_version"),
    "Place": PLACE_FIELDS,
    **{category: PLACE_FIELDS for category in ALL_CATEGORIES},
}

LISTING_WHERE = """
WHERE ($district IS NULL OR EXISTS {
    MATCH (l)-[:IN_DISTRICT]->(:District {district_code: $district})
})
AND ($listing_type IS NULL OR l.listing_type = $listing_type)
AND ($property_type IS NULL OR l.property_type = $property_type)
AND ($min_price IS NULL OR l.price >= $min_price)
AND ($max_price IS NULL OR l.price <= $max_price)
AND ($min_bedrooms IS NULL OR l.bedrooms >= $min_bedrooms)
AND ($max_bedrooms IS NULL OR l.bedrooms <= $max_bedrooms)
AND (NOT $coordinates_only OR l.position IS NOT NULL)
AND (($mrt_station_id IS NULL AND $max_mrt_distance_m IS NULL AND $include_planned)
     OR EXISTS {
        MATCH (l)-[near:NEAR_MRT]->(station:MRT)
        WHERE ($mrt_station_id IS NULL OR station.station_id = $mrt_station_id)
          AND ($max_mrt_distance_m IS NULL OR
               (near.distance_type = 'platform_reported' AND near.distance_status = 'reported'
                AND near.distance_m <= $max_mrt_distance_m))
          AND ($include_planned OR coalesce(near.station_status, 'unspecified') <> 'under_construction')
     })
AND (($place_id IS NULL AND $place_category IS NULL AND $max_place_distance_m IS NULL)
     OR EXISTS {
        MATCH (l)-[near:NEAR_PLACE]->(place:Place {active: true})
        WHERE ($place_id IS NULL OR place.place_id = $place_id)
          AND ($place_category IS NULL OR place.category = $place_category)
          AND ($max_place_distance_m IS NULL OR near.distance_m <= $max_place_distance_m)
          AND near.source = 'osm_sg_proximity' AND near.distance_type = 'geodesic'
     })
AND ($q = '' OR toString(l.listing_id) = $q
     OR toLower(coalesce(l.title, '')) CONTAINS toLower($q)
     OR EXISTS { MATCH (l)-[:IN_PROJECT]->(p:Project) WHERE toString(p.project_id) = $q }
     OR EXISTS { MATCH (l)-[:LISTED_BY]->(a:Agent)
                 WHERE toLower(coalesce(a.name, '')) CONTAINS toLower($q) }
     OR EXISTS { MATCH (l)-[:NEAR_PLACE]->(pl:Place {active: true})
                 WHERE toLower(coalesce(pl.name, '')) CONTAINS toLower($q)
                   OR toLower(coalesce(pl.full_name, '')) CONTAINS toLower($q)
                   OR any(a IN coalesce(pl.alt_names, []) WHERE toLower(a) CONTAINS toLower($q)) })
"""
GRAPH_QUERY = ("MATCH (l:Listing) " + LISTING_WHERE + """
AND l.listing_id > $after
WITH l ORDER BY l.listing_id LIMIT $limit
OPTIONAL MATCH (l)-[r:IN_PROJECT|LISTED_BY|IN_DISTRICT|NEAR_MRT|NEAR_PLACE]->(n)
WHERE NOT 'Place' IN labels(n) OR (n.active = true
  AND ($place_category IS NULL OR n.category = $place_category)
  AND ($place_id IS NULL OR n.place_id = $place_id)
  AND ($max_place_distance_m IS NULL OR r.distance_m <= $max_place_distance_m))
OPTIONAL MATCH (l)-[:IN_DISTRICT]->(region:District)-[rr:HAS_NEARBY_PLACE]->(n)
WHERE 'Place' IN labels(n) AND rr.source = 'osm_sg_proximity'
RETURN l, r, n, region, rr ORDER BY l.listing_id
""")
LISTINGS_QUERY = ("MATCH (l:Listing) " + LISTING_WHERE + """
AND l.listing_id > $after
RETURN l, null AS r, null AS n ORDER BY l.listing_id LIMIT $limit
""")
DISTRICTS_QUERY = """
MATCH (d:District)
OPTIONAL MATCH (d)<-[:IN_DISTRICT]-(l:Listing)
RETURN d, count(l) AS listing_count ORDER BY d.district_code
"""
CENTER_QUERY = "MATCH (l:Listing {listing_id: $listing_id}) RETURN l, null AS r, null AS n"
NEARBY_QUERY = """
MATCH (n:Listing)
WHERE point.distance(n.position, point({latitude: $latitude, longitude: $longitude, srid: 4326})) <= $radius_m
  AND n.listing_id <> $listing_id AND n.coordinate_status = 'valid'
WITH n, point.distance(n.position, point({latitude: $latitude, longitude: $longitude, srid: 4326})) AS distance_m
WHERE distance_m > $after_distance OR (distance_m = $after_distance AND n.listing_id > $after_listing_id)
RETURN n, distance_m
ORDER BY distance_m, n.listing_id LIMIT $limit
"""
EXPAND_QUERY = ("""
MATCH (center)
WHERE (elementId(center) = $node_id OR
       ('Listing' IN labels(center) AND 'Listing:' + toString(center.listing_id) = $node_id) OR
       ('District' IN labels(center) AND 'District:' + center.district_code = $node_id) OR
       ('Place' IN labels(center) AND 'Place:' + center.place_id = $node_id) OR
       ('MRT' IN labels(center) AND 'MRT:' + center.station_id = $node_id) OR
       ('Project' IN labels(center) AND 'Project:' + toString(center.project_id) = $node_id) OR
       ('Agent' IN labels(center) AND 'Agent:' + toString(center.agent_id) = $node_id))
  AND any(label IN labels(center) WHERE label IN $labels)
OPTIONAL MATCH (center)-[r:""" + REL_PATTERN + """]-(neighbor)
WHERE any(label IN labels(neighbor) WHERE label IN $labels)
  AND (NOT 'Place' IN labels(neighbor) OR (neighbor.active = true
       AND ($place_category IS NULL OR neighbor.category = $place_category)
       AND ($place_id IS NULL OR neighbor.place_id = $place_id)
       AND ($max_place_distance_m IS NULL OR coalesce(r.distance_m, r.minimum_distance_m) <= $max_place_distance_m)))
  AND elementId(r) > $after_edge
  AND (NOT 'Listing' IN labels(neighbor) OR EXISTS {
      MATCH (l:Listing) """ + LISTING_WHERE + """
      AND elementId(l) = elementId(neighbor)
  })
RETURN center AS l, r, neighbor AS n ORDER BY elementId(r) LIMIT $limit
""")
PLACES_QUERY = """
MATCH (l:Place {active: true})
WHERE l.place_id > $after
  AND ($category IS NULL OR l.category = $category)
  AND ($q = '' OR toLower(l.name) CONTAINS toLower($q) OR toLower(coalesce(l.name_local, '')) CONTAINS toLower($q)
       OR toLower(coalesce(l.full_name, '')) CONTAINS toLower($q)
       OR any(a IN coalesce(l.alt_names, []) WHERE toLower(a) CONTAINS toLower($q))
       OR l.place_id = $q)
  AND ($district IS NULL OR EXISTS {
      MATCH (:District {district_code: $district})-[:HAS_NEARBY_PLACE]->(l)
  })
RETURN l, null AS r, null AS n ORDER BY l.place_id LIMIT $limit
"""
NEARBY_PLACES_QUERY = """
MATCH (l:Place {active: true})
WHERE point.distance(l.position, point({latitude: $latitude, longitude: $longitude, srid: 4326})) <= $radius_m
  AND (size($categories) = 0 OR l.category IN $categories)
WITH l, point.distance(l.position, point({latitude: $latitude, longitude: $longitude, srid: 4326})) AS distance_m
WHERE distance_m > $after_distance OR (distance_m = $after_distance AND l.place_id > $after_place_id)
RETURN l, null AS r, null AS n, distance_m ORDER BY distance_m, l.place_id LIMIT $limit
"""


def entity_id(node, label):
    key = {"Listing": "listing_id", "Project": "project_id", "Agent": "agent_id",
           "District": "district_code", "MRT": "station_id"}.get(label, "place_id")
    kind = "Place" if label in ALL_CATEGORIES else label
    return f"{kind}:{node.get(key)}"


def serialize_graph(records):
    """Deduplicate and whitelist even if the Neo4j database contains other data."""
    nodes, edges = {}, {}
    for record in records:
        for key in ("l", "n", "region"):
            node = record.get(key)
            if node is None:
                continue
            label = next((name for name in BASE_LABELS[:-1] + ALL_CATEGORIES + ("Place",) if name in node.labels), None)
            if label is None:
                continue
            props = {key: node[key] for key in NODE_FIELDS[label] if node.get(key) is not None}
            caption = (props.get("title") or props.get("display_name") or props.get("name")
                       or props.get("district_code") or props.get("listing_id")
                       or props.get("project_id") or props.get("agent_id"))
            nodes[node.element_id] = {"id": node.element_id, "entity_id": entity_id(node, label),
                                      "label": label, "caption": str(caption), "properties": props}
        for rel_key in ("r", "rr"):
            rel = record.get(rel_key)
            if rel is not None and rel.type in REL_TYPES:
                edges[rel.element_id] = {
                    "id": rel.element_id, "source": rel.start_node.element_id,
                    "target": rel.end_node.element_id, "type": rel.type,
                    "properties": {k: rel[k] for k in REL_FIELDS if rel.get(k) is not None},
                }
    return {"nodes": list(nodes.values()), "edges": [edge for edge in edges.values()
            if edge["source"] in nodes and edge["target"] in nodes]}


def entities(records):
    return [{"entity_id": n["entity_id"], "label": n["label"], "properties": n["properties"]}
            for n in serialize_graph(records)["nodes"]]


def read_query(driver, database, cypher, **params):
    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        return list(session.run(CypherQuery(cypher, timeout=30), **params))


def graph_stats(driver, database):
    def query(cypher, **params):
        return read_query(driver, database, cypher, **params)
    counts = {label: 0 for label in LABELS}
    for row in query("MATCH (n) UNWIND labels(n) AS label WITH label, count(*) AS count "
                     "WHERE label IN $labels RETURN label, count", labels=list(LABELS)):
        counts[row["label"]] = row["count"]
    relationships = {kind: 0 for kind in REL_TYPES}
    for row in query("MATCH ()-[r]->() WHERE type(r) IN $types RETURN type(r) AS type, count(*) AS count",
                     types=list(REL_TYPES)):
        relationships[row["type"]] = row["count"]
    listing_types = [dict(row) for row in query(
        "MATCH (l:Listing) RETURN l.listing_type AS name, count(*) AS count ORDER BY name")]
    districts = [dict(row) for row in query(
        "MATCH (l:Listing)-[:IN_DISTRICT]->(d:District) "
        "RETURN d.district_code AS name, count(*) AS count ORDER BY name")]
    property_types = [row["name"] for row in query(
        "MATCH (l:Listing) WHERE l.property_type IS NOT NULL RETURN DISTINCT l.property_type AS name ORDER BY name")]
    stations = [dict(row) for row in query(
        "MATCH (l:Listing)-[:NEAR_MRT]->(m:MRT) WHERE m.station_id IS NOT NULL "
        "RETURN m.station_id AS station_id, m.name AS name, m.transport_mode AS transport_mode, "
        "m.codes AS codes, count(l) AS count ORDER BY name, transport_mode")]
    coordinates = dict(query(
        "MATCH (l:Listing) RETURN sum(CASE WHEN l.position IS NOT NULL THEN 1 ELSE 0 END) AS valid, "
        "sum(CASE WHEN l.coordinate_status = 'invalid' THEN 1 ELSE 0 END) AS invalid, "
        "sum(CASE WHEN coalesce(l.coordinate_status, 'missing') = 'missing' THEN 1 ELSE 0 END) AS missing")[0])
    distances = dict(query(
        "MATCH (:Listing)-[r:NEAR_MRT]->() RETURN sum(CASE WHEN r.distance_m IS NOT NULL THEN 1 ELSE 0 END) AS reported, "
        "sum(CASE WHEN r.distance_status = 'invalid' THEN 1 ELSE 0 END) AS invalid, "
        "sum(CASE WHEN coalesce(r.distance_status, 'missing') = 'missing' THEN 1 ELSE 0 END) AS missing")[0])
    coverage = dict(query(
        "MATCH (l:Listing) RETURN count(l) AS listings, "
        "sum(CASE WHEN EXISTS { MATCH (l)-[:NEAR_PLACE]->(:Place {active: true}) } THEN 1 ELSE 0 END) AS with_places, "
        "sum(CASE WHEN l.position IS NULL THEN 1 ELSE 0 END) AS unknown_location")[0])
    datasets = [dict(row["d"]) for row in query("MATCH (d:Dataset) RETURN d")]
    return {"database": database, "nodes": counts, "relationships": relationships,
            "total_nodes": sum(counts[label] for label in BASE_LABELS),
            "total_relationships": sum(relationships.values()), "listing_types": listing_types,
            "districts": districts, "property_types": property_types, "stations": stations,
            "coordinates": coordinates, "coordinate_coverage_percent": round(100 * coordinates["valid"] / counts["Listing"], 2) if counts["Listing"] else 0.0,
            "platform_distances": distances, "place_coverage": coverage, "listing_snapshot": snapshot_info(),
            "datasets": datasets, "spatial_distance_type": "geodesic", "proximity_edges_persisted": True,
            "listing_proximity_edges_persisted": False, "place_proximity_edges_persisted": True,
            "warnings": ["HAS_NEARBY_PLACE is listing-proximity evidence, not official district membership.",
                         "Missing listing coordinates mean unknown nearby coverage, not zero amenities."]}


def create_app(driver=None, database=None, sql_engine=None, sqlite_path=None, database_url=None):
    if sqlite_path is not None and database_url is not None:
        raise ValueError("Choose either sqlite_path or database_url, not both")
    load_graph_env()
    db_name = database or os.getenv("NEO4J_DATABASE", "neo4j")

    @asynccontextmanager
    async def lifespan(app):
        app.state.driver = driver if driver is not None else create_graph_driver()
        app.state.sql_engine = sql_engine
        try:
            if sql_engine is None:
                if sqlite_path is not None or database_url or os.getenv("PROPERTYGURU_READ_DATABASE_URL") or os.getenv("PROPERTYGURU_DATABASE_URL"):
                    app.state.sql_engine = open_api_source(sqlite_path=sqlite_path, database_url=database_url)
                if os.getenv("PROPERTYGURU_API_SQLITE"):
                    logger.warning("PROPERTYGURU_API_SQLITE no longer selects the API source; use explicit --sqlite instead.")
            yield
        finally:
            if driver is None:
                app.state.driver.close()
            if sql_engine is None and app.state.sql_engine is not None:
                app.state.sql_engine.dispose()

    app = FastAPI(title="Property Data API", version="1.0.0", lifespan=lifespan,
                  description="Read-only PostgreSQL details + Neo4j relationships and POIs. Versioned HTTP API only; no arbitrary queries.")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/v1/"):
            detail = exc.detail if isinstance(exc.detail, dict) else {"code": f"HTTP_{exc.status_code}", "message": exc.detail}
            return JSONResponse(status_code=exc.status_code, content={"error": detail})
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        if request.url.path.startswith("/api/v1/"):
            return JSONResponse(status_code=422, content={"error": {"code": "INVALID_ARGUMENT", "message": "Invalid request", "fields": errors}})
        return JSONResponse(status_code=422, content={"detail": errors})

    def execute(request, cypher, **params):
        try:
            return read_query(request.app.state.driver, db_name, cypher, **params)
        except (Neo4jError, DriverError):
            logger.warning("Neo4j data query failed", exc_info=True)
            raise HTTPException(503, "Neo4j unavailable. Check the service and NEO4J_* configuration.")

    def sql(request, fn, *args):
        engine = request.app.state.sql_engine
        if engine is None:
            raise HTTPException(503, {"code": "SQL_NOT_CONFIGURED", "message": "Set PROPERTYGURU_DATABASE_URL or explicitly choose --sqlite."})
        try:
            return fn(engine, *args), engine.dialect.name
        except SQLAlchemyError:
            logger.warning("Canonical SQL query failed", exc_info=True)
            raise HTTPException(503, {"code": "SQL_UNAVAILABLE", "message": "SQL unavailable; no automatic SQLite fallback."})

    def cursor(value, resource, filters, default):
        try:
            return decode_cursor(value, resource, filters, default)
        except ValueError as exc:
            raise HTTPException(422, {"code": "INVALID_CURSOR", "message": str(exc)})

    def number_cursor(value, resource, filters):
        result = cursor(value, resource, filters, 0)
        if not isinstance(result, int) or isinstance(result, bool) or not 0 <= result <= 9223372036854775807:
            raise HTTPException(422, {"code": "INVALID_CURSOR", "message": "Expected a nonnegative integer key"})
        return result

    def page(items, resource, filters, keys, size, store="neo4j", warnings=None):
        more = len(items) > size
        return {"items": items[:size], "next_cursor": encode_cursor(resource, filters, keys[size - 1]) if more else None,
                "has_more": more, "page_size": size, "source": {"store": store,
                "role": "rebuildable_projection" if store == "neo4j" else "canonical_listings", "route_verified": False},
                "warnings": warnings or []}

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/neo4j-browser", status_code=307)

    @app.get("/neo4j-browser", include_in_schema=False)
    def official_browser():
        from data.graph.browser import browser_url
        return RedirectResponse(browser_url(database=db_name), status_code=307)

    @app.get("/health")
    def health(request: Request):
        execute(request, "RETURN 1 AS ok")
        return {"status": "ok", "database": db_name}

    @app.get("/ready", tags=["data"])
    def readiness(request: Request):
        execute(request, "RETURN 1 AS ok")
        listing_id, store = sql(request, sql_readiness)
        if listing_id is None:
            raise HTTPException(503, {"code": "SQL_EMPTY", "message": "Import the fixed snapshot before serving data."})
        records = execute(request, "MATCH (l:Listing {listing_id:$listing_id}) RETURN l.listing_id AS listing_id", listing_id=listing_id)
        if not records:
            raise HTTPException(503, {"code": "GRAPH_NOT_SYNCED", "message": "Canonical SQL listing is absent from Neo4j; run a full import."})
        return {"status": "ok", "database": db_name, "sql_source": store, "listing_snapshot": snapshot_info()}

    @app.get("/api/stats")
    @app.get("/api/v1/stats", tags=["data"])
    def stats(request: Request):
        try:
            return graph_stats(request.app.state.driver, db_name)
        except (Neo4jError, DriverError):
            logger.warning("Neo4j statistics failed", exc_info=True)
            raise HTTPException(503, "Neo4j unavailable. Check the service and NEO4J_* configuration.")

    @app.get("/api/v1/capabilities", tags=["data"])
    def capabilities(request: Request):
        engine = request.app.state.sql_engine
        return {"api_version": "1", "read_only": True, "openapi": "/openapi.json", "page_size_max": 1000,
                "sql_source": engine.dialect.name if engine is not None else None, "sql_fallback": False,
                "listing_snapshot": snapshot_info(), "readiness": "/ready",
                "place_categories": list(ALL_CATEGORIES), "graph_ids": "entity_id is stable; graph node id is Neo4j elementId",
                "distance_types": {"NEAR_MRT": "platform_reported (not route verified)", "NEAR_PLACE": "geodesic",
                                   "NEAR_LISTING": "geodesic (query-time only, not persisted)"},
                "district_place_membership": "near_listings_in_district, NOT an administrative boundary",
                "pagination": "Follow next_cursor until null; page_size is not a total result limit."}

    @app.get("/api/v1/districts", response_model=Page[dict[str, Any]], tags=["data"])
    def districts(request: Request):
        records = execute(request, DISTRICTS_QUERY)
        items = [{"district_code": r["d"]["district_code"], "entity_id": entity_id(r["d"], "District"),
                  "listing_count": r["listing_count"]} for r in records]
        return page(items, "districts", {}, [], max(1, len(items)))

    @app.post("/api/v1/listings/search", response_model=Page[Entity], tags=["listings"])
    def search_listings(body: ListingSearch, request: Request):
        filters = body.filters.model_dump()
        after = number_cursor(body.cursor, "listings", filters)
        records = execute(request, LISTINGS_QUERY, **filters, after=after, limit=body.page_size + 1)
        items = entities(records)
        warnings = [f"Listings are the fixed {SNAPSHOT_DATE} snapshot, not current availability.",
                    "Prices are SGD monthly rent for RENT and sale prices for SALE; do not mix averages."]
        if any(filters[key] is not None for key in ("place_id", "place_category", "max_place_distance_m")):
            warnings.append("POI filters use the persisted enrichment radius (see stats.datasets). Listings with unknown coordinates are excluded, not evidence of no amenities.")
        return page(items, "listings", filters, [r["l"]["listing_id"] for r in records], body.page_size,
                    warnings=warnings)

    @app.get("/api/v1/listings/{listing_id}", response_model=Item[Entity], tags=["listings"])
    def get_listing(request: Request, listing_id: int = PathParam(ge=1, le=9223372036854775807)):
        result, store = sql(request, listing_detail, listing_id)
        if result is None:
            raise HTTPException(404, {"code": "LISTING_NOT_FOUND", "message": "Listing not found in the selected SQL source"})
        return {"item": {"entity_id": f"Listing:{listing_id}", "label": "Listing", "properties": result},
                "source": {"store": store, "role": "canonical_listings"}}

    @app.get("/api/v1/listings/{listing_id}/context", response_model=ListingContext, tags=["listings"])
    def listing_context(request: Request, listing_id: int = PathParam(ge=1, le=9223372036854775807)):
        records = execute(request, "MATCH (l:Listing {listing_id: $listing_id}) "
            "OPTIONAL MATCH (l)-[r:IN_PROJECT|LISTED_BY|IN_DISTRICT|NEAR_MRT|NEAR_PLACE]->(n) "
            "WHERE NOT 'Place' IN labels(n) OR n.active = true RETURN l, r, n", listing_id=listing_id)
        if not records:
            raise HTTPException(404, "Listing not found in Neo4j")
        graph = serialize_graph(records)
        by_id = {n["id"]: {"entity_id": n["entity_id"], "label": n["label"], "properties": n["properties"]} for n in graph["nodes"]}
        return {"listing": by_id[records[0]["l"].element_id],
                "relationships": [{"type": e["type"], "target": by_id[e["target"]], "evidence": e["properties"]} for e in graph["edges"]],
                "source": {"store": "neo4j", "role": "rebuildable_projection"},
                "warnings": ["Missing coordinates mean unknown POI coverage. NEAR_PLACE is not a walking route."]}

    @app.get("/api/v1/listings/{listing_id}/images", response_model=Page[dict[str, Any]], tags=["listings"])
    def get_images(request: Request, listing_id: int = PathParam(ge=1, le=9223372036854775807), cursor_value: str | None = Query(default=None, alias="cursor", max_length=2000),
                   page_size: int = Query(default=50, ge=1, le=1000)):
        filters = {"listing_id": listing_id}
        after = number_cursor(cursor_value, "images", filters)
        rows, store = sql(request, listing_images, listing_id, after, page_size)
        if rows is None:
            raise HTTPException(404, "Listing not found in the selected SQL source")
        return page(rows, "images", filters, [r["id"] for r in rows], page_size, store=store)

    @app.post("/api/v1/places/search", response_model=Page[Entity], tags=["places"])
    def search_places(body: PlaceSearch, request: Request):
        filters = body.filters.model_dump()
        after = cursor(body.cursor, "places", filters, "")
        if not isinstance(after, str) or len(after) > 150:
            raise HTTPException(422, "Invalid place cursor")
        records = execute(request, PLACES_QUERY, **filters, after=after, limit=body.page_size + 1)
        return page(entities(records), "places", filters, [r["l"]["place_id"] for r in records], body.page_size,
                    warnings=["District filter means proximity to district listings, not official membership."] if filters["district"] else [])

    @app.get("/api/v1/places/{place_id:path}", response_model=Item[Entity], tags=["places"])
    def get_place(request: Request, place_id: str):
        if len(place_id) > 150:
            raise HTTPException(422, "place_id too long")
        records = execute(request, "MATCH (l:Place {place_id: $place_id, active: true}) RETURN l, null AS r, null AS n", place_id=place_id)
        if not records:
            raise HTTPException(404, "Place not found")
        return {"item": entities(records)[0], "source": {"store": "neo4j", "role": "external_poi_projection"}}

    def center_position(request, listing_id):
        records = execute(request, CENTER_QUERY, listing_id=listing_id)
        if not records:
            raise HTTPException(404, "Listing not found in Neo4j")
        center = records[0]["l"]
        position = center.get("position")
        if position is None or center.get("coordinate_status") != "valid" or getattr(position, "srid", None) != 4326:
            raise HTTPException(422, {"code": "COORDINATES_UNKNOWN", "message": "Listing has no validated coordinates; nearby amenities are unknown, not absent."})
        return center, position, records

    @app.post("/api/v1/places/nearby", response_model=Page[dict[str, Any]], tags=["places"])
    def nearby_places(body: NearbyPlaces, request: Request):
        filters = body.model_dump(exclude={"cursor", "page_size"})
        if body.listing_id is not None:
            center, position, _ = center_position(request, body.listing_id)
            lat, lon = position.latitude, position.longitude
            filters["center_source_updated_at"] = center.get("source_updated_at")
        else:
            lat, lon = body.latitude, body.longitude
        # Include center coordinates in the cursor binding: a relocated listing invalidates it.
        filters.update(latitude=lat, longitude=lon)
        key = cursor(body.cursor, "nearby_places", filters, [-1, ""])
        if (not isinstance(key, list) or len(key) != 2 or not isinstance(key[0], (int, float))
                or not -1 <= key[0] <= 10000 or not isinstance(key[1], str) or len(key[1]) > 150):
            raise HTTPException(422, "Invalid proximity cursor")
        records = execute(request, NEARBY_PLACES_QUERY, latitude=lat, longitude=lon, radius_m=body.radius_m,
                          categories=body.categories, after_distance=key[0], after_place_id=key[1], limit=body.page_size + 1)
        computed_at = datetime.now(timezone.utc).isoformat()
        items = [{"place": entities([r])[0], "distance_m": round(r["distance_m"], 2), "distance_unit": "m",
                  "distance_type": "geodesic", "route_verified": False, "computed_at": computed_at,
                  "measurement_method": "neo4j.point.distance.WGS84"} for r in records]
        result = page(items, "nearby_places", filters, [[r["distance_m"], r["l"]["place_id"]] for r in records], body.page_size,
                      warnings=["Straight-line distance to an OSM reference point, not a walking route or MOE eligibility."])
        result["source"]["distance_type"] = "geodesic"
        return result

    @app.post("/api/v1/graph/search", response_model=GraphPage, tags=["graph"])
    def search_graph(body: ListingSearch, request: Request):
        filters = body.filters.model_dump()
        after = number_cursor(body.cursor, "graph", filters)
        records = execute(request, GRAPH_QUERY, **filters, after=after, limit=body.page_size + 1)
        ids = sorted({r["l"]["listing_id"] for r in records})
        chosen = set(ids[:body.page_size])
        result = serialize_graph([r for r in records if r["l"]["listing_id"] in chosen])
        more = len(ids) > body.page_size
        result.update(listing_count=len(chosen), page_size=body.page_size, has_more=more,
                      next_cursor=encode_cursor("graph", filters, ids[body.page_size - 1]) if more else None)
        return result

    @app.get("/api/nearby", tags=["spatial"])
    def nearby(request: Request, listing_id: int = Query(ge=1, le=9223372036854775807),
               radius_m: float = Query(default=500, ge=1, le=10000), limit: int = Query(default=500, ge=1, le=1000),
               cursor_value: str | None = Query(default=None, alias="cursor", max_length=2000)):
        center, position, centers = center_position(request, listing_id)
        binding = {"listing_id": listing_id, "latitude": position.latitude, "longitude": position.longitude, "radius_m": radius_m}
        key = cursor(cursor_value, "nearby_listings", binding, [-1, 0])
        if (not isinstance(key, list) or len(key) != 2 or not isinstance(key[0], (int, float))
                or not -1 <= key[0] <= 10000 or not isinstance(key[1], int)):
            raise HTTPException(422, "Invalid nearby listing cursor")
        neighbors = execute(request, NEARBY_QUERY, listing_id=listing_id, latitude=position.latitude,
                            longitude=position.longitude, radius_m=radius_m, limit=limit + 1,
                            after_distance=key[0], after_listing_id=key[1])
        rows = neighbors[:limit]
        result = serialize_graph(centers + [{"l": row["n"], "r": None, "n": None} for row in rows])
        computed_at = datetime.now(timezone.utc).isoformat()
        result["edges"] = [{"id": f"spatial:{listing_id}:{row['n']['listing_id']}", "source": center.element_id,
                            "target": row["n"].element_id, "type": "NEAR_LISTING", "properties": {
                                "distance_m": round(row["distance_m"], 2), "distance_unit": "m", "distance_type": "geodesic",
                                "measurement_method": "neo4j.point.distance.WGS84", "route_verified": False,
                                "source": "derived_from_propertyguru_coordinates", "is_virtual": True, "symmetric": True,
                                "computed_at": computed_at, "center_source_updated_at": center.get("source_updated_at"),
                                "neighbor_source_updated_at": row["n"].get("source_updated_at")}} for row in rows]
        result.update(center_id=center.element_id, center_listing_id=listing_id, radius_m=radius_m, limit=limit,
                      neighbor_count=len(rows), truncated=len(neighbors) > limit, distance_type="geodesic", persisted=False,
                      next_cursor=encode_cursor("nearby_listings", binding, [rows[-1]["distance_m"], rows[-1]["n"]["listing_id"]])
                      if len(neighbors) > limit else None)
        return result

    @app.post("/api/v1/graph/expand", response_model=GraphPage, tags=["graph"])
    def expand_graph(body: GraphExpand, request: Request):
        filters = body.filters.model_dump()
        binding = {"entity_id": body.entity_id, "filters": filters}
        after = cursor(body.cursor, "expand", binding, "")
        if not isinstance(after, str) or len(after) > 200:
            raise HTTPException(422, "Invalid expansion cursor")
        records = execute(request, EXPAND_QUERY, node_id=body.entity_id, after_edge=after,
                          limit=body.page_size + 1, labels=list(BASE_LABELS), **filters)
        if not records:
            raise HTTPException(404, "Entity not found; refresh after rebuilding Neo4j")
        result = serialize_graph(records[:body.page_size])
        more = len(records) > body.page_size
        result.update(has_more=more, page_size=body.page_size, next_cursor=encode_cursor("expand", binding,
                      records[body.page_size - 1]["r"].element_id) if more else None)
        return result

    return app
