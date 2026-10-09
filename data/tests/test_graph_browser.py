"""Safe official Browser deep links; no desktop browser or Neo4j service needed."""
import sys
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.service import app as graph_api
from data.graph import browser as graph_browser
from data import knowledge_graph


def test_link_prefills_connection_database_and_bounded_query(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    monkeypatch.setenv("NEO4J_BROWSER_URL", "http://127.0.0.1:7474/browser/")
    monkeypatch.setenv("NEO4J_PASSWORD", "not-for-browser-links")
    link = graph_browser.browser_url()
    parsed = urlsplit(link)
    values = parse_qs(parsed.query)
    assert parsed.scheme == "http" and parsed.netloc == "127.0.0.1:7474"
    assert values == {"connectURL": ["bolt://127.0.0.1:7687"], "db": ["neo4j"],
                      "cmd": ["edit"], "arg": [graph_browser.DEFAULT_QUERY]}
    assert "not-for-browser-links" not in link
    assert "LIMIT 20" in values["arg"][0]
    assert all(word not in values["arg"][0] for word in ("DELETE", "CREATE", "SET"))


def test_url_encoding_and_root_browser_path():
    link = graph_browser.browser_url(uri="bolt://[::1]:7687", database="房源 空格",
                                     base_url="http://localhost:7474/")
    parsed = urlsplit(link)
    assert parsed.path == "/browser/"
    assert parse_qs(parsed.query)["db"] == ["房源 空格"]
    assert parse_qs(parsed.query)["connectURL"] == ["bolt://[::1]:7687"]


@pytest.mark.parametrize("uri", ["http://localhost:7687", "bolt://user:secret@localhost:7687",
                                  "bolt://localhost:7687?password=secret", "bolt://localhost:7687#fragment"])
def test_unsafe_connection_urls_are_rejected(uri):
    with pytest.raises(ValueError):
        graph_browser.browser_url(uri=uri)


@pytest.mark.parametrize("base", ["javascript:alert(1)", "http://user:secret@localhost:7474/browser/",
                                   "http://localhost:7474/browser/?password=secret"])
def test_unsafe_browser_urls_are_rejected(base):
    with pytest.raises(ValueError):
        graph_browser.browser_url(base_url=base)


def test_manual_open_uses_safe_deep_link(monkeypatch):
    opener = Mock()
    monkeypatch.setattr(graph_browser.webbrowser, "open", opener)
    graph_browser.open_browser()
    opener.assert_called_once_with(graph_browser.browser_url())


def test_auto_connection_requires_explicit_no_auth(monkeypatch):
    monkeypatch.setattr(graph_browser, "graph_auth", lambda: ("neo4j", "test-password"))
    with pytest.raises(ValueError, match="local NEO4J_AUTH=none"):
        graph_browser.open_browser(auto_connect=True)


def test_official_browser_routes_redirect_without_credentials(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    monkeypatch.setenv("NEO4J_BROWSER_URL", "http://127.0.0.1:7474/browser/")
    monkeypatch.setenv("NEO4J_PASSWORD", "not-for-browser-links")
    driver = Mock()
    monkeypatch.setattr(graph_api, "read_query", Mock(side_effect=AssertionError("Redirects must not query Neo4j")))
    with TestClient(graph_api.create_app(driver=driver, database="neo4j")) as client:
        root = client.get("/", follow_redirects=False)
        assert root.status_code == 307 and root.headers["location"] == "/neo4j-browser"
        response = client.get(root.headers["location"], follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == graph_browser.browser_url(database="neo4j")
        assert "not-for-browser-links" not in response.headers["location"]
    driver.close.assert_not_called()


def test_custom_frontend_assets_and_redundant_routes_are_removed():
    assert not (Path(__file__).resolve().parents[1] / "graph_web").exists()
    with TestClient(graph_api.create_app(driver=Mock())) as client:
        schema = client.get("/openapi.json").json()
        for path in ("/static/index.html", "/static/app.js", "/static/style.css", "/static/propertyguru.grass",
                     "/api/regions", "/api/graph", "/api/expand"):
            assert client.get(path).status_code == 404
            assert path not in schema["paths"]
        for path in ("/api/v1/graph/search", "/api/v1/graph/expand", "/api/nearby"):
            assert path in schema["paths"]


def test_cli_prints_prefilled_link_without_opening_window(monkeypatch, capfd):
    opener = Mock()
    monkeypatch.setattr(graph_browser, "open_browser", opener)
    assert knowledge_graph.main(["browser", "--print-url"]) == 0
    assert capfd.readouterr().out.strip() == graph_browser.browser_url()
    opener.assert_not_called()
