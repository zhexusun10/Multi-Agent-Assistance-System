"""Load the checked-in rental CSV into empty local test databases.

This is a partial dataset, never the full fixed snapshot. No scraping or downloads.
Repeat imports of the identical files are safe; unrelated contents are refused.
"""

import argparse
import csv
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import Column, Integer, MetaData, String, Table, func, inspect, select
from .core.paths import EXPORT_DIR, PLACE_SNAPSHOT, RUNTIME_DIR
from .graph.source import load_graph_env, open_graph_source
from .graph.sync import create_graph_driver, sync_graph
from .graph.places import load_snapshot, sync_places
from .storage.models import Base, Property

MARKER_ID = "recommendation_csv_sample"
marker = Table(
    "recommendation_sample_import",
    MetaData(),
    Column("id", Integer, primary_key=True),
    Column("source_hash", String(64), nullable=False),
)
NUMERIC = ("price", "psf", "floor_area_sqft", "latitude", "longitude")
INTEGER = ("bedrooms", "bathrooms")
TEXT = (
    "title",
    "property_type",
    "postal_code",
    "street_name",
    "street_number",
    "district_code",
    "url",
    "thumbnail_url",
)


def read_listings(path):
    records = {}
    warnings = []
    with Path(path).open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not {"listing_id", "listing_type", "price", "bedrooms"}.issubset(
            reader.fieldnames or []
        ):
            raise ValueError("Rental CSV is missing required columns")
        for row in reader:
            listing_id = int(row["listing_id"])
            if listing_id <= 0 or row["listing_type"].strip().upper() != "RENT":
                raise ValueError(
                    "Sample import accepts positive rental listing IDs only"
                )
            record = {
                "listing_id": listing_id,
                "listing_type": "RENT",
                "currency": "SGD",
                "updated_at": None,
            }
            record.update({key: row.get(key, "").strip() or None for key in TEXT})
            for key in NUMERIC + INTEGER:
                raw = row.get(key, "").strip()
                value = None
                if raw:
                    try:
                        value = float(raw)
                        if not math.isfinite(value) or (
                            key not in ("latitude", "longitude") and value < 0
                        ):
                            raise ValueError()
                        if key in INTEGER:
                            if not value.is_integer():
                                raise ValueError()
                            value = int(value)
                        if key == "latitude" and abs(value) > 90:
                            raise ValueError()
                        if key == "longitude" and abs(value) > 180:
                            raise ValueError()
                    except ValueError:
                        warnings.append(
                            {
                                "listing_id": str(listing_id),
                                "field": key,
                                "reason": "invalid numeric source value retained as unknown",
                            }
                        )
                        value = None
                record[key] = value
            record["posted_at"] = None
            if row.get("posted_at"):
                record["posted_at"] = datetime.fromisoformat(
                    row["posted_at"].replace("Z", "+00:00")
                )
            record["raw_json"] = {
                "dataset_scope": MARKER_ID,
                "normalization_warnings": [
                    w for w in warnings if w["listing_id"] == str(listing_id)
                ],
            }
            if listing_id in records and records[listing_id] != record:
                raise ValueError("Conflicting duplicate listing ID")
            records[listing_id] = record
    if not records:
        raise ValueError("Refusing an empty sample import")
    return sorted(records.values(), key=lambda row: row["listing_id"]), warnings


def check_sql(connection, fingerprint, expected_count):
    tables = set(inspect(connection).get_table_names())
    if marker.name in tables:
        existing = connection.execute(
            select(marker.c.source_hash).where(marker.c.id == 1)
        ).scalar_one_or_none()
        if existing != fingerprint:
            raise ValueError("SQL sample marker differs from these source files")
        count = connection.scalar(select(func.count()).select_from(Property))
        if count != expected_count:
            raise ValueError("SQL sample count changed; refusing to overwrite it")
        return True
    for table in Base.metadata.sorted_tables:
        if table.name in tables and connection.scalar(
            select(func.count()).select_from(table)
        ):
            raise ValueError(
                "SQL database already contains unmarked data; sample import refused"
            )
    if tables - set(Base.metadata.tables):
        raise ValueError(
            "SQL database contains unrelated tables; sample import refused"
        )
    return False


def check_graph(driver, database, fingerprint):
    with driver.session(database=database, default_access_mode="READ") as session:
        count = session.run("MATCH (n) RETURN count(n) AS count").single()["count"]
        row = session.run(
            "MATCH (d:Dataset {dataset_id:$id}) RETURN d.source_hash AS hash",
            id=MARKER_ID,
        ).single()
        if count and (not row or row["hash"] != fingerprint):
            raise ValueError(
                "Neo4j contains unmarked or different data; sample import refused"
            )
        return bool(count)


def import_sample(engine, driver, database, records, places, metadata, fingerprint):
    # Check BOTH stores before any writes. SQL commit is atomic; graph batches may be
    # resumed with the identical fingerprint after interruption.
    with engine.connect() as connection:
        already_loaded = check_sql(connection, fingerprint, len(records))
    check_graph(driver, database, fingerprint)
    if not already_loaded:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            marker.create(connection, checkfirst=True)
            connection.execute(Property.__table__.insert(), records)
            connection.execute(marker.insert(), {"id": 1, "source_hash": fingerprint})
    with driver.session(database=database) as session:
        session.run(
            "MERGE (d:Dataset {dataset_id:$id}) SET d.source_hash=$hash, d.scope=$scope",
            id=MARKER_ID,
            hash=fingerprint,
            scope="partial rental CSV; not the full snapshot",
        ).consume()
    count = sync_graph(engine, driver=driver, database=database)
    enrichment = sync_places(driver, places, metadata, database=database)
    with driver.session(database=database, default_access_mode="READ") as session:
        graph_ids = {
            str(row["id"])
            for row in session.run("MATCH (l:Listing) RETURN l.listing_id AS id")
        }
    if graph_ids != {str(row["listing_id"]) for row in records}:
        raise ValueError("Graph listing IDs do not match the CSV")
    return {
        "sql_already_loaded": already_loaded,
        "sql_listings": len(records),
        "graph_listings": count,
        "listing_ids_match": True,
        "places": enrichment,
    }


def main():
    load_graph_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv", type=Path, default=EXPORT_DIR / "export_properties_rent.csv"
    )
    parser.add_argument("--places", type=Path, default=PLACE_SNAPSHOT)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Load into guarded empty/identically marked local databases",
    )
    parser.add_argument(
        "--report", type=Path, default=RUNTIME_DIR / "csv-sample-import.json"
    )
    args = parser.parse_args()
    records, warnings = read_listings(args.csv)
    places, metadata = load_snapshot(args.places)
    fingerprint = hashlib.sha256(
        args.csv.read_bytes() + b"\0" + args.places.read_bytes()
    ).hexdigest()
    report = {
        "dataset_scope": "partial_csv_sample",
        "full_snapshot": False,
        "source_hash": fingerprint,
        "rental_rows": len(records),
        "place_rows": len(places),
        "warnings": warnings,
        "applied": False,
    }
    if args.apply:
        engine = open_graph_source()
        driver = None
        try:
            if engine.url.host not in (
                "localhost",
                "127.0.0.1",
                "::1",
            ) or engine.url.database not in ("propertyguru", "propertyguru_sample"):
                raise ValueError(
                    "Sample import is limited to local propertyguru/propertyguru_sample databases"
                )
            if urlsplit(os.getenv("NEO4J_URI", "")).hostname not in (
                "localhost",
                "127.0.0.1",
                "::1",
            ):
                raise ValueError("Sample import requires a local Neo4j instance")
            driver = create_graph_driver()
            report["import"] = import_sample(
                engine,
                driver,
                os.getenv("NEO4J_DATABASE", "neo4j"),
                records,
                places,
                metadata,
                fingerprint,
            )
            report["applied"] = True
        finally:
            engine.dispose()
            if driver:
                driver.close()
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
