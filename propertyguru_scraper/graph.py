"""Rebuildable Neo4j projection of committed PropertyGuru PostgreSQL listings.

Only explicitly selected, non-sensitive columns cross the database boundary. A sync
can be replayed at any time; PostgreSQL is the sole source of truth.
"""
import os
from decimal import Decimal

from sqlalchemy import select, func

from models import Agent, Property


# No phone, license, address/unit, images, or raw JSON in this projection.
LISTING_FIELDS = (
    "listing_type", "title", "property_type", "price", "currency", "psf",
    "bedrooms", "bathrooms", "floor_area_sqft", "land_area_sqft",
    "postal_code", "tenure_category", "latitude", "longitude", "url",
)

CONSTRAINTS = (
    "CREATE CONSTRAINT listing_id_unique IF NOT EXISTS FOR (n:Listing) REQUIRE n.listing_id IS UNIQUE",
    "CREATE CONSTRAINT project_id_unique IF NOT EXISTS FOR (n:Project) REQUIRE n.project_id IS UNIQUE",
    "CREATE CONSTRAINT agent_id_unique IF NOT EXISTS FOR (n:Agent) REQUIRE n.agent_id IS UNIQUE",
    "CREATE CONSTRAINT district_code_unique IF NOT EXISTS FOR (n:District) REQUIRE n.district_code IS UNIQUE",
    "CREATE CONSTRAINT mrt_name_unique IF NOT EXISTS FOR (n:MRT) REQUIRE n.name IS UNIQUE",
)

# Reset only the four relationships owned by this projection. This also removes
# stale links when a PG row changes or loses an ID on a subsequent sync.
UPSERT_LISTINGS = """
UNWIND $rows AS row
MERGE (l:Listing {listing_id: row.listing_id})
SET l = row.props
WITH l, row
OPTIONAL MATCH (l)-[old:IN_PROJECT|LISTED_BY|IN_DISTRICT|NEAR_MRT]->()
DELETE old
WITH DISTINCT l, row
FOREACH (_ IN CASE WHEN row.project_id IS NULL THEN [] ELSE [1] END |
    MERGE (p:Project {project_id: row.project_id})
    MERGE (l)-[:IN_PROJECT]->(p)
)
FOREACH (_ IN CASE WHEN row.agent_id IS NULL THEN [] ELSE [1] END |
    MERGE (a:Agent {agent_id: row.agent_id})
    SET a.name = row.agent_name, a.agency_name = row.agency_name
    MERGE (l)-[:LISTED_BY]->(a)
)
FOREACH (_ IN CASE WHEN row.district_code IS NULL THEN [] ELSE [1] END |
    MERGE (d:District {district_code: row.district_code})
    MERGE (l)-[:IN_DISTRICT]->(d)
)
FOREACH (_ IN CASE WHEN row.station IS NULL THEN [] ELSE [1] END |
    MERGE (m:MRT {name: row.station})
    MERGE (l)-[:NEAR_MRT]->(m)
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
        # SET l = props clears fields removed from PostgreSQL on replay.
        if value is not None:
            props[field] = value
    station = _key(record["nearest_mrt"])
    return {
        "listing_id": listing_id,
        "props": props,
        "project_id": _key(record["project_id"]),
        "agent_id": _key(record["agent_id"]),
        "agent_name": record["agent_name"],
        "agency_name": record["agency_name"],
        "district_code": _key(record["district_code"]),
        "station": station,
    }


def iter_listing_batches(engine, batch_size=500, limit=None):
    """Keyset-page committed PG rows; never materialize the entire table.

    A short PG connection per batch avoids holding a server cursor open across
    Neo4j writes. For a consistent snapshot during concurrent ingestion, rerun
    sync after the crawl (or use --sync-graph on run).
    """
    if batch_size <= 0 or limit is not None and limit < 0:
        raise ValueError("batch_size must be positive and limit must be nonnegative")
    cursor = 0
    emitted = 0
    columns = [Property.listing_id] + [getattr(Property, f) for f in LISTING_FIELDS] + [
        Property.project_id, Property.agent_id, Property.district_code,
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


def sync_graph(engine, *, limit=None, batch_size=500, driver=None, database=None):
    """Project PostgreSQL into Neo4j; return listing count. Own driver if not injected."""
    if batch_size <= 0 or limit is not None and limit < 0:
        raise ValueError("batch_size must be positive and limit must be nonnegative")
    owned = driver is None
    if owned:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password")),
        )
    try:
        # Fail fast even for an empty PostgreSQL table.
        driver.verify_connectivity()
        count = 0
        with driver.session(database=database or os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            for constraint in CONSTRAINTS:
                session.run(constraint).consume()
            for rows in iter_listing_batches(engine, batch_size=batch_size, limit=limit):
                session.execute_write(_write_batch, rows)
                count += len(rows)
        return count
    finally:
        if owned:
            driver.close()
