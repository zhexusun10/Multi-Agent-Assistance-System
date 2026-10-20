import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from recommendation.config import (
    RecommendationConfig,
    ScoringPolicy,
    RunLimits,
)
from recommendation.agent.controller import (
    LiveController,
    ScriptedController,
)
from recommendation.repositories.connections import repositories
from recommendation.service import RecommendationService
from recommendation.state.store import StateStore
from recommendation.ranking.ranker import score
from recommendation.terminal.acceptance import run_acceptance
from recommendation.contracts.errors import RecommendationError
from recommendation.contracts.request import (
    RecommendationRequest,
)


def request():
    return RecommendationRequest(
        request_id="policy",
        conversation_id="policy",
        requirements_version=1,
        requirements=json.loads(
            (Path(__file__).parents[1] / "examples/requirements.json").read_text()
        ),
    )


def make_service(tmp_path, controller=None, config=None):
    listing, geo = repositories("fixture")
    return RecommendationService(
        listing,
        geo,
        StateStore(tmp_path / "state.sqlite"),
        controller or ScriptedController(),
        config=config,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"scales": {"bedrooms": 0, "floor_area_sqft": 500, "geography": 3000}},
        {
            "scales": {
                "bedrooms": float("inf"),
                "floor_area_sqft": 500,
                "geography": 3000,
            }
        },
        {
            "feature_weights": {
                "property_type": 0,
                "bedrooms": 0,
                "floor_area_sqft": 0,
                "geography": 0,
            }
        },
        {
            "numeric_bounds": {
                "price": (20, 10),
                "bedrooms": (0, 10),
                "floor_area_sqft": (0, 10000),
            }
        },
        {"feature_weights": {"unknown": 1}},
    ],
)
def test_invalid_policy(change):
    with pytest.raises(ValidationError):
        ScoringPolicy.model_validate(change)


def test_versioned_policy_and_global_disabled_features(tmp_path):
    default = ScoringPolicy()
    assert default.identifier == "weighted-v1"
    custom = ScoringPolicy(
        scales={"bedrooms": 2, "floor_area_sqft": 400, "geography": 2000}
    )
    assert custom.identifier != default.identifier
    assert (
        custom.identifier
        == ScoringPolicy.model_validate(custom.model_dump()).identifier
    )
    config = RecommendationConfig(scoring=custom)
    service = make_service(tmp_path, config=config)
    try:
        response = service.recommend(request())
        assert response.status == "success"
        assert response.execution.scoring_version == custom.identifier
        trace = service.store.trace("policy")
        assert trace["configuration"]["scoring"] == custom.model_dump(mode="json")
        assert (
            service.store.current("policy")["profile"]["scoring_version"]
            == custom.identifier
        )
        rows = service.listing.retrieve()
        only_bedrooms = ScoringPolicy(
            feature_weights={
                "property_type": 0,
                "bedrooms": 1,
                "floor_area_sqft": 0,
                "geography": 0,
            }
        )
        result = score(
            rows[0], request().requirements.criteria, [], [(rows[1], 90)], only_bedrooms
        )
        assert (
            result.implicit_score is None
        )  # Explicit bedroom statement suppresses the only enabled feature.
    finally:
        service.close()


class RecoveringModel(LiveController):
    def __init__(self):
        super().__init__()
        self.turn = 0

    def invoke(self, messages, tools, remaining):
        self.turn += 1
        if self.turn == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "bad",
                        "name": "rank_candidates",
                        "args": {"context_ref": "wrong"},
                    }
                ],
            }
        if self.turn == 2:
            error = json.loads(messages[-1]["content"])
            assert error["error"]["code"] == "STALE_REFERENCE"
            context = json.loads(messages[1]["content"])["context_ref"]
            return {
                "content": "",
                "tool_calls": [
                    {"id": str(i), "name": name, "args": {"context_ref": context}}
                    for i, name in enumerate(
                        (
                            "retrieve_listings",
                            "get_geographic_evidence",
                            "validate_hard_constraints",
                            "rank_candidates",
                        )
                    )
                ],
            }
        ranked = json.loads(messages[-1]["content"])["ranked_ids"][:4]
        return {
            "content": [{"type": "text", "text": json.dumps({"listing_ids": ranked})}],
            "tool_calls": [],
            "usage": {"total_tokens": 17},
        }


def test_live_dispatch_recovery_content_blocks_and_trace(tmp_path):
    service = make_service(tmp_path, RecoveringModel())
    try:
        result = service.recommend(request())
        assert result.status == "success"
        assert result.execution.model_calls == 3 and result.execution.tool_calls == 5
        assert result.execution.tokens == 17
        models = [
            s for s in service.store.trace("policy")["steps"] if s["kind"] == "model"
        ]
        assert len(models) == 3 and all(s["duration_ms"] >= 0 for s in models)
        assert json.loads(models[1]["messages"][-1]["content"])["status"] == "error"
    finally:
        service.close()


def test_failed_model_invocation_is_counted_and_traced(tmp_path):
    class Failure(LiveController):
        def invoke(self, *args):
            raise RecommendationError("MODEL_UNAVAILABLE", "fixture outage", True)

    service = make_service(tmp_path, Failure())
    try:
        result = service.recommend(request())
        assert result.error.code == "MODEL_UNAVAILABLE"
        assert result.execution.model_calls == 1
        step = service.store.trace("policy")["steps"][0]
        assert step["error"]["code"] == "MODEL_UNAVAILABLE" and step["duration_ms"] >= 0
        assert service.store.current("policy") is None
    finally:
        service.close()


def test_model_and_tool_budgets(tmp_path):
    class Malformed(LiveController):
        def invoke(self, *args):
            return {"content": "", "tool_calls": [{"id": None, "args": []}]}

    service = make_service(
        tmp_path, Malformed(), RecommendationConfig(limits=RunLimits(model_turns=2))
    )
    try:
        result = service.recommend(request())
        assert result.error.code == "LOOP_EXHAUSTED"
        assert result.execution.model_calls == 2
    finally:
        service.close()
    other = tmp_path / "other"
    other.mkdir()
    service = make_service(
        other, config=RecommendationConfig(limits=RunLimits(tool_calls=2))
    )
    try:
        response = service.recommend(request())
        assert response.error.code == "LOOP_EXHAUSTED"
        assert response.execution.tool_calls == 2
        assert service.store.current("policy") is None
    finally:
        service.close()


def test_final_refresh_timeout_does_not_commit(tmp_path, monkeypatch):
    import recommendation.tools.dispatcher as dispatch_module

    clock = [0.0]
    monkeypatch.setattr(dispatch_module.time, "monotonic", lambda: clock[0])
    service = make_service(
        tmp_path,
        config=RecommendationConfig(limits=RunLimits(timeout_seconds=1, repairs=0)),
    )
    original = service.listing.retrieve
    calls = [0]

    def slow_refresh():
        calls[0] += 1
        result = original()
        if calls[0] == 2:
            clock[0] = 2.0
        return result

    service.listing.retrieve = slow_refresh
    try:
        result = service.recommend(request())
        assert result.error.code == "LOOP_EXHAUSTED"
        assert service.store.current("policy") is None
    finally:
        service.close()


def test_acceptance_report_offline_and_blocked(tmp_path, monkeypatch):
    from recommendation.terminal import acceptance

    report = run_acceptance(
        tmp_path / "offline",
        request().requirements,
        data_mode="fixture",
        controller_mode="scripted",
    )
    assert (
        report["status"] == "passed" and report["live_acceptance"] == "not_run_offline"
    )
    assert (tmp_path / "offline/trace.json").exists()
    tighter = json.loads(
        (tmp_path / "offline/tighter_requirements.request.json").read_text()
    )
    for criterion in request().requirements.model_dump()["criteria"]:
        assert criterion in tighter["requirements"]["criteria"]
    monkeypatch.setattr(
        acceptance,
        "check_connections",
        lambda *args: {
            "listing": {"contacted": False},
            "neo4j": {"contacted": False},
            "llm": {"configured": False},
        },
    )
    report = run_acceptance(tmp_path / "blocked", request().requirements)
    assert report["live_acceptance"] == "blocked"
    assert all(s["status"] == "blocked" for s in report["steps"].values())


def test_model_preview_limit_preserves_full_pool(tmp_path):
    service = make_service(
        tmp_path,
        config=RecommendationConfig(limits=RunLimits(model_result_candidates=4)),
    )
    try:
        response = service.recommend(request())
        assert response.status == "success"
        trace = service.store.trace("policy")
        retrieval = next(step for step in trace["steps"] if step["kind"] == "tool")
        assert retrieval["result"]["candidate_ids_truncated"]
        assert len(retrieval["result"]["candidate_ids"]) == 4
        assert (
            retrieval["result"]["candidate_count"]
            == len(retrieval["snapshot"]["pool_ids"])
            > 4
        )
        ranking = [step for step in trace["steps"] if step["kind"] == "tool"][-1]
        assert "contributions" not in ranking["result"]["previews"][0]["scores"]
        assert ranking["snapshot"]["scores"][
            response.recommendations[0].listing.listing_id
        ]["contributions"]
    finally:
        service.close()


def test_configured_scales_change_similarity_without_pool_normalization(tmp_path):
    service = make_service(tmp_path)
    try:
        a, b = service.listing.retrieve()[:2]
        default = score(a, [], [], [(b, 90)])
        custom = ScoringPolicy(
            scales={"bedrooms": 3, "floor_area_sqft": 1000, "geography": 6000}
        )
        modified = score(a, [], [], [(b, 90)], custom)
        assert modified.implicit_score > default.implicit_score
        assert any(
            c.scale == 6000 for c in modified.contributions if c.feature == "geography"
        )
    finally:
        service.close()
