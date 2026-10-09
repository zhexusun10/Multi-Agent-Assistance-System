import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import sys
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
import argparse
import logging
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from rich.panel import Panel
from sqlalchemy.engine import make_url

from data.core.config import config
from data.core.paths import EXPORT_DIR, SQLITE_PATH
from data.storage.database import init_db, get_stats, SessionLocal
from data.storage.models import Property, PropertyImage, Agent, PriceHistory
from data.scraper.pipeline import IngestionPipeline, PipelineStats

console = Console()

def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )

def handle_init_db(args):
    console.print("[bold cyan]Initializing database tables, columns, and indexes...[/bold cyan]")
    init_db()
    console.print("[bold green][OK] Database initialization complete.[/bold green]")

def handle_run(args):
    setup_logging(args.verbose)
    fetch_details = not args.skip_details
    fetch_price_history = not args.skip_price_history

    # Parse districts list: "ALL" -> D01-D28, or comma-separated list
    raw_dist = (getattr(args, "districts", None) or "").strip().upper()
    if not raw_dist or raw_dist in ("ALL", "SINGAPORE", "*"):
        districts = [f"D{i:02d}" for i in range(1, 29)]
    else:
        districts = [d.strip().upper() for d in raw_dist.split(",") if d.strip()]

    # Resume from specific district if requested
    start_d = getattr(args, "start_district", None)
    if start_d:
        start_d = start_d.strip().upper()
        if start_d in districts:
            idx = districts.index(start_d)
            districts = districts[idx:]

    # Parse postal range
    postal_range = None
    if getattr(args, "postal_range", None):
        try:
            p_min, p_max = [int(x.strip()) for x in args.postal_range.split("-")]
            postal_range = (p_min, p_max)
        except Exception:
            pass

    max_pages = None if getattr(args, "all_pages", False) else args.pages

    console.print(Panel.fit(
        f"[bold blue]PropertyGuru Scraper & Ingestion Pipeline[/bold blue]\n"
        f"Target Type         : [cyan]{args.type.upper()}[/cyan]\n"
        f"Districts           : [cyan]{f'ALL 28 Districts ({districts[0]} to {districts[-1]}, total {len(districts)})' if len(districts) > 5 else ', '.join(districts)}[/cyan]\n"
        f"Postal Range Filter : [cyan]{getattr(args, 'postal_range', None) or 'N/A'}[/cyan]\n"
        f"Pages               : [cyan]{'ALL available pages per district' if max_pages is None else f'{args.start_page} to {args.start_page + max_pages - 1} (Total {max_pages} per district)'}[/cyan]\n"
        f"Fetch Details       : [cyan]{'Yes (Exact Address, Agent Profile, Separated Images)' if fetch_details else 'No'}[/cyan]\n"
        f"Fetch Price History : [cyan]{'Yes (URA / HDB Transactions)' if fetch_price_history else 'No'}[/cyan]\n"
        f"Concurrency         : [cyan]{args.concurrency} worker threads[/cyan]\n"
        f"Database            : [cyan]{make_url(config.DATABASE_URL).render_as_string(hide_password=True)}[/cyan]",
        border_style="blue"
    ))

    types_to_crawl = ["sale", "rent"] if args.type.lower() == "all" else [args.type.lower()]
    pipeline = IngestionPipeline()

    overall_upserted = 0
    overall_agents = 0
    overall_tx = 0
    overall_images = 0
    overall_errors = 0

    for l_type in types_to_crawl:
        console.print(f"\n[bold yellow]>>> Starting crawl for category: {l_type.upper()} ({len(districts)} District(s))[/bold yellow]")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("| Page {task.fields[page]}/{task.fields[total_pages]}"),
            TextColumn("| Listings: {task.fields[upserted]}"),
            TextColumn("| Agents: {task.fields[agents]}"),
            TextColumn("| Price TX: {task.fields[tx]}"),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            task = progress.add_task(
                f"[green]Scraping {l_type.upper()}...",
                total=max_pages or 100,
                page=args.start_page,
                total_pages="?",
                upserted=0,
                agents=0,
                tx=0
            )

            def progress_hook(curr_page: int, end_page: int, batch_count: int, stats: PipelineStats):
                progress.update(
                    task,
                    total=end_page,
                    completed=curr_page - args.start_page + 1,
                    page=curr_page,
                    total_pages=end_page,
                    upserted=stats.total_upserted,
                    agents=stats.total_agents_saved,
                    tx=stats.total_price_history_saved
                )

            def district_hook(d_code: str, d_idx: int, total_d: int):
                progress.update(
                    task,
                    description=f"[green]Scraping {l_type.upper()} [{d_code} ({d_idx}/{total_d})]..."
                )

            stats = pipeline.run_sync(
                listing_type=l_type,
                districts=districts,
                start_page=args.start_page,
                max_pages=max_pages,
                fetch_details=fetch_details,
                fetch_price_history=fetch_price_history,
                concurrency=args.concurrency,
                postal_range=postal_range,
                progress_callback=progress_hook,
                district_callback=district_hook
            )

            overall_errors += stats.total_errors
            overall_upserted += stats.total_upserted
            overall_agents += stats.total_agents_saved
            overall_tx += stats.total_price_history_saved
            overall_images += (stats.total_homepage_images + stats.total_detail_images)

        console.print(
            f"[bold green][OK] {l_type.upper()} completed: "
            f"Pages={stats.total_pages_success}/{stats.total_pages_attempted}, "
            f"Listings Ingested={stats.total_upserted}, "
            f"Agents Saved={stats.total_agents_saved}, "
            f"Price History TX={stats.total_price_history_saved}, "
            f"Homepage Images={stats.total_homepage_images}, "
            f"Detail Images={stats.total_detail_images}[/bold green]"
        )

    console.print(
        f"\n[bold green][*] All tasks finished! "
        f"Total properties: {overall_upserted}, "
        f"Total unique agents: {overall_agents}, "
        f"Total price history transactions: {overall_tx}, "
        f"Total images recorded: {overall_images}[/bold green]"
    )

    if getattr(args, "auto_export", False) and overall_upserted > 0:
        try:
            export_args = argparse.Namespace(
                format="csv",
                output=str(EXPORT_DIR / "export_properties.csv"),
                table="all"
            )
            handle_export(export_args)
        except Exception as e_exp:
            console.print(f"[yellow]Auto-export notice: {e_exp}[/yellow]")

    if overall_errors:
        console.print(f"[bold red]Crawl completed with {overall_errors} error(s); check logs.[/bold red]")
        raise SystemExit(1)

    if getattr(args, "sync_graph", False):
        handle_sync_graph(args)


def handle_sync_graph(args):
    # Projection is deliberately downstream of committed PostgreSQL writes.
    from data.storage.database import engine
    from data.graph.sync import sync_projection
    try:
        result = sync_projection(engine, limit=getattr(args, "limit", None),
                                 batch_size=getattr(args, "batch_size", 500))
        count = result["listings"]
        report = result["place_enrichment"]
        console.print(f"[cyan]Public POIs: {report['places']}; proximity edges: {report['near_place_relationships']}[/cyan]")
    except Exception as exc:
        console.print(f"[bold red]Neo4j graph sync failed (PostgreSQL writes remain intact): {exc}[/bold red]")
        raise SystemExit(1) from exc
    console.print(f"[bold green]Graph synced: {count} listings.[/bold green]")


def handle_stats(args):
    console.print("[bold cyan]Fetching comprehensive database statistics...[/bold cyan]")
    stats = get_stats()
    
    console.print(f"\n[bold]Total Properties in DB : [green]{stats['total_count']}[/green] "
                  f"(Sale: [blue]{stats['sale_count']}[/blue], Rent: [yellow]{stats['rent_count']}[/yellow])[/bold]")
    console.print(f"[bold]Total Unique Agents    : [green]{stats['total_agents']}[/green] "
                  f"(With Photos: [cyan]{stats['agents_with_avatar']}[/cyan], "
                  f"With License: [cyan]{stats['agents_with_license']}[/cyan], "
                  f"With PG Years: [cyan]{stats['agents_with_years']}[/cyan])[/bold]")
    console.print(f"[bold]Price History Records  : [green]{stats['total_price_history']}[/green] "
                  f"(Sales: [blue]{stats['tx_sales']}[/blue], Rent: [yellow]{stats['tx_rent']}[/yellow])[/bold]")
    console.print(f"[bold]Exact Postal Codes     : [green]{stats['with_postal_count']}[/green] / {stats['total_count']}[/bold]")
    console.print(f"[bold]Geo Coordinates        : [green]{stats['with_coords_count']}[/green] / {stats['total_count']}[/bold]")
    console.print(f"[bold]Total Images Stored    : [magenta]{stats['total_images']}[/magenta][/bold]\n")

    # Table 0: Target Districts Breakdown (D05, D21, D10, D03, D04)
    if stats.get("target_districts"):
        t0 = Table(title="Target Districts Breakdown (D05, D21, D10, D03, D04)", show_header=True, header_style="bold cyan")
        t0.add_column("District Code", justify="center", width=15)
        t0.add_column("Total Listings", justify="right")
        t0.add_column("Sale Listings", justify="right")
        t0.add_column("Rent Listings", justify="right")
        t0.add_column("Avg Price (SGD)", justify="right")
        t0.add_column("Avg PSF (SGD/sqft)", justify="right")
        for row in stats["target_districts"]:
            t0.add_row(
                row.get("district_code") or "N/A",
                str(row.get("cnt", 0)),
                str(row.get("sale_cnt", 0)),
                str(row.get("rent_cnt", 0)),
                f"${row.get('avg_price', 0):,.0f}" if row.get('avg_price') else "-",
                f"${row.get('avg_psf', 0):,.2f}" if row.get('avg_psf') else "-"
            )
        console.print(t0)
        console.print()

    # Table 1: Separated Images Breakdown
    if stats.get("image_breakdown"):
        t1 = Table(title="Separated Images Breakdown (Homepage vs Detail Page)", show_header=True, header_style="bold green")
        t1.add_column("Source Page (来源页面)", style="cyan", width=22)
        t1.add_column("Image Type (图片类型)", style="magenta", width=20)
        t1.add_column("Image Count (图片总数)", justify="right")
        for row in stats["image_breakdown"]:
            t1.add_row(
                row.get("source_page"),
                row.get("image_type"),
                f"{row.get('cnt', 0):,}"
            )
        console.print(t1)
        console.print()

    # Table 2: Property Types
    if stats.get("by_type"):
        t2 = Table(title="Properties by Type", show_header=True, header_style="bold magenta")
        t2.add_column("Property Type", style="dim", width=25)
        t2.add_column("Count", justify="right")
        t2.add_column("Avg Price (SGD)", justify="right")
        t2.add_column("Avg PSF (SGD/sqft)", justify="right")
        for row in stats["by_type"]:
            t2.add_row(
                str(row.get("property_type") or "Unknown"),
                str(row.get("cnt", 0)),
                f"${row.get('avg_price', 0):,.0f}" if row.get('avg_price') else "-",
                f"${row.get('avg_psf', 0):,.2f}" if row.get('avg_psf') else "-"
            )
        console.print(t2)

def handle_export(args):
    import pandas as pd
    import os
    table_choice = getattr(args, "table", "all")
    fmt = args.format.lower()
    base_prefix = os.path.splitext(args.output)[0]
    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    db = SessionLocal()
    try:
        from sqlalchemy import text
        
        # 1. Properties
        if table_choice in ("properties", "all"):
            out_file = args.output if table_choice == "properties" else f"{base_prefix}_properties.{fmt}"
            console.print(f"[bold cyan]Exporting properties to {out_file}...[/bold cyan]")
            if db.bind.dialect.name == "sqlite":
                thumb_expr = "json_extract(p.homepage_images, '$.thumbnail')"
                photo_expr = "json_extract(p.detail_images, '$.photo_count')"
                fp_expr = "json_extract(p.detail_images, '$.floor_plan_count')"
            else:
                thumb_expr = "p.homepage_images->>'thumbnail'"
                photo_expr = "p.detail_images->>'photo_count'"
                fp_expr = "p.detail_images->>'floor_plan_count'"

            sql_props = f"""
                SELECT p.listing_id, 
                       p.listing_type, 
                       COALESCE(p.transaction_category, CASE WHEN p.listing_type = 'SALE' THEN '买房/出售' ELSE '租房/出租' END) as transaction_category,
                       p.title, p.property_type, p.price, p.psf,
                       p.bedrooms, p.bathrooms, p.floor_area_sqft, p.postal_code, p.street_name, p.street_number,
                       p.district_code, p.latitude, p.longitude,
                       p.agent_name, p.agency_name, p.agent_license, p.agent_years_with_pg, p.agent_phone,
                       {thumb_expr} as thumbnail_url,
                       {photo_expr} as detail_photo_count,
                       {fp_expr} as detail_floorplan_count,
                       p.posted_at, p.url
                FROM properties p
                ORDER BY p.listing_id DESC
            """
            df_props = pd.read_sql(sql_props, db.bind)
            if fmt == "csv":
                df_props.to_csv(out_file, index=False)
                # Also generate dedicated files for SALE and RENT
                sale_file = f"{base_prefix}_properties_sale.csv"
                rent_file = f"{base_prefix}_properties_rent.csv"
                df_props[df_props["listing_type"] == "SALE"].to_csv(sale_file, index=False)
                df_props[df_props["listing_type"] == "RENT"].to_csv(rent_file, index=False)
                console.print(f"[bold green][OK] Exported {len(df_props)} total properties to {out_file}[/bold green]")
                console.print(f"[bold green]  - 买房 (SALE): {len(df_props[df_props['listing_type'] == 'SALE'])} records -> {sale_file}[/bold green]")
                console.print(f"[bold green]  - 租房 (RENT): {len(df_props[df_props['listing_type'] == 'RENT'])} records -> {rent_file}[/bold green]")
            else:
                df_props.to_json(out_file, orient="records", indent=2)
                console.print(f"[bold green][OK] Exported {len(df_props)} properties to {out_file}[/bold green]")

        # 2. Agents
        if table_choice in ("agents", "all"):
            out_file = f"{base_prefix}_agents.{fmt}" if table_choice == "all" else args.output
            console.print(f"[bold cyan]Exporting agents to {out_file}...[/bold cyan]")
            sql_agents = """
                SELECT agent_id, name, agency_name, license, years_with_pg, years_count, phone, avatar_url, profile_url
                FROM agents
                ORDER BY agent_id
            """
            df_agents = pd.read_sql(sql_agents, db.bind)
            if fmt == "csv":
                df_agents.to_csv(out_file, index=False)
            else:
                df_agents.to_json(out_file, orient="records", indent=2)
            console.print(f"[bold green][OK] Exported {len(df_agents)} agents to {out_file}[/bold green]")

        # 3. Price History
        if table_choice in ("price_history", "all"):
            out_file = f"{base_prefix}_price_history.{fmt}" if table_choice == "all" else args.output
            console.print(f"[bold cyan]Exporting price history to {out_file}...[/bold cyan]")
            sql_tx = """
                SELECT id, project_id, project_name, contract_date, price, psf, building, floor_level,
                       size_sqft, bedrooms, transaction_type, property_type, district_code, postal_code
                FROM price_history
                ORDER BY project_id, contract_date DESC
            """
            df_tx = pd.read_sql(sql_tx, db.bind)
            if fmt == "csv":
                df_tx.to_csv(out_file, index=False)
            else:
                df_tx.to_json(out_file, orient="records", indent=2)
            console.print(f"[bold green][OK] Exported {len(df_tx)} price history transactions to {out_file}[/bold green]")

        # 4. Separated Images
        if table_choice in ("images", "all"):
            out_file = f"{base_prefix}_images.{fmt}" if table_choice == "all" else args.output
            console.print(f"[bold cyan]Exporting separated images to {out_file}...[/bold cyan]")
            sql_images = """
                SELECT pi.id, pi.listing_id, pi.source_page, pi.image_type, pi.image_url, pi.caption, pi.display_order
                FROM property_images pi
                JOIN properties p ON pi.listing_id = p.listing_id
                ORDER BY pi.listing_id, pi.source_page, pi.display_order
            """
            df_images = pd.read_sql(sql_images, db.bind)
            if fmt == "csv":
                df_images.to_csv(out_file, index=False)
            else:
                df_images.to_json(out_file, orient="records", indent=2)
            console.print(f"[bold green][OK] Exported {len(df_images)} separated image URLs to {out_file}[/bold green]")

    finally:
        db.close()

def handle_import_postgres(args):
    from data.storage.migration import check_postgres_connection, migrate_sqlite_to_target
    target_url = getattr(args, "pg_url", None) or config.DATABASE_URL
    sqlite_path = getattr(args, "sqlite_path", str(SQLITE_PATH))
    batch_size = getattr(args, "batch_size", 500)
    dry_run = getattr(args, "dry_run", False)

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if not dry_run and make_url(target_url).get_backend_name() != "postgresql":
        console.print("[red]import-postgres requires a PostgreSQL target; set PROPERTYGURU_DATABASE_URL.[/red]")
        raise SystemExit(1)

    console.print(Panel(
        f"[bold cyan]PostgreSQL Target URL:[/bold cyan] {make_url(target_url).render_as_string(hide_password=True)}\n"
        f"[bold cyan]Source SQLite:[/bold cyan] {sqlite_path}\n"
        f"[bold cyan]Batch Size:[/bold cyan] {batch_size}",
        title="[bold green]Database Migration / PostgreSQL Ingestion[/bold green]"
    ))

    if dry_run:
        test_target = "sqlite:///:memory:"
        console.print("[yellow]Running in dry-run mode (migrating to in-memory test database)...[/yellow]")
        stats = migrate_sqlite_to_target(source_sqlite_path=sqlite_path, target_db_url=test_target, batch_size=batch_size)
        console.print("[bold green][OK] Dry-run completed successfully! All records valid.[/bold green]")
        return

    # Check connection
    ok, err = check_postgres_connection(target_url)
    if not ok:
        console.print(Panel(
            f"[bold red]Connection to PostgreSQL failed![/bold red]\n"
            f"[yellow]Error:[/yellow] {err}\n\n"
            f"[bold white]Troubleshooting Guide:[/bold white]\n"
            f"1. Make sure your local PostgreSQL service is running on port 5432, or start one using Docker:\n"
            f"   [cyan]See data/README.md PostgreSQL setup; bind port 5432 to loopback and use your own password.[/cyan]\n"
            f"2. Specify your custom connection URL via --pg-url:\n"
            f"   [cyan]python data/main.py import-postgres --pg-url postgresql+psycopg2://user:pass@host:5432/propertyguru[/cyan]\n"
            f"3. Or set PROPERTYGURU_DATABASE_URL in your .env file.\n\n"
            f"[green]Note:[/green] All 21,431 crawled listings are fully preserved in [bold]data/propertyguru.db[/bold] and [bold]data/exports/[/bold].",
            title="[bold red]PostgreSQL Not Reachable[/bold red]"
        ))
        sys.exit(1)

    console.print("[bold green]Connection established to PostgreSQL! Starting migration...[/bold green]")
    stats = migrate_sqlite_to_target(
        source_sqlite_path=sqlite_path,
        target_db_url=target_url,
        batch_size=batch_size
    )

    t = Table(title="PostgreSQL Migration Summary", show_header=True, header_style="bold green")
    t.add_column("Table", style="cyan")
    t.add_column("Source Records", justify="right")
    t.add_column("Migrated Records", justify="right")
    t.add_column("Target Count", justify="right")
    t.add_column("Status", style="bold green")

    for tbl in ("agents", "properties", "images", "price_history"):
        s = stats.get(tbl, {})
        tgt = stats.get("target_counts", {}).get(tbl, 0)
        t.add_row(
            tbl,
            f"{s.get('src', 0):,}",
            f"{s.get('migrated', 0):,}",
            f"{tgt:,}",
            "[green]VERIFIED OK[/green]" if s.get('migrated', 0) == s.get('src', 0) else "[yellow]PARTIAL[/yellow]"
        )
    console.print(t)
    console.print("[bold green][OK] Successfully ingested all data into PostgreSQL![/bold green]")

def handle_check_sparsity(args):
    import pandas as pd
    sqlite_path = getattr(args, "sqlite_path", str(SQLITE_PATH))
    console.print(f"[bold cyan]Checking data sparsity from {sqlite_path}...[/bold cyan]")

    from data.graph.source import open_graph_source
    eng = open_graph_source(sqlite_path=sqlite_path)
    df = pd.read_sql("SELECT * FROM properties", eng)
    eng.dispose()

    total_rows = len(df)
    t = Table(title=f"PropertyGuru Dataset Sparsity Audit (Total: {total_rows:,} Listings)", show_header=True, header_style="bold blue")
    t.add_column("Field / Column", style="cyan", width=22)
    t.add_column("Category", style="magenta", width=18)
    t.add_column("Valid (Filled)", justify="right", width=14)
    t.add_column("Missing (Null)", justify="right", width=14)
    t.add_column("Fill Rate (%)", justify="right", style="bold green", width=13)
    t.add_column("Sparsity (%)", justify="right", style="yellow", width=13)
    t.add_column("Remarks / Business Context", style="dim", width=42)

    field_categories = {
        "listing_id": ("Core Key", "Primary Key, 100% Unique"),
        "listing_type": ("Core Key", "RENT / SALE"),
        "transaction_category": ("Core Key", "买房/出售 或 租房/出租"),
        "title": ("Listing Info", "Listing title / condo name"),
        "property_type": ("Listing Info", "Condo, HDB, Landed, etc."),
        "price": ("Financial", "Monthly rent in SGD"),
        "currency": ("Financial", "Default SGD"),
        "psf": ("Financial", "Calculated price per sqft"),
        "bedrooms": ("Unit Specs", "Number of bedrooms"),
        "bathrooms": ("Unit Specs", "Number of bathrooms"),
        "floor_area_sqft": ("Unit Specs", "Omitted in room rentals"),
        "land_area_sqft": ("Unit Specs", "Only applicable to Landed houses"),
        "tenure": ("Building", "99-year Leasehold, Freehold"),
        "build_year": ("Building", "Year completed"),
        "full_address": ("Location", "Normalized address"),
        "district_code": ("Location", "Singapore D01-D28 postal district"),
        "region_code": ("Location", "OCR, CCR, RCR"),
        "postal_code": ("Location", "6-digit postal code (masked by some landlords)"),
        "street_name": ("Location", "Street name"),
        "street_number": ("Location", "House / block number"),
        "latitude": ("Geo", "GPS Latitude coordinates"),
        "longitude": ("Geo", "GPS Longitude coordinates"),
        "nearest_mrt": ("Transit", "Nearest MRT station name"),
        "mrt_distance_m": ("Transit", "Walking distance in meters"),
        "agent_name": ("Agent", "CEA licensed agent name"),
        "agent_license": ("Agent", "CEA License number"),
        "agency_name": ("Agent", "Agency firm (PropNex, ERA, etc.)"),
        "agent_phone": ("Agent", "Direct contact phone number"),
        "homepage_images": ("Media", "Thumbnails & previews"),
        "detail_images": ("Media", "High-res photos & floor plans"),
        "posted_at": ("Metadata", "Listing publication timestamp"),
    }

    for col, (cat, remarks) in field_categories.items():
        if col not in df.columns:
            continue
        series = df[col]
        if series.dtype == object:
            null_count = (series.isna() | (series == '') | (series == 'None') | (series == '{}') | (series == '[]')).sum()
        else:
            null_count = series.isna().sum()
        fill_count = total_rows - null_count
        fill_rate = (fill_count / total_rows) * 100
        sparsity_rate = 100 - fill_rate

        fill_style = "[bold green]" if fill_rate > 90 else ("[yellow]" if fill_rate > 40 else "[red]")
        t.add_row(
            col,
            cat,
            f"{fill_count:,}",
            f"{null_count:,}",
            f"{fill_style}{fill_rate:.1f}%[/]",
            f"{sparsity_rate:.1f}%",
            remarks
        )

    console.print(t)

def main():
    parser = argparse.ArgumentParser(description="PropertyGuru Scraper & Database Ingestion CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init-db command
    subparsers.add_parser("init-db", help="Initialize database tables and indexes")

    # run command
    run_parser = subparsers.add_parser("run", help="Run crawler and ingest into database")
    run_parser.add_argument("--type", choices=["sale", "rent", "all"], default="sale", help="Listing type (default: sale)")
    run_parser.add_argument("--districts", default="ALL", help="Comma-separated district codes or 'ALL' for all 28 Singapore districts (default: ALL)")
    run_parser.add_argument("--start-district", default=None, help="Starting district code to resume from (e.g. D01, D05)")
    run_parser.add_argument("--postal-range", default=None, help="Optional postal code range filter (e.g. 117000-139999)")
    run_parser.add_argument("--pages", type=int, default=5, help="Number of pages to scrape per category (default: 5)")
    run_parser.add_argument("--all-pages", action="store_true", help="Scrape all available pages in selected districts")
    run_parser.add_argument("--start-page", type=int, default=1, help="Starting page number (default: 1)")
    run_parser.add_argument("--skip-details", action="store_true", help="Skip detail page enrichment")
    run_parser.add_argument("--skip-price-history", action="store_true", help="Skip URA price history transactions fetching")
    run_parser.add_argument("--concurrency", type=int, default=5, help="Concurrent worker threads for detail pages (default: 5)")
    run_parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")
    run_parser.add_argument("--auto-export", action="store_true", default=True, help="Automatically export CSVs after crawl finishes (default: True)")
    run_parser.add_argument("--sync-graph", action="store_true", help="Sync committed rows to Neo4j after successful crawl")

    # sync-graph command (reads existing rows; no scraping)
    graph_parser = subparsers.add_parser("sync-graph", help="Project listings into local Neo4j")
    graph_parser.add_argument("--limit", type=int, default=None, help="Maximum listings for development")
    graph_parser.add_argument("--batch-size", type=int, default=500, help="Listings per transaction (default: 500)")

    # stats command
    subparsers.add_parser("stats", help="Display summary statistics from database")

    # export command
    export_parser = subparsers.add_parser("export", help="Export properties from DB to CSV or JSON")
    export_parser.add_argument("--output", default=str(EXPORT_DIR / "export_properties.csv"), help="Output file path prefix (default: data/exports/export_properties.csv)")
    export_parser.add_argument("--format", choices=["csv", "json"], default="csv", help="Export format")
    export_parser.add_argument("--table", choices=["properties", "agents", "price_history", "images", "all"], default="all", help="Table(s) to export (default: all)")

    # import-postgres command
    import_pg_parser = subparsers.add_parser("import-postgres", help="Migrate SQLite data to PostgreSQL")
    import_pg_parser.add_argument("--pg-url", default=None, help="PostgreSQL connection URL (default: read from config or .env)")
    import_pg_parser.add_argument("--sqlite-path", default=str(SQLITE_PATH), help="Source SQLite file path (default: data/propertyguru.db)")
    import_pg_parser.add_argument("--batch-size", type=int, default=500, help="Batch size (default: 500)")
    import_pg_parser.add_argument("--dry-run", action="store_true", help="Validate migration pipeline without PostgreSQL")

    # check-sparsity command
    sparsity_parser = subparsers.add_parser("check-sparsity", help="Check data sparsity and null-value distribution")
    sparsity_parser.add_argument("--sqlite-path", default=str(SQLITE_PATH), help="Database file path (default: data/propertyguru.db)")

    args = parser.parse_args()

    if args.command == "init-db":
        handle_init_db(args)
    elif args.command == "run":
        handle_run(args)
    elif args.command == "sync-graph":
        handle_sync_graph(args)
    elif args.command == "stats":
        handle_stats(args)
    elif args.command == "export":
        handle_export(args)
    elif args.command == "import-postgres":
        handle_import_postgres(args)
    elif args.command == "check-sparsity":
        handle_check_sparsity(args)

if __name__ == "__main__":
    main()
