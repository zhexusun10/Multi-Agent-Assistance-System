import logging
from typing import Optional, Callable, Tuple, List, Dict, Any
from dataclasses import dataclass, field
from scraper import PropertyGuruScraper
from cleaner import PropertyCleaner
from database import upsert_properties, upsert_property_images, upsert_agents, upsert_price_history

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
            raw_listings, pagination = self.scraper.fetch_page(
                current_page,
                listing_type=listing_type,
                districts=districts
            )

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
            if fetch_details and cleaned_batch:
                urls = [c["url"] for c in cleaned_batch if c.get("url")]
                logger.debug(f"Fetching {len(urls)} detail pages concurrently (concurrency={concurrency})...")
                details_map = self.scraper.fetch_details_concurrent(urls, max_workers=concurrency)
                
                for c in cleaned_batch:
                    u = c.get("url")
                    if u and u in details_map and details_map[u]:
                        c = PropertyCleaner.enrich_from_detail(c, details_map[u])
                        stats.total_details_enriched += 1

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
                for r in img_rows:
                    if r["source_page"] == "HOMEPAGE":
                        stats.total_homepage_images += 1
                    elif r["source_page"] == "DETAIL_PAGE":
                        stats.total_detail_images += 1

                # 4. Fetch Project Price History (transactions) if associated with a project
                if fetch_price_history and c.get("project_id"):
                    proj_id = c["project_id"]
                    if proj_id > 0 and proj_id not in self.processed_projects:
                        self.processed_projects.add(proj_id)
                        # Get project slug or title
                        proj_slug = (c.get("title") or "").lower().replace(" ", "-")
                        html_proj = self.scraper.fetch_project_page(proj_id, proj_slug)
                        if html_proj:
                            txs = PropertyCleaner.parse_project_transactions(html_proj, proj_id, c.get("title"))
                            tx_batch.extend(txs)

            stats.total_cleaned += len(cleaned_batch)

            # Bulk save to PostgreSQL
            if cleaned_batch:
                try:
                    upserted = upsert_properties(cleaned_batch)
                    stats.total_upserted += upserted
                except Exception as e:
                    logger.error(f"DB properties upsert error: {e}")
                    stats.total_errors += 1

            if agents_batch:
                try:
                    ag_saved = upsert_agents(agents_batch)
                    stats.total_agents_saved += ag_saved
                except Exception as e:
                    logger.error(f"DB agents upsert error: {e}")

            if image_records_batch:
                try:
                    upsert_property_images(image_records_batch)
                except Exception as e:
                    logger.error(f"DB images upsert error: {e}")

            if tx_batch:
                try:
                    tx_saved = upsert_price_history(tx_batch)
                    stats.total_price_history_saved += tx_saved
                except Exception as e:
                    logger.error(f"DB price history upsert error: {e}")

            logger.info(
                f"Page {current_page}/{total_pages_known}: "
                f"saved {len(cleaned_batch)} properties, {len(agents_batch)} agents, "
                f"{len(image_records_batch)} images, {len(tx_batch)} price transactions."
            )

            if progress_callback:
                progress_callback(current_page, total_pages_known, len(cleaned_batch), stats)

            if site_total_pages and current_page >= site_total_pages:
                logger.info(f"Reached final page {site_total_pages} for {listing_type.upper()}.")
                break

            current_page += 1

        return stats
