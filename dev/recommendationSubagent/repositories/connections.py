import os
from pathlib import Path
from .listing_repository import FixtureListingRepository, SQLListingRepository
from .geographic_repository import (
    FixtureGeographicRepository,
    Neo4jGeographicRepository,
)
from dev.sharedContract.recommendationSubagentContract.errors import RecommendationError

ROOT = Path(__file__).resolve().parents[3]


def load_environment():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)


def listing_connection():
    from sqlalchemy import create_engine

    url = os.getenv("PROPERTYGURU_READ_DATABASE_URL") or os.getenv(
        "PROPERTYGURU_DATABASE_URL"
    )
    if not url:
        raise RecommendationError(
            "NOT_CONFIGURED",
            "Set PROPERTYGURU_READ_DATABASE_URL or PROPERTYGURU_DATABASE_URL",
        )
    if not url.startswith("postgresql"):
        raise RecommendationError(
            "INVALID_ARGUMENT", "Database mode requires the existing PostgreSQL store"
        )
    return SQLListingRepository(
        create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    )


def geographic_connection():
    from neo4j import GraphDatabase

    uri = os.getenv("NEO4J_URI")
    if not uri:
        raise RecommendationError("NOT_CONFIGURED", "Set NEO4J_URI")
    auth = (
        None
        if os.getenv("NEO4J_AUTH") == "none"
        else (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", ""))
    )
    return Neo4jGeographicRepository(
        GraphDatabase.driver(
            uri, auth=auth, connection_timeout=5, max_transaction_retry_time=0
        ),
        os.getenv("NEO4J_DATABASE", "neo4j"),
    )


def repositories(mode):
    if mode == "fixture":
        return FixtureListingRepository(), FixtureGeographicRepository()
    listing = listing_connection()
    try:
        return listing, geographic_connection()
    except Exception:
        listing.close()
        raise
