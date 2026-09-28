import json
import time
import random
import logging
from typing import Tuple, List, Dict, Any, Optional
from bs4 import BeautifulSoup
from curl_cffi import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import config

logger = logging.getLogger(__name__)

class PropertyGuruScraper:
    def __init__(self, impersonate: str = config.IMPERSONATE):
        self.impersonate = impersonate
        self.session: Optional[requests.Session] = None
        self.last_url: Optional[str] = None
        self.project_cache: Dict[int, str] = {}
        self._init_session()

    def _get_headers(self, referer: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": "\"Chromium\";v=\"124\", \"Google Chrome\";v=\"124\", \"Not-A.Brand\";v=\"99\"",
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": "\"macOS\"",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin" if referer else "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1"
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def _init_session(self):
        """Create a new curl_cffi session and warm it up to acquire Cloudflare clearance cookies."""
        logger.info("Initializing scraping session with TLS impersonation...")
        self.session = requests.Session(impersonate=self.impersonate)
        try:
            headers = self._get_headers()
            warm_url = f"{config.BASE_URL}{config.SALE_PATH}"
            resp = self.session.get(warm_url, headers=headers, timeout=config.REQUEST_TIMEOUT)
            if resp.status_code == 200:
                logger.info("Session warmed up successfully. Clearance cookies acquired.")
                self.last_url = warm_url
            else:
                logger.warning(f"Session warmup returned HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Session warmup exception: {e}")

    def _build_url(self, listing_type: str, page: int, districts: Optional[List[str]] = None) -> str:
        path = config.RENT_PATH if listing_type.upper() == "RENT" else config.SALE_PATH
        page_part = f"/{page}" if page > 1 else ""
        if districts:
            query_part = "&".join([f"district_code[]={d}" for d in districts])
            return f"{config.BASE_URL}{path}{page_part}?{query_part}"
        return f"{config.BASE_URL}{path}{page_part}"

    def fetch_page(
        self,
        page: int,
        listing_type: str = "sale",
        districts: Optional[List[str]] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Fetch a single page of listings supporting single or multiple districts.
        Returns (listings_raw_list, pagination_data_dict)
        """
        url = self._build_url(listing_type, page, districts=districts)
        headers = self._get_headers(referer=self.last_url)
        retries = 0

        while retries < config.MAX_RETRIES:
            try:
                sleep_time = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
                time.sleep(sleep_time)

                logger.debug(f"Requesting SRP {url} (attempt {retries + 1}/{config.MAX_RETRIES})...")
                resp = self.session.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT)

                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    script_tag = soup.find("script", id="__NEXT_DATA__")
                    
                    if not script_tag or not script_tag.string:
                        logger.warning(f"__NEXT_DATA__ tag not found on {url}")
                        retries += 1
                        time.sleep(config.BACKOFF_FACTOR * retries)
                        continue

                    data = json.loads(script_tag.string)
                    pdata = data.get("props", {}).get("pageProps", {}).get("pageData", {}).get("data", {})
                    
                    listings = pdata.get("listingsData", [])
                    pagination = pdata.get("paginationData", {})
                    
                    self.last_url = url
                    dist_str = "+".join(districts) if districts else "ALL"
                    logger.info(f"Page {page} ({listing_type.upper()}, Districts={dist_str}): retrieved {len(listings)} listings.")
                    return listings, pagination

                elif resp.status_code in (403, 429):
                    logger.warning(f"HTTP {resp.status_code} on {url}. Reinitializing session...")
                    time.sleep(config.BACKOFF_FACTOR * (retries + 1))
                    self._init_session()
                    headers = self._get_headers(referer=self.last_url)
                    retries += 1
                else:
                    logger.warning(f"HTTP {resp.status_code} on {url}. Retrying...")
                    retries += 1
                    time.sleep(config.BACKOFF_FACTOR * retries)

            except Exception as e:
                logger.error(f"Error fetching page {page} on {url}: {e}")
                retries += 1
                time.sleep(config.BACKOFF_FACTOR * retries)

        logger.error(f"Failed to fetch {url} after {config.MAX_RETRIES} attempts.")
        return [], {}

    def fetch_details_concurrent(
        self,
        urls: List[str],
        max_workers: int = 5
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Fetch batch of listing detail pages concurrently with cookie inheritance.
        """
        results = {}
        if not urls:
            return results

        cookies_dict = dict(self.session.cookies) if self.session else {}

        def _fetch_single(u: str) -> Tuple[str, Optional[Dict[str, Any]]]:
            w_session = requests.Session(impersonate=self.impersonate)
            if cookies_dict:
                w_session.cookies.update(cookies_dict)
            headers = self._get_headers(referer=self.last_url)

            for attempt in range(3):
                try:
                    time.sleep(random.uniform(0.2, 0.5))
                    resp = w_session.get(u, headers=headers, timeout=config.REQUEST_TIMEOUT)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        script_tag = soup.find("script", id="__NEXT_DATA__")
                        if script_tag and script_tag.string:
                            data = json.loads(script_tag.string)
                            pdata = data.get("props", {}).get("pageProps", {}).get("pageData", {}).get("data", {})
                            return u, pdata
                    elif resp.status_code in (403, 429):
                        time.sleep(1.0 * (attempt + 1))
                except Exception as e:
                    logger.debug(f"Worker exception on {u} (attempt {attempt+1}): {e}")
                    time.sleep(0.5)

            return u, None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_url = {executor.submit(_fetch_single, u): u for u in urls}
            for future in as_completed(future_to_url):
                try:
                    u, pdata = future.result()
                    results[u] = pdata
                except Exception:
                    results[future_to_url[future]] = None

        return results

    def fetch_project_page(self, project_id: int, project_slug: Optional[str] = None) -> Optional[str]:
        """
        Fetch project HTML page to extract past URA/HDB price history transactions.
        Results are cached in memory to avoid duplicate fetches for the same condo.
        """
        if not project_id:
            return None

        if project_id in self.project_cache:
            return self.project_cache[project_id]

        slug = project_slug or "project"
        url = f"{config.BASE_URL}/project/{slug}-{project_id}"
        headers = self._get_headers(referer=self.last_url)

        try:
            time.sleep(random.uniform(0.3, 0.7))
            resp = self.session.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT)
            if resp.status_code == 200:
                self.project_cache[project_id] = resp.text
                return resp.text
            elif resp.status_code == 404:
                # Project page doesn't exist
                self.project_cache[project_id] = ""
                return None
        except Exception as e:
            logger.debug(f"Error fetching project page {url}: {e}")

        return None
