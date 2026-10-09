"""Rebuildable Neo4j projection of committed PropertyGuru SQL listings.

Only explicitly selected, non-sensitive columns cross the database boundary. A sync
can be replayed at any time; PostgreSQL or SQLite remains the source of truth.
"""
import os
from decimal import Decimal
import math

from data.graph.semantics import (distance_label, location_for, metric_status,
                             nonnegative_number, normalize_station, source_timestamp)

from sqlalchemy import select, func

from data.storage.models import Agent, Property


# No phone, license, address/unit, images, or raw JSON in this projection.
LISTING_FIELDS = (
    "listing_type", "title", "property_type", "price", "currency", "psf",
    "bedrooms", "bathrooms", "floor_area_sqft", "land_area_sqft",
    "postal_code", "tenure_category", "latitude", "longitude", "url",
    "build_year", "mrt_distance_m", "mrt_walking_mins",
)

CONSTRAINTS = (
    "CREATE CONSTRAINT listing_id_unique IF NOT EXISTS FOR (n:Listing) REQUIRE n.listing_id IS UNIQUE",
    "CREATE CONSTRAINT project_id_unique IF NOT EXISTS FOR (n:Project) REQUIRE n.project_id IS UNIQUE",
    "CREATE CONSTRAINT agent_id_unique IF NOT EXISTS FOR (n:Agent) REQUIRE n.agent_id IS UNIQUE",
    "CREATE CONSTRAINT district_code_unique IF NOT EXISTS FOR (n:District) REQUIRE n.district_code IS UNIQUE",
    "CREATE CONSTRAINT mrt_station_id_unique IF NOT EXISTS FOR (n:MRT) REQUIRE n.station_id IS UNIQUE",
)
SCHEMA_MIGRATIONS = ("DROP CONSTRAINT mrt_name_unique IF EXISTS",)
INDEXES = (
    "CREATE POINT INDEX listing_position IF NOT EXISTS FOR (n:Listing) ON (n.position)",
    "CREATE INDEX listing_type_price IF NOT EXISTS FOR (n:Listing) ON (n.listing_type, n.price)",
)
# Only remove legacy nodes observed on this projection's old edges, and only
# after a successful full sync. Never DETACH DELETE someone else's relationships.
LEGACY_MRT_CLEANUP = """
MATCH (m:MRT {legacy_projection: 'propertyguru'})
WHERE m.station_id IS NULL AND NOT (m)--()
DELETE m
"""

# Reset only the four relationships owned by this projection. This also removes
# stale links when a PG row changes or loses an ID on a subsequent sync.
UPSERT_LISTINGS = """
UNWIND $rows AS row
MERGE (l:Listing {listing_id: row.listing_id})
SET l = row.props
SET l.source = 'propertyguru', l.projection_version = 2,
    l.source_updated_at = row.source_updated_at,
    l.coordinate_status = row.coordinate_status,
    l.coordinate_validation = 'singapore_bbox_v1',
    l.location_source = 'propertyguru_listing_detail',
    l.position = CASE WHEN row.location IS NULL THEN null ELSE point(row.location) END
WITH l, row
OPTIONAL MATCH (l)-[old:IN_PROJECT|LISTED_BY|IN_DISTRICT|NEAR_MRT]->(previous)
FOREACH (_ IN CASE WHEN type(old) = 'NEAR_MRT' AND 'MRT' IN labels(previous)
                       AND previous.station_id IS NULL THEN [1] ELSE [] END |
    SET previous.legacy_projection = 'propertyguru'
)
DELETE old
WITH DISTINCT l, row
FOREACH (_ IN CASE WHEN row.project_id IS NULL THEN [] ELSE [1] END |
    MERGE (p:Project {project_id: row.project_id})
    SET p.display_name = coalesce(p.display_name, row.props.title),
        p.identity_method = 'propertyguru_project_id', p.name_source = 'listing_title'
    MERGE (l)-[:IN_PROJECT]->(p)
)
FOREACH (_ IN CASE WHEN row.agent_id IS NULL THEN [] ELSE [1] END |
    MERGE (a:Agent {agent_id: row.agent_id})
    SET a.name = coalesce(row.agent_name, a.name),
        a.agency_name = coalesce(row.agency_name, a.agency_name),
        a.identity_method = 'propertyguru_agent_id'
    MERGE (l)-[:LISTED_BY]->(a)
)
FOREACH (_ IN CASE WHEN row.district_code IS NULL THEN [] ELSE [1] END |
    MERGE (d:District {district_code: row.district_code})
    MERGE (l)-[:IN_DISTRICT]->(d)
)
FOREACH (_ IN CASE WHEN row.station_info IS NULL THEN [] ELSE [1] END |
    MERGE (m:MRT {station_id: row.station_info.station_id})
    SET m.name = row.station_info.name, m.name_key = row.station_info.name_key,
        m.transport_mode = row.station_info.transport_mode,
        m.identity_method = row.station_info.identity_method,
        m.normalization_version = row.station_info.normalization_version,
        m.aliases = reduce(acc = coalesce(m.aliases, []), alias IN [row.station_info.alias] |
                          CASE WHEN alias IN acc THEN acc ELSE acc + [alias] END),
        m.codes = reduce(acc = coalesce(m.codes, []), code IN row.station_info.codes |
                        CASE WHEN code IN acc THEN acc ELSE acc + [code] END)
    MERGE (l)-[near:NEAR_MRT]->(m)
    SET near = row.near_props
)
"""


def _key(value):
    """Do not merge missing/blank identifiers into shared nodes."""
    if value is None or value == 0 or (isinstance(value, str) and not value.strip()):
        return None
    return value.strip() if isinstance(value, str) else value


def map_listing(record):
    """Map a SQLAlchemy RowMapping (or plain mapping) to driver-safe parameters."""
    listing_id = _key(record["listing_id"])
    if listing_id is None:
        return None
    props = {"listing_id": listing_id}
    for field in LISTING_FIELDS:
        value = record[field]
        if isinstance(value, Decimal):
            value = float(value)
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        # SET l = props clears fields removed from SQL on replay.
        if value is not None:
            props[field] = value
    location, coordinate_status = location_for(record.get("latitude"), record.get("longitude"))
    if location is None:
        props.pop("latitude", None)
        props.pop("longitude", None)
    else:
        props["latitude"] = location["latitude"]
        props["longitude"] = location["longitude"]
    distance = nonnegative_number(record.get("mrt_distance_m"))
    minutes = nonnegative_number(record.get("mrt_walking_mins"))
    for field, value in (("mrt_distance_m", distance), ("mrt_walking_mins", minutes)):
        if value is None:
            props.pop(field, None)
        else:
            props[field] = value
    station = normalize_station(record.get("nearest_mrt"))
    observed_at = source_timestamp(record.get("updated_at"))
    near_props = {
        "distance_m": distance, "walking_mins": minutes,
        "distance_type": "platform_reported", "distance_unit": "m", "walking_time_unit": "min",
        "measurement_method": "propertyguru_nearby_text", "source": "propertyguru",
        "route_verified": False, "observed_at": observed_at,
        "observation_basis": "source_row_updated_at",
        "distance_status": metric_status(record.get("mrt_distance_m")),
        "walking_time_status": metric_status(record.get("mrt_walking_mins")),
        "distance_label": distance_label(distance, minutes),
        "raw_station_label": station["alias"] if station else None,
        "station_status": station["station_status"] if station else None,
    }
    return {
        "listing_id": listing_id,
        "props": props,
        "project_id": _key(record["project_id"]),
        "agent_id": _key(record["agent_id"]),
        "agent_name": _key(record["agent_name"]),
        "agency_name": _key(record["agency_name"]),
        "district_code": _key(record["district_code"]),
        "station": station["alias"] if station else None,
        "station_info": station,
        "location": location, "coordinate_status": coordinate_status,
        "source_updated_at": observed_at, "near_props": near_props,
    }


def iter_listing_batches(engine, batch_size=500, limit=None):
    """Keyset-page committed SQL rows; never materialize the entire table.

    A short SQL connection per batch avoids holding a server cursor open across
    Neo4j writes. For a consistent snapshot during concurrent ingestion, rerun
    sync after the crawl (or use --sync-graph on run).
    """
    if batch_size <= 0 or limit is not None and limit < 0:
        raise ValueError("batch_size must be positive and limit must be nonnegative")
    cursor = 0
    emitted = 0
    columns = [Property.listing_id] + [getattr(Property, f) for f in LISTING_FIELDS] + [
        Property.project_id, Property.agent_id, Property.district_code, Property.updated_at,
        Property.nearest_mrt, func.coalesce(Property.agent_name, Agent.name).label("agent_name"),
        func.coalesce(Property.agency_name, Agent.agency_name).label("agency_name"),
    ]
    while limit is None or emitted < limit:
        size = min(batch_size, limit - emitted) if limit is not None else batch_size
        stmt = (select(*columns).select_from(Property)
                .outerjoin(Agent, Agent.agent_id == Property.agent_id)
                .where(Property.listing_id > cursor)
                .order_by(Property.listing_id).limit(size))
        with engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(stmt).mappings()]
        if not rows:
            break
        cursor = rows[-1]["listing_id"]
        emitted += len(rows)
        batch = [mapped for row in rows if (mapped := map_listing(row)) is not None]
        if batch:
            yield batch


def _write_batch(tx, rows):
    tx.run(UPSERT_LISTINGS, rows=rows).consume()


def graph_auth():
    """Use explicit no-auth only on loopback; otherwise require real credentials.

    NEO4J_AUTH follows the Docker convention: 'none' or 'user/password'. If
    unset, keep the existing NEO4J_USER/NEO4J_PASSWORD configuration contract.
    """
    from urllib.parse import urlsplit
    configured = os.getenv("NEO4J_AUTH", "").strip()
    if configured == "none":
        host = urlsplit(os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")).hostname
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("NEO4J_AUTH=none is allowed only for a loopback Neo4j URI")
        return None
    if configured:
        user, separator, password = configured.partition("/")
        if not separator or not user or not password:
            raise ValueError("NEO4J_AUTH must be 'none' or 'user/password'")
        return user, password
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        raise ValueError("Set NEO4J_PASSWORD in the project .env or environment first")
    return os.getenv("NEO4J_USER", "neo4j"), password


def create_graph_driver():
    """Credentials stay on the server; unauthenticated mode is explicit/local."""
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
        auth=graph_auth(),
        connection_timeout=10,
        max_transaction_retry_time=15,
    )


def sync_graph(engine, *, limit=None, batch_size=500, driver=None, database=None,
               progress_callback=None):
    """Project SQL into Neo4j; return listing count. Own driver if not injected.

    Progress is reported only after each batch transaction has committed. A failed
    sync leaves earlier batches intact; replay is safe and repairs partial imports.
    """
    if batch_size <= 0 or limit is not None and limit < 0:
        raise ValueError("batch_size must be positive and limit must be nonnegative")
    owned = driver is None
    if owned:
        driver = create_graph_driver()
    try:
        # Fail fast even for an empty PostgreSQL table.
        driver.verify_connectivity()
        count = 0
        with driver.session(database=database or os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            for statement in SCHEMA_MIGRATIONS + CONSTRAINTS + INDEXES:
                session.run(statement).consume()
            for rows in iter_listing_batches(engine, batch_size=batch_size, limit=limit):
                session.execute_write(_write_batch, rows)
                count += len(rows)
                if progress_callback:
                    progress_callback(count)
            if limit is None:
                session.run(LEGACY_MRT_CLEANUP).consume()
        return count
    finally:
        if owned:
            driver.close()


def sync_projection(engine, *, driver=None, database=None, limit=None, batch_size=500,
                    progress_callback=None, include_places=True, radius_m=1500):
    """One import path for both CLIs: listings, then public-place evidence."""
    owned = driver is None
    if owned:
        driver = create_graph_driver()
    try:
        count = sync_graph(engine, driver=driver, database=database, limit=limit,
                           batch_size=batch_size, progress_callback=progress_callback)
        enrichment = None
        if include_places:
            from data.graph.places import acquire_places, sync_places
            rows, metadata = acquire_places()
            enrichment = sync_places(driver, rows, metadata, database=database,
                                     batch_size=batch_size, radius_m=radius_m)
        return {"listings": count, "place_enrichment": enrichment}
    finally:
        if owned:
            driver.close()
