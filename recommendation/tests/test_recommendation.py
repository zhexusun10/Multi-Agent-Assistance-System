import json, math, sys
from pathlib import Path
import pytest
from pydantic import ValidationError
from recommendation.contracts.request import (
    RecommendationRequest,
)
from recommendation.contracts.requirements import Requirements
from recommendation.contracts.behavior import BehaviorContext, ListingViewDuration
from recommendation.contracts.errors import RecommendationError
from recommendation.repositories.connections import repositories
from recommendation.state.store import StateStore
from recommendation.service import RecommendationService
from recommendation.agent.controller import (
    ScriptedController,
    Proposal,
    LiveController,
)
from recommendation.ranking.ranker import pair_similarity
from recommendation.ranking.scoring_v2 import browsing_weight
from recommendation.config import RecommendationConfig
from .test_scoring_v2 import pool_scores
from recommendation.ranking.metrics import haversine, numeric_similarity
from recommendation.terminal.__main__ import feedback_example
from recommendation.schemas import generate, validate_response


@pytest.fixture
def service(tmp_path):
    a, b = repositories("fixture")
    s = RecommendationService(
        a, b, StateStore(tmp_path / "state.sqlite"), ScriptedController()
    )
    yield s
    s.close()


def req(**changes):
    data = dict(
        request_id="r1",
        conversation_id="c1",
        requirements_version=1,
        requirements=json.loads(
            (Path(__file__).parents[1] / "examples/requirements.json").read_text()
        ),
    )
    data.update(changes)
    return RecommendationRequest(**data)


def snapshot(response, **changes):
    """behavior_context for ``response`` (simulated orchestrator, main contract)."""
    data = feedback_example(response, as_of="2026-10-23T08:00:00Z")
    data.update(changes)
    return BehaviorContext.model_validate(data)


def next_req(response, **changes):
    return req(request_id="r2", previous_state_id=response.state.state_id, **changes)


def test_full_flow_and_restart(service):
    first = service.recommend(req())
    assert first.status == "success"
    # Default 10 results, fewer when the pool is smaller (fixture pool: 9 eligible).
    assert len(first.recommendations) == min(10, first.pool.size) == 9
    # P5 (fused-v2): a missing behaviour branch contributes 0 and its weight is not
    # redistributed, so combined = soft_weight x soft (weighted-v1 gave the soft score weight 1).
    assert all(
        r.scores.behavior_score is None
        and r.scores.combined_score == r.scores.soft_weight * r.scores.soft_score
        for r in first.recommendations
    )
    validate_response(first)
    generate(check=True)
    feedback = snapshot(first)
    second = service.recommend(next_req(first, behavior_context=feedback))
    assert second.status == "success"
    # P5: the configured behaviour weight (ranking.behavior_weight) replaces weighted-v1's
    # fixed implicit weight.
    behavior_weight = RecommendationConfig.default().ranking.behavior_weight
    assert all(r.scores.behavior_weight == behavior_weight for r in second.recommendations)
    assert service.recommend(next_req(first, behavior_context=feedback)) == second
    # Re-sending the same snapshot under a new request ID adds nothing (no native accumulation).
    duplicate = service.recommend(
        req(request_id="r3", previous_state_id=second.state.state_id, behavior_context=feedback)
    )
    profile = service.store.current("c1")["profile"]
    assert profile["browsing"] == {
        r.listing_id: {"duration_seconds": r.duration_seconds, "view_count": r.view_count,
                       "interest_eligible": r.interest_eligible}
        for r in feedback.listing_view_durations
    }
    assert [r.scores for r in duplicate.recommendations] == [
        r.scores for r in second.recommendations
    ]
    reopened = StateStore(service.store.path)
    assert reopened.current("c1") == service.store.current("c1")
    reopened.close()
    trace = service.store.trace("c1", "r3")
    assert trace["steps"][-1]["kind"] == "final_gate" and trace["steps"][-1]["passed"]
    # P4: resolve_entities is the first tool step; build_pool is the step that built the pool.
    # P6: the step references the pool (pool_version); its rows resolve from pool_cache.
    build = next(s for s in trace["steps"] if s.get("result", {}).get("tool") == "build_pool")
    assert build["result"]["data"]["pool"]["pool_version"] == trace["pool_ref"]["pool_version"]
    assert service.store.resolve_reference("c1", f"{trace['trace_id']}/pool")["rows"]


def test_broader_tighter_soft_update(service):
    first = service.recommend(req())
    requirements = req().requirements.model_copy(deep=True)
    requirements.criteria[0].value = 2200.0
    second = service.recommend(
        next_req(first, requirements=requirements, requirements_version=2)
    )
    assert len(second.recommendations) == 2
    requirements.criteria[0].value = 6000.0
    requirements.criteria[2].value = ["D21"]
    third = service.recommend(
        req(
            request_id="r3",
            previous_state_id=second.state.state_id,
            requirements=requirements,
            requirements_version=3,
        )
    )
    assert third.state.assessed_count > second.state.assessed_count
    assert third.recommendations[0].listing.district_code == "D21"


def test_conflicts_atomicity_and_stale(service):
    first = service.recommend(req())
    assert service.recommend(req(result_limit=2)).error.code == "IDEMPOTENCY_CONFLICT"
    assert service.recommend(req(request_id="late")).error.code == "STALE_REFERENCE"
    # The failed requests above committed nothing (still sequence 1, first's state).
    assert service.store.current("c1")["state_id"] == first.state.state_id
    # A behaviour summary for a listing outside the pool is not an error: it is ignored and
    # counted (in-pool references only).
    data = feedback_example(first, as_of="2026-10-23T08:00:00Z")
    data["listing_view_durations"][0]["listing_id"] = "invented"
    accepted = service.recommend(next_req(first, behavior_context=BehaviorContext.model_validate(data)))
    assert accepted.status == "success"
    assert service.store.current("c1")["sequence"] == 2
    assert service.store.current("c1")["state_id"] == accepted.state.state_id


def test_stale_requirements_version_backwards_or_changed_without_newer_version(service):
    """Both STALE_REQUIREMENTS branches of context.manager.check_state; nothing is committed."""
    first = service.recommend(req(requirements_version=2))
    assert first.status == "success"
    after = lambda request_id, **changes: req(request_id=request_id, previous_state_id=first.state.state_id, **changes)
    backwards = service.recommend(after("backwards", requirements_version=1))
    assert backwards.error.code == "STALE_REQUIREMENTS" and "backwards" in backwards.error.message
    changed = req().requirements.model_dump(mode="json")
    changed["criteria"][0]["value"] = 2500
    same = service.recommend(after("same-version", requirements_version=2, requirements=changed))
    assert same.error.code == "STALE_REQUIREMENTS" and "newer version" in same.error.message
    assert service.store.current("c1")["state_id"] == first.state.state_id
    # Unchanged requirements at the same version, and changed ones at a newer version, are accepted.
    unchanged = service.recommend(after("unchanged", requirements_version=2))
    assert unchanged.status == "success"
    newer = service.recommend(req(request_id="newer", previous_state_id=unchanged.state.state_id,
                                  requirements_version=3, requirements=changed))
    assert newer.status == "success" and newer.state.run_sequence == 3


def test_session_conflict(service):
    # Contract 2.0 has no per-event sessions: a conflicting observation for the same
    # request ID is a different body and is rejected by request identity.
    first = service.recommend(req())
    feedback = snapshot(first)
    second = service.recommend(next_req(first, behavior_context=feedback))
    assert second.status == "success"
    changed = feedback.model_copy(deep=True)
    changed.listing_view_durations[0].duration_seconds = 99
    third = service.recommend(next_req(first, behavior_context=changed))
    assert third.error.code == "IDEMPOTENCY_CONFLICT"


def test_invalid_proposal_and_outages(service):
    class Bad(ScriptedController):
        def propose(self, d, c, t):
            super().propose(d, c, t)
            return Proposal(listing_ids=["invented"])

    service.controller = Bad()
    result = service.recommend(req())
    assert result.status == "error" and not result.recommendations
    service.controller = ScriptedController()

    def fail():
        raise RecommendationError("DATA_UNAVAILABLE", "test outage")

    service.listing.retrieve = fail
    assert service.recommend(req()).error.code == "DATA_UNAVAILABLE"


def test_no_match_unknown_unsupported(service):
    r = req()
    r.requirements.criteria[0].value = 1.0
    assert service.recommend(r).status == "no_match"
    r = req(conversation_id="c2")
    r.requirements = Requirements.model_validate(
        {
            "criteria": [
                {
                    "criterion_id": "f",
                    "field": "furnishing",
                    "operator": "eq",
                    "value": "furnished",
                    "strength": "hard",
                }
            ]
        }
    )
    assert service.recommend(r).status == "needs_clarification"
    r = req(conversation_id="c3")
    r.requirements = Requirements.model_validate(
        {
            "criteria": [
                {
                    "criterion_id": "d",
                    "field": "commute",
                    "operator": "lte",
                    "value": 30,
                    "unit": "minutes",
                    "strength": "hard",
                    "category": "School",
                    "distance_type": "transit",
                    "transport_mode": "transit",
                }
            ]
        }
    )
    assert service.recommend(r).status == "needs_clarification"


def test_geography(service):
    r = req()
    r.requirements = Requirements.model_validate_json(
        (Path(__file__).parents[1] / "examples/geography-fixture.json").read_text()
    )
    result = service.recommend(r)
    assert result.status == "success"
    assert all(
        e.method == "coordinate_haversine" or e.unknown_reason for e in result.evidence
    )
    r = req(conversation_id="c2")
    r.requirements = Requirements.model_validate(
        {
            "criteria": [
                {
                    "criterion_id": "d",
                    "field": "distance",
                    "operator": "lte",
                    "value": 5000,
                    "unit": "m",
                    "strength": "hard",
                    "destination_id": "absent",
                    "distance_type": "straight_line",
                }
            ]
        }
    )
    assert service.recommend(r).status == "needs_clarification"


def test_metrics(service):
    a = service.listing.rows[0]
    b = service.listing.rows[1]
    similarity = RecommendationConfig.default().ranking.similarity
    weights = {k: v for k, v in similarity.feature_weights.as_dict().items() if v > 0}
    spreads = {"price": 1000.0, "bedrooms": 1.0, "floor_area_sqft": 200.0}
    assert haversine(a, a) == 0
    # P5 (R1d): a listing is never compared with itself (weighted-v1 returned similarity 1).
    assert pair_similarity(a, a, weights, spreads, similarity) == (None, None, [])
    assert pair_similarity(a, b, weights, spreads, similarity)[0] == pytest.approx(
        pair_similarity(b, a, weights, spreads, similarity)[0])
    assert numeric_similarity(0, 3, 3)[0] == 0
    assert numeric_similarity(0, 4, 3)[0] == 0
    assert numeric_similarity(0, 1, 3)[0] > numeric_similarity(0, 2, 3)[0]
    missing = a.model_copy(update={"listing_id": "missing-coordinates", "latitude": None})
    assert haversine(missing, a) is None
    # P5: with geography_district_fallback the district codes are compared instead of the
    # coordinates (coverage stays 1); without it geography is not compared and the coverage
    # drops by its weight share (weighted-v1: fixed 0.7 for its geography weight 0.30).
    s, c, ds = pair_similarity(missing, a, weights, spreads, similarity)
    assert c == pytest.approx(1) and ("geography", 1.0, None, "district_fallback") in ds
    no_fallback = similarity.model_copy(update={"geography_district_fallback": False})
    s, c, ds = pair_similarity(missing, a, weights, spreads, no_fallback)
    assert c == pytest.approx(1 - weights["geography"] / sum(weights.values()))
    with pytest.raises(ValueError):
        pair_similarity(a, b, weights, {**spreads, "bedrooms": 0}, similarity)
    with pytest.raises(ValueError):
        numeric_similarity(0, 1, float("inf"))
    explicit = pool_scores([a, b]).breakdown(a.listing_id)
    assert explicit.combined_score == 0 and explicit.ordering == "stable_listing_id"
    zero = pool_scores([a, b], browsing=[(b.listing_id, 0)]).breakdown(a.listing_id)
    assert zero.behavior_score is None
    # P5: browsing is uncapped (duration_cap_seconds null): more than 120 s still increases the
    # reference weight (weighted-v1 capped it at 120 s, so 120 s and 999 s scored alike).
    browsing = RecommendationConfig.default().ranking.browsing
    assert browsing.duration_cap_seconds is None
    assert browsing_weight(999, browsing) > browsing_weight(120, browsing)


@pytest.mark.parametrize(
    "patch",
    [
        {"operator": "in", "value": [1, 2]},
        {"unit": "USD"},
        {"value": float("nan")},
        {"value": [4, 2], "operator": "between"},
    ],
)
def test_bad_criteria(patch):
    d = req().model_dump()
    d["requirements"]["criteria"][0].update(patch)
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate(d)


def test_contradictions_and_contract_boundary():
    d = req().model_dump()
    d["requirements"]["criteria"].append(
        {
            "criterion_id": "min",
            "field": "price",
            "operator": "gte",
            "value": 5000,
            "unit": "SGD/month",
            "strength": "hard",
        }
    )
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate(d)
    import subprocess

    subprocess.run(
        [
            sys.executable,
            "-c",
            "from recommendation.contracts.request import RecommendationRequest; import sys; assert not any(k.startswith(('recommendation.service', 'recommendation.agent', 'recommendation.repositories', 'recommendation.ranking')) for k in sys.modules)",
        ],
        check=True,
    )


def test_loop_exhaustion(service):
    class Loop(LiveController):
        def invoke(self, messages, tools, remaining):
            return {"content": "not json", "tool_calls": []}

    service.controller = Loop()
    r = service.recommend(req())
    assert r.error.code == "LOOP_EXHAUSTED"
    assert (
        len([s for s in service.store.trace("c1")["steps"] if s["kind"] == "model"])
        == 8
    )


def test_reference_resolution_and_gate_replay(service):
    first = service.recommend(req())
    pool = service.store.resolve_reference("c1", first.state.candidate_pool_ref)
    # P3: '<trace>/pool' resolves the pool content from pool_cache (eligible rows only); the 12
    # source rows are counted in its partition.
    assert len(pool["rows"]) == first.pool.size and pool["partition"]["source_count"] == 12
    trace = service.store.resolve_reference("c1", first.execution.trace_ref)
    build = next(s for s in trace["steps"] if s.get("result", {}).get("tool") == "build_pool")  # P4 stage name
    # P6: snapshot_ref resolves to the compact step record (was: the dispatcher snapshot with rows).
    assert service.store.resolve_reference("c1", build["snapshot_ref"])[
        "result"
    ]["data"]["pool"]["pool_version"] == first.pool.pool_version
    with pytest.raises(RecommendationError):
        service.store.resolve_reference("other", first.execution.trace_ref)
    # A retry is a verified historical replay, even after source changes.
    service.listing.rows[0].price = 99999
    assert service.recommend(req()) == first
    assert (
        service.recommend(next_req(first)).recommendations[0].listing.listing_id
        != "fixture-01"
    )


def test_feedback_cannot_override_budget(service):
    first = service.recommend(req())
    feedback = snapshot(first)
    feedback.listing_view_durations[0].duration_seconds = 100000
    requirements = req().requirements.model_copy(deep=True)
    requirements.criteria[0].value = 2000.0
    result = service.recommend(
        next_req(
            first, behavior_context=feedback, requirements=requirements, requirements_version=2
        )
    )
    assert result.status == "no_match" and not result.recommendations


def test_inclusive_geographic_boundary(service):
    from recommendation.validation.rules import evaluate

    a = service.listing.rows[0]
    c = Requirements.model_validate_json(
        (Path(__file__).parents[1] / "examples/geography-fixture.json").read_text()
    ).criteria[0]
    evidence = service.geographic.evidence([a], [c])
    c.value = evidence[0].value
    assert evaluate(a, [c], evidence)[0].outcome == "pass"
    c.value -= 0.001
    assert evaluate(a, [c], evidence)[0].outcome == "fail"


def test_failed_request_identity_and_finite_duration(service):
    first = service.recommend(req())
    bad = next_req(first)
    bad.previous_state_id = "wrong"
    assert service.recommend(bad).status == "error"
    assert service.recommend(next_req(first)).error.code == "IDEMPOTENCY_CONFLICT"
    raw = feedback_example(first)["listing_view_durations"][0]
    for duration in [-1, float("inf"), float("nan")]:
        with pytest.raises(ValidationError):
            ListingViewDuration.model_validate({**raw, "duration_seconds": duration})


def test_concurrent_state_updates(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "concurrent.sqlite"

    def execute(request):
        a, b = repositories("fixture")
        s = RecommendationService(a, b, StateStore(path), ScriptedController())
        try:
            return s.recommend(request)
        finally:
            s.close()

    initial = execute(req())
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [
            req(request_id=f"concurrent-{i}", previous_state_id=initial.state.state_id)
            for i in range(2)
        ]
        results = list(pool.map(execute, requests))
    assert sorted(r.status for r in results) == ["error", "success"]
    assert (
        next(r for r in results if r.status == "error").error.code == "STALE_REFERENCE"
    )
