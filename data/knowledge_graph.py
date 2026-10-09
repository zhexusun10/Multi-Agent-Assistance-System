import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
"""Import and explore PropertyGuru graphs without running the scraper or Agents.

Run from the project root: python data/knowledge_graph.py --help
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from data.core.paths import PLACE_SNAPSHOT, RUNTIME_DIR, SQLITE_PATH
from data.graph.sync import create_graph_driver, sync_projection
from data.graph.source import load_graph_env, open_api_source, open_graph_source
from data.storage.models import Property
from data.storage.snapshot import assert_snapshot, prepare_postgres_snapshot, snapshot_info, sql_counts


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def nonnegative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def bounded_radius(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("radius must be a number")
    if not math.isfinite(number) or not 1 <= number <= 10000:
        raise argparse.ArgumentTypeError("radius must be 1..10000 meters")
    return number


def import_graph(args):
    from data.service.app import graph_stats
    engine = open_graph_source(sqlite_path=args.sqlite, database_url=args.database_url)
    driver = None
    started = time.monotonic()
    try:
        with engine.connect() as connection:
            total = connection.scalar(select(func.count()).select_from(Property))
        source = (str(Path(args.sqlite).resolve()) if args.sqlite
                  else engine.url.render_as_string(hide_password=True))
        target = min(total, args.limit) if args.limit is not None else total
        print(f"Source: {source}\nListings: {total:,}; this import: {target:,}", flush=True)
        if args.limit is None:
            assert_snapshot(sql_counts(engine))
        driver = create_graph_driver()
        database = os.getenv("NEO4J_DATABASE", "neo4j")
        result = sync_projection(
            engine, driver=driver, database=database, limit=args.limit, batch_size=args.batch_size,
            progress_callback=lambda n: print(f"Committed {n:,}/{target:,} listings", flush=True),
            include_places=not args.skip_places, radius_m=args.place_radius_m,
        )
        count, enrichment = result["listings"], result["place_enrichment"]
        report = {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "snapshot": snapshot_info(), "snapshot_counts_validated": args.limit is None,
            "source": source, "source_read_only": True,
            "source_listings": total, "imported_listings": count,
            "limit": args.limit, "batch_size": args.batch_size, "projection_version": 2,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "graph": graph_stats(driver, database), "place_enrichment": enrichment,
            "scope": "listings, dimensions and named public POIs; no images, raw JSON, phones, licenses or price history",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.report:
            destination = Path(args.report)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Report: {destination.resolve()}")
    finally:
        engine.dispose()
        if driver is not None:
            driver.close()


def main(argv=None):
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    load_graph_env()
    parser = argparse.ArgumentParser(description="PropertyGuru → Neo4j knowledge graph & visualization")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-postgres", help="Import the fixed snapshot into an empty PG database; reuse a prepared one")
    prepare.add_argument("--sqlite-path", default=str(SQLITE_PATH))
    prepare.add_argument("--batch-size", type=positive_int, default=500)
    verifier = commands.add_parser("verify", help="Read-only SQL/Neo4j consistency and optional full HTTP acceptance checks")
    verify_sources = verifier.add_mutually_exclusive_group()
    verify_sources.add_argument("--sqlite", help="Explicit canonical SQLite source (otherwise use the API's PG source)")
    verify_sources.add_argument("--database-url", help="Dedicated canonical PG source")
    verifier.add_argument("--api-url", help="Also verify a running HTTP API, e.g. http://127.0.0.1:8088")
    verifier.add_argument("--report", default=str(RUNTIME_DIR / "verification-report.json"))
    importer = commands.add_parser("import", help="Rebuild Neo4j from the fixed SQL snapshot, including public places")
    sources = importer.add_mutually_exclusive_group()
    sources.add_argument("--sqlite", help="Existing SQLite database, opened read-only (e.g. data/propertyguru.db)")
    sources.add_argument("--database-url", help="Dedicated PostgreSQL URL (or PROPERTYGURU_DATABASE_URL)")
    importer.add_argument("--limit", type=nonnegative_int, help="Smoke test only; omit for full import")
    importer.add_argument("--batch-size", type=positive_int, default=500)
    importer.add_argument("--report", default=str(RUNTIME_DIR / "import-report.json"), help="JSON verification report path")
    importer.add_argument("--skip-places", action="store_true", help="Skip POI refresh (existing distance edges may be stale)")
    importer.add_argument("--place-radius-m", type=bounded_radius, default=1500, help="Persist straight-line POI edges within this radius (1..10000 m)")
    enrich = commands.add_parser("enrich-places", help="Fetch/cache public Singapore POIs and rebuild their relationships")
    enrich.add_argument("--refresh", action="store_true", help="Fetch a new snapshot; Overpass with BBBike PBF fallback")
    enrich.add_argument("--snapshot", default=str(PLACE_SNAPSHOT))
    enrich.add_argument("--pbf", help="Explicit existing OSM extract (filtered by Singapore country polygon)")
    enrich.add_argument("--radius-m", type=bounded_radius, default=1500)
    enrich.add_argument("--batch-size", type=positive_int, default=500)
    commands.add_parser("stats", help="Print actual Neo4j node, edge and listing counts as JSON")
    checker = commands.add_parser("check", help="Check Bolt authentication and database readiness")
    checker.add_argument("--quiet", action="store_true")
    browser = commands.add_parser("browser", help="Open the official Neo4j Browser with connection/query prefilled")
    browser.add_argument("--print-url", action="store_true", help="Print the safe deep link without opening a browser")
    browser.add_argument("--auto-connect", action="store_true", help="Use optional Chrome automation for local no-auth connection")
    server = commands.add_parser("serve", help="Start the independent, read-only HTTP data API")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8088)
    sql_sources = server.add_mutually_exclusive_group()
    sql_sources.add_argument("--sqlite", help="Explicit read-only SQL source for canonical detail APIs; no automatic fallback")
    sql_sources.add_argument("--database-url", help="Dedicated PostgreSQL URL; default PROPERTYGURU_DATABASE_URL")
    args = parser.parse_args(argv)
    if args.command == "serve":
        import uvicorn
        from data.service.app import create_app
        from data.graph.sync import graph_auth
        if graph_auth() is None and args.host not in ("127.0.0.1", "localhost", "::1"):
            parser.error("No-auth Neo4j requires a loopback data API host")
        uvicorn.run(create_app(sqlite_path=args.sqlite, database_url=args.database_url), host=args.host, port=args.port)
        return
    try:
        if args.command == "prepare-postgres":
            engine = open_graph_source()
            try:
                report = prepare_postgres_snapshot(engine, sqlite_path=args.sqlite_path, batch_size=args.batch_size)
                print(json.dumps(report, ensure_ascii=False, indent=2))
            finally:
                engine.dispose()
        elif args.command == "verify":
            from data.service.validation import verify_data
            from urllib.parse import urlsplit
            if args.api_url:
                url = urlsplit(args.api_url)
                if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
                    raise ValueError("--api-url must be an HTTP(S) base URL without credentials, query or fragment")
            engine = open_api_source(sqlite_path=args.sqlite, database_url=args.database_url)
            try:
                with create_graph_driver() as driver:
                    report = verify_data(engine, driver, os.getenv("NEO4J_DATABASE", "neo4j"), api_url=args.api_url)
                destination = Path(args.report)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(report, ensure_ascii=False, indent=2))
                print(f"Report: {destination.resolve()}")
            finally:
                engine.dispose()
        elif args.command == "import":
            import_graph(args)
        elif args.command == "enrich-places":
            from data.graph.places import acquire_places, sync_places
            rows, metadata = acquire_places(output=args.snapshot, pbf=args.pbf, refresh=args.refresh)
            with create_graph_driver() as driver:
                driver.verify_connectivity()
                report = sync_places(driver, rows, metadata, radius_m=args.radius_m, batch_size=args.batch_size)
            print(json.dumps({"metadata": metadata, "projection": report}, ensure_ascii=False, indent=2))
        elif args.command == "browser":
            from data.graph.browser import browser_url, open_browser
            if args.print_url:
                print(browser_url())
            else:
                open_browser(auto_connect=args.auto_connect)
        elif args.command == "check":
            from data.service.app import read_query
            with create_graph_driver() as driver:
                read_query(driver, os.getenv("NEO4J_DATABASE", "neo4j"), "RETURN 1 AS ok")
            if not args.quiet:
                print("Neo4j is ready.")
        elif args.command == "stats":
            from data.service.app import graph_stats
            with create_graph_driver() as driver:
                driver.verify_connectivity()
                print(json.dumps(graph_stats(driver, os.getenv("NEO4J_DATABASE", "neo4j")),
                                 ensure_ascii=False, indent=2))
    except Exception as exc:
        if not getattr(args, "quiet", False):
            # Database exceptions can contain SQL parameters/private source rows.
            from sqlalchemy.exc import SQLAlchemyError
            message = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) and not isinstance(exc, SQLAlchemyError) else type(exc).__name__
            if args.command == "verify":
                # A failed new run must not leave an old success report looking current.
                try:
                    destination = Path(args.report)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(json.dumps({"status": "failed", "read_only": True,
                        "verified_at": datetime.now(timezone.utc).isoformat(), "error": message},
                        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                except OSError:
                    pass
            print(f"Data operation failed: {message}\nCheck configuration/source and rerun. No automatic fallback or deletion.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
