import json
from pathlib import Path
import pytest
from recommendation.config import (
    RecommendationConfig,
    ConfigError,
)
from recommendation.ranking.scoring_v2 import scoring_identifier
from recommendation.agent.controller import (
    LiveController,
    ScriptedController,
)
from recommendation.repositories.connections import repositories
from recommendation.service import RecommendationService
from recommendation.state.store import StateStore
from .test_scoring_v2 import pool_scores
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


def settings(**overrides):
    # Explicit fixture config: the committed YAML plus dotted-key overrides, re-validated.
    return RecommendationConfig.default().with_overrides(overrides)


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
    "change,key",
    [
        # Similarity scales: numeric spreads are fitted on the pool (numeric_scaling, P5); the
        # configurable fixed scale is the geography scale.
        ({"ranking.similarity.geographic_scale_m": 0}, "ranking.similarity.geographic_scale_m"),
        (
            {"ranking.similarity.geographic_scale_m": float("inf")},
            "ranking.similarity.geographic_scale_m",
        ),
        (
            {
                "ranking.similarity.feature_weights": {
                    "price": 0,
                    "property_type": 0,
                    "bedrooms": 0,
                    "floor_area_sqft": 0,
                    "geography": 0,
                }
            },
            "ranking.similarity.feature_weights",
        ),
        ({"ranking.soft.numeric_bounds.price": [20, 10]}, "ranking.soft.numeric_bounds.price"),
        (
            {"ranking.similarity.feature_weights.unknown": 1},
            "ranking.similarity.feature_weights.unknown",
        ),
    ],
)
def test_invalid_policy(change, key):
    with pytest.raises(ConfigError) as error:
        RecommendationConfig.default().with_overrides(change)
    assert key in str(error.value)


def test_versioned_policy_and_global_disabled_features(tmp_path):
    default = RecommendationConfig.default()
    # No code defaults any more: the identifier always names the settings digest.
    assert scoring_identifier(default) == f"fused-v2-pool-minmax:{default.selection_hash[:12]}"  # P5: was weighted-v1
    custom = settings(**{"ranking.similarity.geographic_scale_m": 2000})
    assert scoring_identifier(custom) != scoring_identifier(default)
    assert scoring_identifier(custom) == scoring_identifier(
        RecommendationConfig.from_data(custom.to_data())
    )
    config = custom
    service = make_service(tmp_path, config=config)
    try:
        response = service.recommend(request())
        assert response.status == "success"
        assert response.execution.scoring_version == scoring_identifier(custom)
        trace = service.store.trace("policy")
        assert trace["configuration"] == custom.to_data()
        assert trace["config_hash"] == custom.config_hash
        assert (
            service.store.current("policy")["profile"]["scoring_version"]
            == scoring_identifier(custom)
        )
        rows = service.listing.retrieve()
        only_bedrooms = settings(
            **{
                "ranking.similarity.feature_weights": {
                    "price": 0,
                    "property_type": 0,
                    "bedrooms": 1,
                    "floor_area_sqft": 0,
                    "geography": 0,
                }
            }
        )
        result = pool_scores(
            rows, request().requirements.criteria, browsing=[(rows[1].listing_id, 90)], config=only_bedrooms
        ).breakdown(rows[0].listing_id)
        assert (
            result.behavior_score is not None
        )  # A hard bedroom criterion no longer suppresses a varying feature.
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
                            "resolve_entities",
                            "build_pool",
                            "get_geographic_evidence",
                            "rank_candidates",
                        )
                    )
                ],
            }
        artifact = json.loads(messages[-1]["content"])["ranking_artifact_id"]
        return {
            "content": [{"type": "text", "text": json.dumps({"ranking_artifact_id": artifact})}],
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
        # P6: a model step stores only the messages added since the previous one (was: "messages").
        assert json.loads(models[1]["messages_added"][-1]["content"])["status"] == "error"
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
        tmp_path, Malformed(), settings(**{"controller.max_model_turns": 2})
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
        other,
        config=settings(
            **{"controller.max_tool_calls": 2, "controller.max_resolution_tool_calls": 2}
        ),
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
        config=settings(**{"controller.timeout_seconds": 1, "controller.repairs": 0}),
    )
    # P3: the final refresh is the gate's by-id re-read with the data-version re-query
    # (lookup_versioned), no longer a second full retrieve.
    original = service.listing.lookup_versioned
    calls = [0]

    def slow_refresh(ids):
        calls[0] += 1
        result = original(ids)
        clock[0] = 2.0
        return result

    service.listing.lookup_versioned = slow_refresh
    try:
        result = service.recommend(request())
        assert result.error.code == "LOOP_EXHAUSTED"
        assert calls[0] == 1  # the timeout happened during the final refresh
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
        # model_result_candidates must be >= recommendation.max_result_limit, so this
        # experiment config lowers both to keep the preview smaller than the pool.
        config=settings(
            **{
                "controller.model_result_candidates": 4,
                "recommendation.max_result_limit": 4,
                "recommendation.default_result_limit": 4,
            }
        ),
    )
    try:
        response = service.recommend(request())
        assert response.status == "success"
        trace = service.store.trace("policy")
        retrieval = next(step for step in trace["steps"]
                         if step["kind"] == "tool" and step["result"]["tool"] == "build_pool")
        assert retrieval["result"]["candidate_ids_truncated"]
        assert len(retrieval["result"]["candidate_ids"]) == 4
        # P6: the full pool is preserved in pool_cache ('<trace>/pool'), not in a tool snapshot.
        assert (
            retrieval["result"]["candidate_count"]
            == len(service.store.resolve_reference("policy", response.state.candidate_pool_ref)["rows"])
            > 4
        )
        ranking = [step for step in trace["steps"] if step["kind"] == "tool"][-1]
        assert "contributions" not in ranking["result"]["previews"][0]["scores"]
        # P6: full breakdowns live in the response (and the debug trace), not in a tool snapshot.
        assert response.recommendations[0].scores.contributions
    finally:
        service.close()


def test_legacy_geography_scale_does_not_override_pool_normalization(tmp_path):
    service = make_service(tmp_path)
    try:
        a, b = service.listing.retrieve()[:2]
        default = pool_scores([a, b], browsing=[(b.listing_id, 90)]).breakdown(a.listing_id)
        # Legacy fixed scale cannot change current pool-normalized geography.
        custom = settings(**{"ranking.similarity.geographic_scale_m": 6000})
        modified = pool_scores([a, b], browsing=[(b.listing_id, 90)], config=custom).breakdown(a.listing_id)
        assert modified.behavior_score == default.behavior_score
        assert any(
            c.scale != 6000 for c in modified.contributions if c.feature == "geography"
        )
    finally:
        service.close()
