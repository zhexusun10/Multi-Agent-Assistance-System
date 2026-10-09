"""Read-only access to canonical SQL records, independent of crawler globals.

No fallback engine, DDL, raw SQL endpoint, contacts, unit numbers or raw_json.
The caller explicitly chooses PostgreSQL or a mode=ro SQLite snapshot.
"""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import math

from sqlalchemy import select, text

from data.graph.semantics import LISTING_FIELDS, location_for
from data.storage.models import Property, PropertyImage

DETAIL_FIELDS = ("listing_id",) + LISTING_FIELDS + (
    "project_id", "district_code", "district_text", "region_code", "region_text", "nearest_mrt",
    "detail_fetched", "posted_at", "created_at", "updated_at", "agent_id", "agent_name", "agency_name",
)


@contextmanager
def readonly_connection(engine):
    with engine.connect() as connection, connection.begin():
        if engine.dialect.name == "postgresql":
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = '15s'"))
        yield connection


def json_value(value):
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def sql_readiness(engine):
    """Probe real canonical tables, not just a TCP connection or SELECT 1."""
    with readonly_connection(engine) as connection:
        listing_id = connection.scalar(select(Property.listing_id).order_by(Property.listing_id).limit(1))
        connection.execute(select(PropertyImage.id).limit(1))
        return listing_id


def listing_detail(engine, listing_id):
    with readonly_connection(engine) as connection:
        row = connection.execute(select(*(getattr(Property, f) for f in DETAIL_FIELDS))
                                 .where(Property.listing_id == listing_id)).mappings().first()
    if row is None:
        return None
    result = {key: json_value(value) for key, value in row.items()}
    _, result["coordinate_status"] = location_for(row.get("latitude"), row.get("longitude"))
    return result


def listing_images(engine, listing_id, after, page_size):
    with readonly_connection(engine) as connection:
        exists = connection.scalar(select(Property.listing_id).where(Property.listing_id == listing_id))
        if exists is None:
            return None
        rows = connection.execute(select(PropertyImage.id, PropertyImage.source_page,
                                         PropertyImage.image_type, PropertyImage.image_url,
                                         PropertyImage.caption, PropertyImage.display_order)
                                  .where(PropertyImage.listing_id == listing_id, PropertyImage.id > after)
                                  .order_by(PropertyImage.id).limit(page_size + 1)).mappings().all()
    return [dict(row) for row in rows]
