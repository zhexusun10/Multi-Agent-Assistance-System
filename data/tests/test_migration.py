import os
import sys
import pytest
from pathlib import Path
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from data.storage.migration import _ensure_dict, check_postgres_connection, migrate_sqlite_to_target
from data.storage.models import Base, Property, Agent, PropertyImage

def test_ensure_dict():
    assert _ensure_dict(None) is None
    assert _ensure_dict({"a": 1}) == {"a": 1}
    assert _ensure_dict('{"b": 2}') == {"b": 2}
    assert _ensure_dict('[1, 2, 3]') == [1, 2, 3]
    assert _ensure_dict("not_json") == "not_json"

def test_check_postgres_connection_unreachable():
    ok, err = check_postgres_connection("postgresql+psycopg2://user:pass@127.0.0.1:59999/nodb")
    assert ok is False
    assert err is not None

def test_migrate_sqlite_to_target(tmp_path):
    # Create a small source SQLite database
    src_db_file = tmp_path / "src.db"
    tgt_db_file = tmp_path / "tgt.db"
    src_url = f"sqlite:///{src_db_file}"
    tgt_url = f"sqlite:///{tgt_db_file}"

    src_engine = create_engine(src_url)
    Base.metadata.create_all(src_engine)
    SrcSession = sessionmaker(bind=src_engine)

    with SrcSession() as s:
        agent = Agent(agent_id=1, name="Test Agent", agency_name="Agency A")
        prop = Property(
            listing_id=101,
            listing_type="RENT",
            title="Condo Unit",
            property_type="Condominium",
            price=3500,
            bedrooms=2,
            bathrooms=2,
            district_code="D05",
            homepage_images={"thumbnail": "http://example.com/thumb.jpg"}
        )
        img = PropertyImage(
            listing_id=101,
            source_page="HOMEPAGE",
            image_type="THUMBNAIL",
            image_url="http://example.com/thumb.jpg"
        )
        s.add_all([agent, prop, img])
        s.commit()
    src_engine.dispose()

    # Perform migration
    stats = migrate_sqlite_to_target(
        source_sqlite_path=str(src_db_file),
        target_db_url=tgt_url,
        batch_size=10
    )

    assert stats["agents"]["migrated"] == 1
    assert stats["properties"]["migrated"] == 1
    assert stats["images"]["migrated"] == 1
    assert stats["target_counts"]["properties"] == 1
    assert stats["target_counts"]["agents"] == 1
    assert stats["target_counts"]["images"] == 1
