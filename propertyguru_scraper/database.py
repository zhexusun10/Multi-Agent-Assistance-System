import logging
from typing import List, Dict, Any
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.dialects.postgresql import insert
from config import config
from models import Base, Property, PropertyImage, Agent, PriceHistory

logger = logging.getLogger(__name__)

engine = create_engine(
    config.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    pool_pre_ping=True
)

SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

def init_db():
    """Create all tables, missing columns, and indexes."""
    Base.metadata.create_all(bind=engine)
    
    with engine.connect() as conn:
        new_cols = [
            ("project_id", "BIGINT"),
            ("agent_avatar", "TEXT"),
            ("agent_license", "VARCHAR(50)"),
            ("agent_years_with_pg", "VARCHAR(50)"),
            ("agent_years_count", "INTEGER"),
            ("agent_phone", "VARCHAR(50)"),
            ("agent_profile_url", "TEXT"),
            ("price_insights", "JSONB"),
            ("transaction_category", "VARCHAR(50)")
        ]
        for col_name, col_type in new_cols:
            try:
                conn.execute(text(f"ALTER TABLE properties ADD COLUMN IF NOT EXISTS {col_name} {col_type};"))
                conn.commit()
            except Exception as e:
                logger.debug(f"Column {col_name} check/add: {e}")
                
    logger.info("Database initialized successfully with all tables and columns.")

def upsert_properties(items: List[Dict[str, Any]]) -> int:
    """Bulk upsert properties into PostgreSQL with strict column filtering."""
    if not items:
        return 0

    valid_cols = {c.name for c in Property.__table__.columns}
    clean_items = [{k: v for k, v in item.items() if k in valid_cols} for item in items]

    db = SessionLocal()
    try:
        stmt = insert(Property).values(clean_items)
        update_cols = {
            c.name: stmt.excluded[c.name]
            for c in Property.__table__.columns
            if c.name not in ("listing_id", "created_at")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[Property.listing_id],
            set_=update_cols
        )
        db.execute(stmt)
        db.commit()
        return len(clean_items)
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to upsert properties: {e}")
        raise
    finally:
        db.close()

def upsert_property_images(image_records: List[Dict[str, Any]]) -> int:
    """Bulk upsert image records into property_images table."""
    if not image_records:
        return 0

    valid_cols = {c.name for c in PropertyImage.__table__.columns}
    clean_records = [{k: v for k, v in r.items() if k in valid_cols} for r in image_records]

    db = SessionLocal()
    try:
        stmt = insert(PropertyImage).values(clean_records)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_listing_source_image"
        )
        result = db.execute(stmt)
        db.commit()
        return result.rowcount
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to upsert property images: {e}")
        return 0
    finally:
        db.close()

def upsert_agents(agent_records: List[Dict[str, Any]]) -> int:
    """Bulk upsert agent records into agents table."""
    if not agent_records:
        return 0

    valid_cols = {c.name for c in Agent.__table__.columns}
    clean_records = [{k: v for k, v in r.items() if k in valid_cols} for r in agent_records]
    dedup = {a["agent_id"]: a for a in clean_records if a.get("agent_id")}.values()
    if not dedup:
        return 0

    db = SessionLocal()
    try:
        stmt = insert(Agent).values(list(dedup))
        update_cols = {
            c.name: stmt.excluded[c.name]
            for c in Agent.__table__.columns
            if c.name not in ("agent_id", "created_at")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[Agent.agent_id],
            set_=update_cols
        )
        db.execute(stmt)
        db.commit()
        return len(dedup)
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to upsert agents: {e}")
        return 0
    finally:
        db.close()

def upsert_price_history(tx_records: List[Dict[str, Any]]) -> int:
    """Bulk upsert price history transaction records into price_history table."""
    if not tx_records:
        return 0

    valid_cols = {c.name for c in PriceHistory.__table__.columns}
    clean_records = [{k: v for k, v in r.items() if k in valid_cols} for r in tx_records]

    # Deduplicate in-memory by unique constraint keys to avoid PostgreSQL batch conflict error
    dedup = {}
    for r in clean_records:
        key = (
            r.get("project_id"),
            r.get("contract_date"),
            r.get("building"),
            r.get("floor_level"),
            r.get("size_sqft"),
            r.get("price")
        )
        dedup[key] = r
    clean_records = list(dedup.values())

    db = SessionLocal()
    try:
        stmt = insert(PriceHistory).values(clean_records)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_price_history_record"
        )
        result = db.execute(stmt)
        db.commit()
        return result.rowcount
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to upsert price history: {e}")
        return 0
    finally:
        db.close()

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

        # Target districts: D05, D21, D10, D03, D04
        target_districts_res = db.execute(text("""
            SELECT district_code, COUNT(*) as cnt,
                   SUM(CASE WHEN listing_type = 'SALE' THEN 1 ELSE 0 END) as sale_cnt,
                   SUM(CASE WHEN listing_type = 'RENT' THEN 1 ELSE 0 END) as rent_cnt,
                   ROUND(AVG(price), 0) as avg_price,
                   ROUND(AVG(psf), 2) as avg_psf
            FROM properties
            WHERE district_code IN ('D05', 'D21', 'D10', 'D03', 'D04')
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
