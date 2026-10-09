"""Migration utility: transfers crawled listings, agents, images, and price history
from local SQLite (data/propertyguru.db) to PostgreSQL with batching and conflict resolution.
"""
import os
import sys
import json
import logging
from typing import Optional, Dict, Any, Tuple
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

# Ensure scraper package imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data.storage.models import Base, Property, PropertyImage, Agent, PriceHistory
from data.core.config import config
from data.core.paths import SQLITE_PATH

logger = logging.getLogger(__name__)

def _ensure_dict(val: Any) -> Any:
    """Safely convert stringified JSON to Python dictionary/list for PostgreSQL JSONB."""
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        val_str = val.strip()
        if (val_str.startswith("{") and val_str.endswith("}")) or (val_str.startswith("[") and val_str.endswith("]")):
            try:
                return json.loads(val_str)
            except Exception:
                return val
    return val

def check_postgres_connection(pg_url: str) -> Tuple[bool, Optional[str]]:
    """Test connection to PostgreSQL without crashing."""
    try:
        eng = create_engine(pg_url, connect_args={"connect_timeout": 3})
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True, None
    except Exception as e:
        return False, str(e)

def migrate_sqlite_to_target(
    source_sqlite_path: str = str(SQLITE_PATH),
    target_db_url: Optional[str] = None,
    batch_size: int = 500,
    progress_callback = None,
) -> Dict[str, Any]:
    """
    Stream rows from source SQLite database to target database (PostgreSQL or SQLite test target).
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if not target_db_url:
        target_db_url = config.DATABASE_URL

    if not os.path.exists(source_sqlite_path):
        raise FileNotFoundError(f"Source SQLite database not found at '{source_sqlite_path}'")

    from data.graph.source import open_graph_source
    src_engine = open_graph_source(sqlite_path=source_sqlite_path)
    tgt_engine = create_engine(target_db_url)

    is_pg = tgt_engine.dialect.name == "postgresql"
    insert_fn = pg_insert if is_pg else sqlite_insert

    # 1. Initialize schema in target DB
    Base.metadata.create_all(tgt_engine)

    SrcSession = sessionmaker(bind=src_engine)
    TgtSession = sessionmaker(bind=tgt_engine)

    stats = {
        "agents": {"src": 0, "migrated": 0},
        "properties": {"src": 0, "migrated": 0},
        "images": {"src": 0, "migrated": 0},
        "price_history": {"src": 0, "migrated": 0},
    }

    # Count source records
    with SrcSession() as src_db:
        stats["agents"]["src"] = src_db.scalar(select(func.count()).select_from(Agent)) or 0
        stats["properties"]["src"] = src_db.scalar(select(func.count()).select_from(Property)) or 0
        stats["images"]["src"] = src_db.scalar(select(func.count()).select_from(PropertyImage)) or 0
        stats["price_history"]["src"] = src_db.scalar(select(func.count()).select_from(PriceHistory)) or 0

    # 2. Migrate Agents
    with SrcSession() as src_db, TgtSession() as tgt_db:
        offset = 0
        while True:
            agents = src_db.execute(
                select(Agent).order_by(Agent.agent_id).offset(offset).limit(batch_size)
            ).scalars().all()
            if not agents:
                break
            for a in agents:
                row = {c.name: getattr(a, c.name) for c in Agent.__table__.columns}
                row.pop("created_at", None)
                row.pop("updated_at", None)
                stmt = insert_fn(Agent).values(row)
                updates = {c.name: func.coalesce(stmt.excluded[c.name], Agent.__table__.c[c.name])
                           for c in Agent.__table__.columns if c.name not in ("agent_id", "created_at", "updated_at")}
                updates["updated_at"] = func.now()
                tgt_db.execute(stmt.on_conflict_do_update(index_elements=[Agent.agent_id], set_=updates))
                stats["agents"]["migrated"] += 1
            tgt_db.commit()
            offset += len(agents)
            if progress_callback:
                progress_callback("agents", stats["agents"]["migrated"], stats["agents"]["src"])

    # 3. Migrate Properties
    with SrcSession() as src_db, TgtSession() as tgt_db:
        offset = 0
        while True:
            props = src_db.execute(
                select(Property).order_by(Property.listing_id).offset(offset).limit(batch_size)
            ).scalars().all()
            if not props:
                break
            for p in props:
                row = {c.name: getattr(p, c.name) for c in Property.__table__.columns}
                row.pop("created_at", None)
                row.pop("updated_at", None)
                # Ensure JSONB fields are parsed dicts
                for jf in ("homepage_images", "detail_images", "price_insights", "raw_json"):
                    row[jf] = _ensure_dict(row.get(jf))
                stmt = insert_fn(Property).values(row)
                updates = {c.name: stmt.excluded[c.name]
                           for c in Property.__table__.columns if c.name not in ("listing_id", "created_at", "updated_at")}
                updates["updated_at"] = func.now()
                tgt_db.execute(stmt.on_conflict_do_update(index_elements=[Property.listing_id], set_=updates))
                stats["properties"]["migrated"] += 1
            tgt_db.commit()
            offset += len(props)
            if progress_callback:
                progress_callback("properties", stats["properties"]["migrated"], stats["properties"]["src"])

    # 4. Migrate Property Images in batches
    img_batch_size = max(batch_size * 4, 2000)
    with SrcSession() as src_db, TgtSession() as tgt_db:
        offset = 0
        while True:
            images = src_db.execute(
                select(PropertyImage).order_by(PropertyImage.id).offset(offset).limit(img_batch_size)
            ).scalars().all()
            if not images:
                break
            rows = []
            for img in images:
                row = {c.name: getattr(img, c.name) for c in PropertyImage.__table__.columns if c.name != "id"}
                row.pop("created_at", None)
                rows.append(row)
            if rows:
                if is_pg:
                    stmt = insert_fn(PropertyImage).values(rows).on_conflict_do_nothing(constraint="uq_listing_source_image")
                else:
                    stmt = insert_fn(PropertyImage).values(rows).on_conflict_do_nothing(index_elements=["listing_id", "source_page", "image_url"])
                tgt_db.execute(stmt)
                tgt_db.commit()
                stats["images"]["migrated"] += len(rows)
            offset += len(images)
            if progress_callback:
                progress_callback("images", stats["images"]["migrated"], stats["images"]["src"])

    # 5. Migrate Price History
    with SrcSession() as src_db, TgtSession() as tgt_db:
        offset = 0
        while True:
            txs = src_db.execute(
                select(PriceHistory).order_by(PriceHistory.id).offset(offset).limit(batch_size)
            ).scalars().all()
            if not txs:
                break
            rows = []
            for tx in txs:
                row = {c.name: getattr(tx, c.name) for c in PriceHistory.__table__.columns if c.name != "id"}
                row.pop("created_at", None)
                row["raw_json"] = _ensure_dict(row.get("raw_json"))
                rows.append(row)
            if rows:
                if is_pg:
                    stmt = insert_fn(PriceHistory).values(rows).on_conflict_do_nothing(constraint="uq_price_history_record")
                else:
                    stmt = insert_fn(PriceHistory).values(rows).on_conflict_do_nothing(
                        index_elements=["project_id", "contract_date", "building", "floor_level", "size_sqft", "price"]
                    )
                tgt_db.execute(stmt)
                tgt_db.commit()
                stats["price_history"]["migrated"] += len(rows)
            offset += len(txs)
            if progress_callback:
                progress_callback("price_history", stats["price_history"]["migrated"], stats["price_history"]["src"])

    # 6. Verification counts from target DB
    with TgtSession() as tgt_db:
        tgt_agents = tgt_db.scalar(select(func.count()).select_from(Agent)) or 0
        tgt_props = tgt_db.scalar(select(func.count()).select_from(Property)) or 0
        tgt_images = tgt_db.scalar(select(func.count()).select_from(PropertyImage)) or 0
        tgt_tx = tgt_db.scalar(select(func.count()).select_from(PriceHistory)) or 0

    stats["target_counts"] = {
        "agents": tgt_agents,
        "properties": tgt_props,
        "images": tgt_images,
        "price_history": tgt_tx,
    }

    src_engine.dispose()
    tgt_engine.dispose()
    return stats
