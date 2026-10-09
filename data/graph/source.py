"""Explicit SQL sources for graph imports. Never fall back to another database."""
import os
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from data.core.paths import ENV_PATH


def load_graph_env():
    from dotenv import load_dotenv
    # Independent of the calling directory; real environment values take priority.
    load_dotenv(ENV_PATH, override=False)


def open_api_source(*, sqlite_path=None, database_url=None):
    """PG by default; only an explicit --sqlite selects the local snapshot.

    Legacy PROPERTYGURU_API_SQLITE cannot silently override a PG deployment.
    """
    if sqlite_path is not None and database_url is not None:
        raise ValueError("Choose either --sqlite or --database-url, not both")
    return open_graph_source(sqlite_path=sqlite_path,
                             database_url=(database_url or os.getenv("PROPERTYGURU_READ_DATABASE_URL"))
                             if sqlite_path is None else None)


def open_graph_source(*, sqlite_path=None, database_url=None):
    """SQLite is opened mode=ro: a typo cannot create an empty database."""
    if sqlite_path is not None and database_url is not None:
        raise ValueError("Choose either --sqlite or --database-url, not both")
    if sqlite_path is not None:
        path = Path(sqlite_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"SQLite source does not exist: {path}")
        uri = path.as_uri() + "?mode=ro"
        return create_engine(
            "sqlite://",
            creator=lambda: sqlite3.connect(uri, uri=True, check_same_thread=False),
            poolclass=NullPool,
        )
    url = database_url or os.getenv("PROPERTYGURU_DATABASE_URL")
    if not url:
        raise ValueError("Specify --sqlite data/propertyguru.db or set PROPERTYGURU_DATABASE_URL")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        raise ValueError("--database-url must be PostgreSQL; use --sqlite for a read-only SQLite source")
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
