import logging
from typing import Optional, Callable, Tuple, List, Dict, Any
from dataclasses import dataclass, field
from scraper import PropertyGuruScraper
from cleaner import PropertyCleaner
from database import save_batch

logger = logging.getLogger(__name__)

@dataclass
class PipelineStats:
    total_pages_attempted: int = 0
    total_pages_success: int = 0
    total_raw_listings: int = 0
    total_cleaned: int = 0
    total_upserted: int = 0
    total_details_enriched: int = 0
    total_agents_saved: int = 0
    total_price_history_saved: int = 0
    total_homepage_images: int = 0
    total_detail_images: int = 0
    total_in_postal_range: int = 0
    total_errors: int = 0

class IngestionPipeline:
    def __init__(self, scraper: Optional[PropertyGuruScraper] = None):
        self.scraper = scraper or PropertyGuruScraper()
        self.processed_projects: set = set()

    def run_sync(
        self,
        listing_type: str = "sale",
        districts: Optional[List[str]] = None,
        start_page: int = 1,
        max_pages: Optional[int] = None,
        fetch_details: bool = True,
        fetch_price_history: bool = True,
        concurrency: int = 5,
        postal_range: Optional[Tuple[int, int]] = (117000, 139999),
        progress_callback: Optional[Callable[[int, int, int, PipelineStats], None]] = None
    ) -> PipelineStats:
        """
        Executes an ingestion crawl for the specified districts and page range.
        Handles full detail enrichment (specific address, agent profile with tenure,
        separated images, and project price history).
        """
        stats = PipelineStats()
        self.processed_projects.clear()
        current_page = start_page
        total_pages_known = max_pages or 1
        dist_str = "+".join(districts) if districts else "ALL"

        logger.info(
            f"Starting pipeline: {listing_type.upper()}, Districts={dist_str}, "
            f"start_page={start_page}, max_pages={max_pages or 'ALL'}, "
            f"fetch_details={fetch_details}, fetch_price_history={fetch_price_history}"
        )

        while True:
            if max_pages and current_page > (start_page + max_pages - 1):
                break

            stats.total_pages_attempted += 1
            try:
                raw_listings, pagination = self.scraper.fetch_page(
                    current_page, listing_type=listing_type, districts=districts
                )
            except Exception:
                logger.exception("Failed to fetch page %s", current_page)
                stats.total_errors += 1
                raw_listings, pagination = [], {}

            site_total_pages = pagination.get("totalPages")
            if site_total_pages:
                total_pages_known = min(site_total_pages, start_page + max_pages - 1) if max_pages else site_total_pages

            if not raw_listings:
                logger.warning(f"No listings returned on page {current_page}.")
                stats.total_errors += 1
                if progress_callback:
                    progress_callback(current_page, total_pages_known, 0, stats)
                if site_total_pages and current_page >= site_total_pages:
                    break
                current_page += 1
                continue

            stats.total_pages_success += 1
            stats.total_raw_listings += len(raw_listings)

            cleaned_batch: List[Dict[str, Any]] = []
            for item in raw_listings:
                try:
                    cleaned = PropertyCleaner.clean_listing(item, default_type=listing_type)
                    if cleaned:
                        cleaned_batch.append(cleaned)
                except Exception as e:
                    logger.error(f"Cleaner error on listing: {e}")
                    stats.total_errors += 1

            # Concurrent detail enrichment
            # Dedup before fetching details or persisting; keep the last card.
            cleaned_batch = list({c["listing_id"]: c for c in cleaned_batch}.values())
            if fetch_details and cleaned_batch:
                urls = list({c["url"] for c in cleaned_batch if c.get("url")})
                logger.debug("Fetching %s detail pages (concurrency=%s)", len(urls), concurrency)
                try:
                    details_map = self.scraper.fetch_details_concurrent(urls, max_workers=concurrency)
                except Exception:
                    logger.exception("Detail fetch failed on page %s", current_page)
                    stats.total_errors += 1
                    details_map = {}
                for c in cleaned_batch:
                    u = c.get("url")
                    if u and details_map.get(u):
                        try:
                            PropertyCleaner.enrich_from_detail(c, details_map[u])
                            stats.total_details_enriched += 1
                        except Exception:
                            logger.exception("Detail enrichment failed for %s", c["listing_id"])
                            stats.total_errors += 1
                    elif u and u in details_map:
                        logger.warning("Detail fetch returned no data for %s", u)
                        stats.total_errors += 1

            # Process Agents, Price History, and Separated Images
            agents_batch: List[Dict[str, Any]] = []
            image_records_batch: List[Dict[str, Any]] = []
            tx_batch: List[Dict[str, Any]] = []

            for c in cleaned_batch:
                # 1. Postal Range check
                p_code = c.get("postal_code")
                is_match = False
                if p_code:
                    try:
                        p_int = int(p_code)
                        if postal_range and (postal_range[0] <= p_int <= postal_range[1]):
                            is_match = True
                    except ValueError:
                        pass
                elif c.get("district_code") == "D05":
                    is_match = True

                if is_match:
                    stats.total_in_postal_range += 1

                # 2. Extract Agent Record
                ag = PropertyCleaner.extract_agent_record(c)
                if ag:
                    agents_batch.append(ag)

                # 3. Extract Separated Images
                img_rows = PropertyCleaner.extract_separated_image_records(c)
                image_records_batch.extend(img_rows)

                # 4. Fetch Project Price History (transactions) if associated with a project
                if fetch_price_history and c.get("project_id"):
                    proj_id = c["project_id"]
                    if proj_id > 0 and proj_id not in self.processed_projects:
                        # Get project slug or title
                        proj_slug = (c.get("title") or "").lower().replace(" ", "-")
                        try:
                            html_proj = self.scraper.fetch_project_page(proj_id, proj_slug)
                            if html_proj:
                                tx_batch.extend(PropertyCleaner.parse_project_transactions(html_proj, proj_id, c.get("title")))
                                self.processed_projects.add(proj_id)
                        except Exception:
                            logger.exception("Price history fetch failed for project %s", proj_id)
                            stats.total_errors += 1

            stats.total_cleaned += len(cleaned_batch)

            # One transaction per page: no related rows when property save fails.
            if cleaned_batch:
                try:
                    upserted, ag_saved, _, tx_saved = save_batch(
                        cleaned_batch, image_records_batch, agents_batch, tx_batch
                    )
                    stats.total_upserted += upserted
                    stats.total_agents_saved += ag_saved
                    stats.total_price_history_saved += tx_saved
                    stats.total_homepage_images += sum(
                        r["source_page"] == "HOMEPAGE" for r in image_records_batch
                    )
                    stats.total_detail_images += sum(
                        r["source_page"] == "DETAIL_PAGE" for r in image_records_batch
                    )
                except Exception:
                    logger.exception("DB batch save failed on page %s", current_page)
                    stats.total_errors += 1
                    # Retry project transactions on a later page after rollback.
                    self.processed_projects.difference_update(r["project_id"] for r in tx_batch)

            logger.info("Page %s/%s: %s cleaned listings, %s total errors",
                        current_page, total_pages_known, len(cleaned_batch), stats.total_errors)

            if progress_callback:
                progress_callback(current_page, total_pages_known, len(cleaned_batch), stats)

            if site_total_pages and current_page >= site_total_pages:
                logger.info(f"Reached final page {site_total_pages} for {listing_type.upper()}.")
                break

            current_page += 1

        return stats
