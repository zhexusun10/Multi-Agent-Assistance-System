import sys
import argparse
import logging
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from rich.panel import Panel
from sqlalchemy.engine import make_url

from config import config
from database import init_db, get_stats, SessionLocal
from models import Property, PropertyImage, Agent, PriceHistory
from pipeline import IngestionPipeline, PipelineStats

console = Console()

def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )

def handle_init_db(args):
    console.print("[bold cyan]Initializing PostgreSQL database tables, columns, and indexes...[/bold cyan]")
    init_db()
    console.print("[bold green]✓ Database initialization complete.[/bold green]")

def handle_run(args):
    setup_logging(args.verbose)
    fetch_details = not args.skip_details
    fetch_price_history = not args.skip_price_history

    # Parse districts list: e.g. "D05,D21,D10,D03,D04" or ["D05", "D21", "D10", "D03", "D04"]
    districts = [d.strip().upper() for d in args.districts.split(",") if d.strip()] if args.districts else None

    # Parse postal range
    postal_range = None
    if args.postal_range:
        try:
            p_min, p_max = [int(x.strip()) for x in args.postal_range.split("-")]
            postal_range = (p_min, p_max)
        except Exception:
            pass

    max_pages = None if args.all_pages else args.pages

    console.print(Panel.fit(
        f"[bold blue]PropertyGuru Scraper & Ingestion Pipeline[/bold blue]\n"
        f"Target Type         : [cyan]{args.type.upper()}[/cyan]\n"
        f"Districts           : [cyan]{', '.join(districts) if districts else 'ALL'}[/cyan]\n"
        f"Postal Range Filter : [cyan]{args.postal_range or 'N/A'}[/cyan]\n"
        f"Pages               : [cyan]{'ALL available pages' if max_pages is None else f'{args.start_page} to {args.start_page + max_pages - 1} (Total {max_pages})'}[/cyan]\n"
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
        console.print(f"\n[bold yellow]>>> Starting crawl for category: {l_type.upper()} (Districts={', '.join(districts) if districts else 'ALL'})[/bold yellow]")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("• Page {task.fields[page]}/{task.fields[total_pages]}"),
            TextColumn("• Listings: {task.fields[upserted]}"),
            TextColumn("• Agents: {task.fields[agents]}"),
            TextColumn("• Price TX: {task.fields[tx]}"),
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

            stats = pipeline.run_sync(
                listing_type=l_type,
                districts=districts,
                start_page=args.start_page,
                max_pages=max_pages,
                fetch_details=fetch_details,
                fetch_price_history=fetch_price_history,
                concurrency=args.concurrency,
                postal_range=postal_range,
                progress_callback=progress_hook
            )

            overall_errors += stats.total_errors
            overall_upserted += stats.total_upserted
            overall_agents += stats.total_agents_saved
            overall_tx += stats.total_price_history_saved
            overall_images += (stats.total_homepage_images + stats.total_detail_images)

        console.print(
            f"[bold green]✓ {l_type.upper()} completed: "
            f"Pages={stats.total_pages_success}/{stats.total_pages_attempted}, "
            f"Listings Ingested={stats.total_upserted}, "
            f"Agents Saved={stats.total_agents_saved}, "
            f"Price History TX={stats.total_price_history_saved}, "
            f"Homepage Images={stats.total_homepage_images}, "
            f"Detail Images={stats.total_detail_images}[/bold green]"
        )

    if overall_errors:
        console.print(f"[bold red]Crawl completed with {overall_errors} error(s); check logs.[/bold red]")
        raise SystemExit(1)

    console.print(
        f"\n[bold green]★ All tasks finished! "
        f"Total properties: {overall_upserted}, "
        f"Total unique agents: {overall_agents}, "
        f"Total price history transactions: {overall_tx}, "
        f"Total images recorded: {overall_images}[/bold green]"
    )

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
            sql_props = """
                SELECT p.listing_id, 
                       p.listing_type, 
                       COALESCE(p.transaction_category, CASE WHEN p.listing_type = 'SALE' THEN '买房/出售' ELSE '租房/出租' END) as transaction_category,
                       p.title, p.property_type, p.price, p.psf,
                       p.bedrooms, p.bathrooms, p.floor_area_sqft, p.postal_code, p.street_name, p.street_number,
                       p.district_code, p.latitude, p.longitude,
                       p.agent_name, p.agency_name, p.agent_license, p.agent_years_with_pg, p.agent_phone,
                       p.homepage_images->>'thumbnail' as thumbnail_url,
                       p.detail_images->>'photo_count' as detail_photo_count,
                       p.detail_images->>'floor_plan_count' as detail_floorplan_count,
                       p.posted_at, p.url
                FROM properties p
                WHERE p.district_code IN ('D05', 'D21', 'D10', 'D03', 'D04')
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
                console.print(f"[bold green]✓ Exported {len(df_props)} total properties to {out_file}[/bold green]")
                console.print(f"[bold green]  - 买房 (SALE): {len(df_props[df_props['listing_type'] == 'SALE'])} records -> {sale_file}[/bold green]")
                console.print(f"[bold green]  - 租房 (RENT): {len(df_props[df_props['listing_type'] == 'RENT'])} records -> {rent_file}[/bold green]")
            else:
                df_props.to_json(out_file, orient="records", indent=2)
                console.print(f"[bold green]✓ Exported {len(df_props)} properties to {out_file}[/bold green]")

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
            console.print(f"[bold green]✓ Exported {len(df_agents)} agents to {out_file}[/bold green]")

        # 3. Price History
        if table_choice in ("price_history", "all"):
            out_file = f"{base_prefix}_price_history.{fmt}" if table_choice == "all" else args.output
            console.print(f"[bold cyan]Exporting price history to {out_file}...[/bold cyan]")
            sql_tx = """
                SELECT id, project_id, project_name, contract_date, price, psf, building, floor_level,
                       size_sqft, bedrooms, transaction_type, property_type, district_code, postal_code
                FROM price_history
                WHERE district_code IN ('D05', 'D21', 'D10', 'D03', 'D04')
                ORDER BY project_id, contract_date DESC
            """
            df_tx = pd.read_sql(sql_tx, db.bind)
            if fmt == "csv":
                df_tx.to_csv(out_file, index=False)
            else:
                df_tx.to_json(out_file, orient="records", indent=2)
            console.print(f"[bold green]✓ Exported {len(df_tx)} price history transactions to {out_file}[/bold green]")

        # 4. Separated Images
        if table_choice in ("images", "all"):
            out_file = f"{base_prefix}_images.{fmt}" if table_choice == "all" else args.output
            console.print(f"[bold cyan]Exporting separated images to {out_file}...[/bold cyan]")
            sql_images = """
                SELECT pi.id, pi.listing_id, pi.source_page, pi.image_type, pi.image_url, pi.caption, pi.display_order
                FROM property_images pi
                JOIN properties p ON pi.listing_id = p.listing_id
                WHERE p.district_code IN ('D05', 'D21', 'D10', 'D03', 'D04')
                ORDER BY pi.listing_id, pi.source_page, pi.display_order
            """
            df_images = pd.read_sql(sql_images, db.bind)
            if fmt == "csv":
                df_images.to_csv(out_file, index=False)
            else:
                df_images.to_json(out_file, orient="records", indent=2)
            console.print(f"[bold green]✓ Exported {len(df_images)} separated image URLs to {out_file}[/bold green]")

    finally:
        db.close()

def main():
    parser = argparse.ArgumentParser(description="PropertyGuru Scraper & PostgreSQL Ingestion CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init-db command
    subparsers.add_parser("init-db", help="Initialize PostgreSQL tables and indexes")

    # run command
    run_parser = subparsers.add_parser("run", help="Run crawler and ingest into PostgreSQL")
    run_parser.add_argument("--type", choices=["sale", "rent", "all"], default="sale", help="Listing type (default: sale)")
    run_parser.add_argument("--districts", default="D05,D21,D10,D03,D04", help="Comma-separated district codes (default: D05,D21,D10,D03,D04)")
    run_parser.add_argument("--postal-range", default=None, help="Optional postal code range filter (e.g. 117000-139999)")
    run_parser.add_argument("--pages", type=int, default=5, help="Number of pages to scrape per category (default: 5)")
    run_parser.add_argument("--all-pages", action="store_true", help="Scrape all available pages in selected districts")
    run_parser.add_argument("--start-page", type=int, default=1, help="Starting page number (default: 1)")
    run_parser.add_argument("--skip-details", action="store_true", help="Skip detail page enrichment")
    run_parser.add_argument("--skip-price-history", action="store_true", help="Skip URA price history transactions fetching")
    run_parser.add_argument("--concurrency", type=int, default=5, help="Concurrent worker threads for detail pages (default: 5)")
    run_parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")

    # stats command
    subparsers.add_parser("stats", help="Display summary statistics from PostgreSQL")

    # export command
    export_parser = subparsers.add_parser("export", help="Export properties from DB to CSV or JSON")
    export_parser.add_argument("--output", default="properties_selected_districts.csv", help="Output file path prefix")
    export_parser.add_argument("--format", choices=["csv", "json"], default="csv", help="Export format")
    export_parser.add_argument("--table", choices=["properties", "agents", "price_history", "images", "all"], default="all", help="Table(s) to export (default: all)")

    args = parser.parse_args()

    if args.command == "init-db":
        handle_init_db(args)
    elif args.command == "run":
        handle_run(args)
    elif args.command == "stats":
        handle_stats(args)
    elif args.command == "export":
        handle_export(args)

if __name__ == "__main__":
    main()
