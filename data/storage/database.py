"""Database persistence for the scraper supporting PostgreSQL and SQLite."""
import hashlib
import logging
from typing import List, Dict, Any
from sqlalchemy import create_engine, text, func, select, or_, case, delete, inspect
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from data.core.config import config
from data.storage.models import Base, Property, PropertyImage, Agent, PriceHistory

logger = logging.getLogger(__name__)

def _create_db_engine():
    db_url = config.DATABASE_URL
    if db_url.startswith("sqlite"):
        return create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True
        )
    # Lazy connection: importing the CLI must not connect, and a failed PG write
    # must never silently land in another database. SQLite requires an explicit URL.
    return create_engine(
        db_url,
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )

engine = _create_db_engine()
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

def _get_insert():
    if engine.dialect.name == "sqlite":
        return sqlite_insert
    return pg_insert

def init_db():
    """Add missing nullable property columns and indexes; do not alter existing data/types."""
    with engine.begin() as conn:
        if inspect(conn).has_table(Property.__tablename__):
            existing = {col["name"] for col in inspect(conn).get_columns(Property.__tablename__)}
            required = {col.name for col in Property.__table__.columns if not col.nullable}
            if missing := required - existing:
                raise ValueError(f"properties missing required columns: {sorted(missing)}")
            for col in Property.__table__.columns:
                if col.name not in existing:
                    sql_type = col.type.compile(dialect=conn.dialect)
                    conn.execute(text(f'ALTER TABLE properties ADD COLUMN "{col.name}" {sql_type}'))
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "postgresql":
        for index in Base.metadata.tables["properties"].indexes:
            index.create(bind=engine, checkfirst=True)
    logger.info("Database initialized successfully.")

def _columns(model, row):
    return {k: v for k, v in row.items() if k in model.__table__.columns}

_DETAIL_FIELDS = frozenset((
    "postal_code", "street_name", "street_number", "block", "unit", "floor_level",
    "latitude", "longitude", "agent_id", "agent_name", "agent_avatar",
    "agent_license", "agent_years_with_pg", "agent_years_count", "agent_phone",
    "agent_profile_url", "agency_name", "project_id", "detail_images", "price_insights",
))

def _properties(db, items):
    dedup = {}
    for item in items:
        row = _columns(Property, item)
        if not row.get("listing_id") or not row.get("listing_type"):
            raise ValueError("property requires listing_id and listing_type")
        dedup[row["listing_id"]] = row
    insert_fn = _get_insert()
    for row in dedup.values():
        stmt = insert_fn(Property).values(row)
        updates = {}
        for key in row:
            if key in ("listing_id", "created_at", "updated_at"):
                continue
            if key == "detail_fetched":
                updates[key] = or_(Property.detail_fetched.is_(True), stmt.excluded.detail_fetched.is_(True))
            else:
                value = func.coalesce(stmt.excluded[key], Property.__table__.c[key])
                if key in _DETAIL_FIELDS:
                    value = case(
                        (Property.detail_fetched.is_(True) & stmt.excluded.detail_fetched.is_not(True),
                         Property.__table__.c[key]),
                        else_=value,
                    )
                updates[key] = value
        updates["updated_at"] = func.now()
        db.execute(stmt.on_conflict_do_update(index_elements=[Property.listing_id], set_=updates))
    return len(dedup)

def _images(db, records):
    dedup = {}
    for record in records:
        row = _columns(PropertyImage, record)
        key = (row["listing_id"], row["source_page"], row["image_url"])
        dedup[key] = row
    count = 0
    insert_fn = _get_insert()
    for row in dedup.values():
        if engine.dialect.name == "sqlite":
            stmt = insert_fn(PropertyImage).values(row).on_conflict_do_nothing(index_elements=["listing_id", "source_page", "image_url"])
        else:
            stmt = insert_fn(PropertyImage).values(row).on_conflict_do_nothing(constraint="uq_listing_source_image")
        count += db.execute(stmt).rowcount
    return count

def _replace_detail_images(db, properties, images):
    """Prune obsolete detail URLs only after a successful detail image fetch."""
    latest = {row["listing_id"]: row for row in properties}
    ids = {lid for lid, row in latest.items()
           if row.get("detail_fetched") is True and row.get("detail_images") is not None}
    for lid in ids:
        urls = {row["image_url"] for row in images
                if row.get("listing_id") == lid and row.get("source_page") == "DETAIL_PAGE"}
        condition = [PropertyImage.listing_id == lid, PropertyImage.source_page == "DETAIL_PAGE"]
        if urls:
            condition.append(PropertyImage.image_url.not_in(urls))
        db.execute(delete(PropertyImage).where(*condition))

def _agents(db, records):
    dedup = {}
    for record in records:
        row = _columns(Agent, record)
        if row.get("agent_id"):
            dedup[row["agent_id"]] = row
    insert_fn = _get_insert()
    for row in dedup.values():
        stmt = insert_fn(Agent).values(row)
        updates = {key: func.coalesce(stmt.excluded[key], Agent.__table__.c[key])
                   for key in row if key not in ("agent_id", "created_at", "updated_at")}
        updates["updated_at"] = func.now()
        db.execute(stmt.on_conflict_do_update(index_elements=[Agent.agent_id], set_=updates))
    return len(dedup)

_TX_KEY = ("project_id", "contract_date", "building", "floor_level", "size_sqft", "price")

def _transactions(db, records):
    dedup = {}
    for record in records:
        row = _columns(PriceHistory, record)
        dedup[tuple(row.get(k) for k in _TX_KEY)] = row
    count = 0
    insert_fn = _get_insert()
    for key in sorted(dedup, key=repr):
        row = dedup[key]
        if engine.dialect.name == "postgresql":
            digest = hashlib.sha256(repr(key).encode()).digest()
            lock_id = int.from_bytes(digest[:8], "big", signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
        condition = [PriceHistory.__table__.c[k].is_not_distinct_from(value)
                     for k, value in zip(_TX_KEY, key)]
        if db.execute(select(PriceHistory.id).where(*condition).limit(1)).first():
            continue
        if engine.dialect.name == "sqlite":
            stmt = insert_fn(PriceHistory).values(row).on_conflict_do_nothing(index_elements=["project_id", "contract_date", "building", "floor_level", "size_sqft", "price"])
        else:
            stmt = insert_fn(PriceHistory).values(row).on_conflict_do_nothing(constraint="uq_price_history_record")
        count += db.execute(stmt).rowcount
    return count

def save_batch(properties, images=(), agents=(), transactions=()):
    """Persist one page atomically; no images/related rows if properties fail."""
    db = SessionLocal()
    try:
        counts = (
            _properties(db, properties),
            _agents(db, agents),
            _replace_detail_images(db, properties, images),
            _images(db, images),
            _transactions(db, transactions),
        )
        db.commit()
        return counts[0], counts[1], counts[3], counts[4]
    except Exception:
        db.rollback()
        logger.exception("Failed to persist PropertyGuru batch")
        raise
    finally:
        db.close()

def upsert_properties(items: List[Dict[str, Any]]) -> int:
    return save_batch(items)[0] if items else 0

def upsert_property_images(image_records: List[Dict[str, Any]]) -> int:
    return save_batch([], images=image_records)[2] if image_records else 0

def upsert_agents(agent_records: List[Dict[str, Any]]) -> int:
    return save_batch([], agents=agent_records)[1] if agent_records else 0

def upsert_price_history(tx_records: List[Dict[str, Any]]) -> int:
    return save_batch([], transactions=tx_records)[3] if tx_records else 0

def get_stats() -> Dict[str, Any]:
    """Retrieve detailed statistics from database."""
    db = SessionLocal()
    try:
        total_count = db.query(Property).count()
        sale_count = db.query(Property).filter(Property.listing_type == "SALE").count()
        rent_count = db.query(Property).filter(Property.listing_type == "RENT").count()
        with_postal_count = db.query(Property).filter(Property.postal_code.isnot(None)).count()
        with_coords_count = db.query(Property).filter(Property.latitude.isnot(None)).count()

        # Agents stats
        total_agents = db.query(Agent).count()
        agents_with_avatar = db.query(Agent).filter(Agent.avatar_url.isnot(None)).count()
        agents_with_license = db.query(Agent).filter(Agent.license.isnot(None)).count()
        agents_with_years = db.query(Agent).filter(Agent.years_with_pg.isnot(None)).count()

        # Price history stats
        total_tx = db.query(PriceHistory).count()
        tx_sales = db.query(PriceHistory).filter(PriceHistory.transaction_type == "sales").count()
        tx_rent = db.query(PriceHistory).filter(PriceHistory.transaction_type == "rent").count()

        # Images stats
        total_images = db.query(PropertyImage).count()
        img_stats_res = db.execute(text("""
            SELECT source_page, image_type, COUNT(*) as cnt
            FROM property_images
            GROUP BY source_page, image_type
            ORDER BY source_page, cnt DESC
        """)).fetchall()

        # Crawled districts summary
        target_districts_res = db.execute(text("""
            SELECT district_code, COUNT(*) as cnt,
                   SUM(CASE WHEN listing_type = 'SALE' THEN 1 ELSE 0 END) as sale_cnt,
                   SUM(CASE WHEN listing_type = 'RENT' THEN 1 ELSE 0 END) as rent_cnt,
                   ROUND(AVG(price), 0) as avg_price,
                   ROUND(AVG(psf), 2) as avg_psf
            FROM properties
            WHERE district_code IS NOT NULL
            GROUP BY district_code
            ORDER BY cnt DESC
        """)).fetchall()

        # Property types
        type_res = db.execute(text("""
            SELECT property_type, COUNT(*) as cnt, ROUND(AVG(price), 0) as avg_price, ROUND(AVG(psf), 2) as avg_psf
            FROM properties
            WHERE price IS NOT NULL
            GROUP BY property_type
            ORDER BY cnt DESC
            LIMIT 10
        """)).fetchall()

        return {
            "total_count": total_count,
            "sale_count": sale_count,
            "rent_count": rent_count,
            "with_postal_count": with_postal_count,
            "with_coords_count": with_coords_count,
            "total_agents": total_agents,
            "agents_with_avatar": agents_with_avatar,
            "agents_with_license": agents_with_license,
            "agents_with_years": agents_with_years,
            "total_price_history": total_tx,
            "tx_sales": tx_sales,
            "tx_rent": tx_rent,
            "total_images": total_images,
            "image_breakdown": [dict(row._mapping) for row in img_stats_res],
            "target_districts": [dict(row._mapping) for row in target_districts_res],
            "by_type": [dict(row._mapping) for row in type_res]
        }
    finally:
        db.close()
