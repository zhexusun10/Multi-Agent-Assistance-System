"""No network requests; optional PostgreSQL test: PROPERTYGURU_TEST_DATABASE_URL."""
import argparse
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Config
from pipeline import IngestionPipeline, PipelineStats
import main
import database
from models import Property, PropertyImage, Agent, PriceHistory


class FakeScraper:
    def fetch_page(self, *args, **kwargs):
        card = {
            "id": 101, "typeCode": "SALE", "title": "Test condo",
            "price": 1000000, "url": "/listing/101", "thumbnail": "https://example.org/card.jpg",
            "agent": {"id": 30, "name": "Card Agent"},
            "property": {"id": 20},
        }
        return [card, dict(card)], {"totalPages": 1}

    def fetch_details_concurrent(self, urls, max_workers=5):
        return {urls[0]: {
            "listingDetail": {"location": {"address": {"postalCode": "123456"}},
                              "project": {"id": 20}},
            "contactAgentData": {"contactAgentCard": {"agentInfoProps": {
                "agent": {"id": 30, "name": "Detail Agent"}}}},
            "mediaGalleryData": {"media": {"images": {"items": [
                {"src": "https://example.org/detail.jpg"}]}}},
        }}

    def fetch_project_page(self, *args):
        return 'prefix [{"building":"Block A","contract_date":"2025-01-01","price":1000000}] suffix'


def test_config_never_reads_langgraph_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://langgraph@localhost/langgraph")
    monkeypatch.setenv("PGDATABASE", "langgraph")
    monkeypatch.delenv("PROPERTYGURU_DATABASE_URL", raising=False)
    monkeypatch.setenv("PROPERTYGURU_PGUSER", "scraper")
    monkeypatch.setenv("PROPERTYGURU_PGPASSWORD", "p@ss:/word")
    assert "propertyguru" in Config().DATABASE_URL
    assert "p%40ss%3A%2Fword" in Config().DATABASE_URL
    monkeypatch.setenv("PROPERTYGURU_DATABASE_URL", "postgresql://other/isolated")
    assert Config().DATABASE_URL == "postgresql://other/isolated"


def test_pipeline_deduplicates_and_reports_db_failure():
    with patch("pipeline.save_batch", side_effect=RuntimeError("db down")) as save:
        stats = IngestionPipeline(FakeScraper()).run_sync(max_pages=1, fetch_price_history=False)
    assert len(save.call_args.args[0]) == 1
    assert stats.total_errors == 1
    assert stats.total_upserted == 0
    assert stats.total_details_enriched == 1
    assert len(save.call_args.args[1]) == 2  # images + agents are passed only with properties


def test_cli_run_exits_nonzero_on_persistence_error(monkeypatch):
    class FailedPipeline:
        def run_sync(self, **kwargs):
            return PipelineStats(total_errors=1)

    monkeypatch.setattr(main, "IngestionPipeline", lambda: FailedPipeline())
    args = argparse.Namespace(
        verbose=False, skip_details=True, skip_price_history=True,
        districts="D05", postal_range=None, all_pages=False, pages=1,
        type="sale", start_page=1, concurrency=1,
    )
    with pytest.raises(SystemExit) as exc:
        main.handle_run(args)
    assert exc.value.code == 1


@pytest.fixture
def pg(monkeypatch):
    url = os.getenv("PROPERTYGURU_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set PROPERTYGURU_TEST_DATABASE_URL for real PostgreSQL integration")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import scoped_session, sessionmaker

    schema = "scraper_test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", scoped_session(sessionmaker(bind=engine)))
    try:
        database.init_db()  # empty schema
        database.init_db()  # repeatable
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_committed_ingestion_can_be_backfilled_into_graph_batches(pg):
    from graph import iter_listing_batches

    stats = IngestionPipeline(FakeScraper()).run_sync(max_pages=1, fetch_price_history=False)
    assert stats.total_errors == 0
    assert stats.total_upserted == 1
    batches = list(iter_listing_batches(pg, batch_size=1))
    assert len(batches) == 1
    assert len(batches[0]) == 1
    row = batches[0][0]
    assert row["listing_id"] == 101
    assert row["props"]["title"] == "Test condo"
    assert row["project_id"] == 20
    assert row["agent_id"] == 30
    assert row["agent_name"] == "Detail Agent"
    assert "agent_phone" not in row["props"]


def test_repeat_run_enrichment_related_rows_and_rollback(pg, monkeypatch):
    from sqlalchemy import select, func, event
    scraper = FakeScraper()
    pipeline = IngestionPipeline(scraper)
    first = pipeline.run_sync(max_pages=1)
    assert first.total_errors == 0
    assert first.total_upserted == 1
    assert first.total_price_history_saved == 1
    replay = pipeline.run_sync(max_pages=1, fetch_details=False)
    assert replay.total_errors == 0
    with database.SessionLocal() as session:
        prop = session.get(Property, 101)
        assert prop.postal_code == "123456"
        assert prop.agent_name == "Detail Agent"
        assert prop.detail_fetched is True
        assert session.scalar(select(func.count()).select_from(Property)) == 1
        assert session.scalar(select(func.count()).select_from(PropertyImage)) == 2
        assert session.scalar(select(func.count()).select_from(Agent)) == 1
        # nullable transaction key fields are NULL: repeat must still dedup
        assert session.scalar(select(func.count()).select_from(PriceHistory)) == 1

    def fail_image(conn, cursor, statement, parameters, context, executemany):
        if "INSERT INTO property_images" in statement:
            raise RuntimeError("injected image failure")

    event.listen(pg, "before_cursor_execute", fail_image)
    try:
        with pytest.raises(RuntimeError, match="injected image failure"):
            database.save_batch([{"listing_id": 102, "listing_type": "RENT"}], [
                {"listing_id": 102, "source_page": "HOMEPAGE", "image_type": "PREVIEW",
                 "image_url": "https://example.org/new.jpg"}])
    finally:
        event.remove(pg, "before_cursor_execute", fail_image)
    with pytest.raises(ValueError, match="listing_id"):
        database.save_batch([{"listing_type": "RENT"}], [
            {"listing_id": 103, "source_page": "HOMEPAGE", "image_type": "PREVIEW",
             "image_url": "https://example.org/orphan.jpg"}])
    with database.SessionLocal() as session:
        assert session.get(Property, 102) is None
        assert session.scalar(select(func.count()).select_from(PropertyImage)) == 2
