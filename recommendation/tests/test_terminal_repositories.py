import argparse, json
from pathlib import Path
from types import SimpleNamespace
import pytest
from recommendation.terminal.__main__ import Terminal
from recommendation.repositories.listing_repository import (
    normalize,
    deduplicate,
    SQLListingRepository,
)
from recommendation.repositories.geographic_repository import (
    Neo4jGeographicRepository,
)
from recommendation.contracts.errors import RecommendationError
from recommendation.contracts.requirements import Criterion


def test_terminal_staging_retry_resume(tmp_path, capsys):
    args = argparse.Namespace(
        state_path=str(tmp_path / "state.sqlite"),
        conversation_id="terminal",
        data_mode="fixture",
        controller="scripted",
    )
    t = Terminal(args)
    example = Path(__file__).parents[1] / "examples/requirements.json"
    t.command(f"requirements @{example}")
    t.command("recommend")
    first = t.response
    t.command(f"feedback-example {tmp_path}/feedback.json")
    t.command(f"feedback @{tmp_path}/feedback.json")
    assert t.events
    assert t.store.current("terminal")["events"] == []
    t.command("recommend")
    assert t.response.state.feedback_version == len(first.recommendations) and not t.events
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    with pytest.raises(ValueError):
        t.command(f"requirements @{bad}")
    assert t.response.state.feedback_version == len(first.recommendations)
    state = t.current["state_id"]
    t.service.close()
    resumed = Terminal(args)
    assert resumed.current["state_id"] == state
    resumed.command("show-trace")
    resumed.command("reset")
    assert resumed.current is None
    assert resumed.store.current("terminal")["state_id"] == state
    resumed.store.close()


def test_normalization():
    row = normalize(
        {
            "listing_id": 1,
            "listing_type": "RENT",
            "price": "",
            "bedrooms": "2",
            "postal_code": "012345",
            "property_type": "Condo",
        },
        "test",
    )
    assert (
        row.price is None
        and row.bedrooms == 2
        and row.postal_code == "012345"
        and row.property_type == "condo"
    )
    assert len(deduplicate([row, row])) == 1
    sentinel = normalize(
        {"listing_id": 2, "listing_type": "RENT", "bedrooms": -1, "latitude": 999},
        "test",
    )
    assert (
        sentinel.bedrooms is None
        and sentinel.latitude is None
        and sentinel.normalization_warnings
    )
    with pytest.raises(RecommendationError):
        deduplicate([row, row.model_copy(update={"price": 100.0})])


def test_sql_pagination_read_only_nulls():
    calls = []

    class Connection:
        page = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, params=None):
            calls.append((str(query), params))
            if str(query).startswith("SELECT"):
                self.page += 1
                return SimpleNamespace(
                    mappings=lambda: SimpleNamespace(
                        all=lambda: (
                            [
                                {
                                    "listing_id": self.page,
                                    "listing_type": "RENT",
                                    "price": None,
                                }
                            ]
                            if self.page < 3
                            else []
                        )
                    )
                )

    engine = SimpleNamespace(connect=lambda: Connection())
    rows = SQLListingRepository(engine).retrieve()
    assert len(rows) == 2 and rows[0].price is None
    assert calls[0][0] == "SET TRANSACTION READ ONLY"
    selects = [c for c in calls if c[1]]
    assert (
        selects[1][1]["after"] == 1 and "price" not in selects[0][0].split("WHERE")[1]
    )


def test_neo4j_schema_mapping_and_missing_edges():
    repo = Neo4jGeographicRepository(None, "neo4j")
    queries = []

    def query(q, **params):
        queries.append((q, params))
        if "p.active" in q:
            return [{"place_id": "OSM:node:1", "latitude": 1.3, "longitude": 103.8}]
        return []

    repo.query = query
    listing = normalize(
        {"listing_id": 1, "listing_type": "RENT", "latitude": 1.3, "longitude": 103.81},
        "test",
    )
    criterion = Criterion(
        criterion_id="geo",
        field="distance",
        operator="lte",
        value=2000,
        unit="m",
        strength="hard",
        category="School",
        distance_type="straight_line",
    )
    result = repo.evidence([listing], [criterion])[0]
    assert result.value > 0 and result.method == "coordinate_haversine"
    assert queries[1][1]["ids"] == [1]
    assert "NEAR_PLACE" in queries[1][0]


@pytest.mark.parametrize(
    "payload", [[], {"events": None}, {"events": [42]}, {"events": "wrong"}]
)
def test_terminal_rejects_malformed_event_shapes(tmp_path, payload):
    args = argparse.Namespace(
        state_path=str(tmp_path / "state.sqlite"),
        conversation_id="terminal",
        data_mode="fixture",
        controller="scripted",
    )
    terminal = Terminal(args)
    try:
        path = tmp_path / "feedback.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(ValueError):
            terminal.command(f"feedback @{path}")
        assert terminal.events == [] and terminal.current is None
    finally:
        terminal.store.close()
