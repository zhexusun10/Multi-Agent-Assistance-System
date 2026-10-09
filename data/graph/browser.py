"""Open the genuine Neo4j Browser using its supported URL parameters.

Optional Chrome automation uses an isolated window, never the user's existing
browser profile, and only performs no-auth connections and a bounded read query.
"""
import os
import re
import webbrowser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from data.graph.sync import create_graph_driver, graph_auth

DEFAULT_QUERY = (
    "MATCH (l:Listing) WITH l ORDER BY l.listing_id LIMIT 20 "
    "OPTIONAL MATCH (l)-[r:IN_PROJECT|LISTED_BY|IN_DISTRICT|NEAR_MRT]->(n) "
    "RETURN l, r, n;"
)


def browser_url(*, uri=None, database=None, base_url=None):
    """Prefill the Bolt URL, database, and editor; never put credentials in a URL."""
    connection = uri or os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    parsed = urlsplit(connection)
    if parsed.scheme not in ("bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc") or not parsed.hostname:
        raise ValueError("NEO4J_URI must be a valid Bolt/Neo4j connection URL")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("Do not embed credentials or query parameters in NEO4J_URI")
    base = urlsplit(base_url or os.getenv("NEO4J_BROWSER_URL", "http://127.0.0.1:7474/browser/"))
    if base.scheme not in ("http", "https") or not base.hostname or base.username is not None or base.password is not None:
        raise ValueError("NEO4J_BROWSER_URL must be an HTTP(S) URL without credentials")
    params = dict(parse_qsl(base.query))
    if any(key.lower() in ("password", "username", "auth", "token") for key in params):
        raise ValueError("Do not embed authentication information in NEO4J_BROWSER_URL")
    params.update(connectURL=connection, db=database or os.getenv("NEO4J_DATABASE", "neo4j"),
                  cmd="edit", arg=DEFAULT_QUERY)
    path = base.path if base.path not in ("", "/") else "/browser/"
    return urlunsplit((base.scheme, base.netloc, path, urlencode(params), ""))


def connect_browser_page(page, url):
    """Drive the official 5.26 Browser controls; do not patch its frontend assets."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    connect = page.get_by_role("button", name="Connect", exact=True)
    connect.wait_for(state="visible", timeout=60000)
    auth_select = page.locator("select").filter(has=page.locator('option[value="NO_AUTH"]'))
    auth_select.select_option("NO_AUTH")
    connect.click()
    page.get_by_text("You have a working connection and server auth is disabled.", exact=True).wait_for(timeout=30000)
    # URL parameters already prefill the editor. Reinsert the known read-only
    # query in this isolated session in case a startup guide changed focus/text.
    page.locator(".monaco-editor").first.click()
    page.keyboard.press("Control+A")
    page.keyboard.insert_text(DEFAULT_QUERY)
    page.keyboard.press("Control+Enter")
    # Use a DOM locator rather than JS eval: the official Browser has a strict CSP.
    page.get_by_text(re.compile(r"Displaying \d+ nodes?, \d+ relationships?\.")).first.wait_for(timeout=30000)


def open_browser(*, auto_connect=False):
    url = browser_url()
    if not auto_connect:
        webbrowser.open(url)
        return
    # Never type a password or automate connections to a remote no-auth server.
    if graph_auth() is not None:
        raise ValueError("--auto-connect is only for explicit local NEO4J_AUTH=none")
    if urlsplit(url).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("--auto-connect requires a loopback Browser URL")
    with create_graph_driver() as driver:
        driver.verify_connectivity()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Optional automation is not installed. Opening prefilled official Browser instead.\n"
              "Install data/graph_browser_requirements.txt for automatic connection.", flush=True)
        webbrowser.open(url)
        return
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=False, args=["--start-maximized"])
        except Exception:
            print("Chrome automation is unavailable. Opening the prefilled Browser in your default browser.", flush=True)
            webbrowser.open(url)
            return
        try:
            page = browser.new_page(no_viewport=True)
            connect_browser_page(page, url)
            print("Official Neo4j Browser connected without credentials and displaying the graph.", flush=True)
            # Keep the visible window alive until the user closes it. Launchers
            # run this in the background; stopping it only closes this window.
            while browser.is_connected() and browser.contexts and any(context.pages for context in browser.contexts):
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    break
        except Exception:
            print("Automatic Browser setup failed. Opening the prefilled URL for manual connection.", flush=True)
            webbrowser.open(url)
        finally:
            if browser.is_connected():
                browser.close()
