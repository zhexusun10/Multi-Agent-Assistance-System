import json, math, sys
from pathlib import Path
import pytest
from pydantic import ValidationError
from dev.sharedContract.recommendationSubagentContract.request import (
    RecommendationRequest,
)
from dev.sharedContract.recommendationSubagentContract.requirements import Requirements
from dev.sharedContract.recommendationSubagentContract.feedback import ViewingEvent
from dev.sharedContract.recommendationSubagentContract.errors import RecommendationError
from dev.recommendationSubagent.repositories.connections import repositories
from dev.recommendationSubagent.state.store import StateStore
from dev.recommendationSubagent.service import RecommendationService
from dev.recommendationSubagent.agent.controller import (
    ScriptedController,
    Proposal,
    LiveController,
)
from dev.recommendationSubagent.ranking.ranker import pairwise, score
from dev.recommendationSubagent.ranking.metrics import haversine, numeric_similarity
from dev.recommendationSubagent.terminal.__main__ import feedback_example
from dev.recommendationSubagent.schemas import generate, validate_response


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


def next_req(response, **changes):
    return req(request_id="r2", previous_state_id=response.state.state_id, **changes)


def test_full_flow_and_restart(service):
    first = service.recommend(req())
    assert first.status == "success"
    assert len(first.recommendations) == 4
    assert all(
        r.scores.implicit_score is None
        and r.scores.combined_score == r.scores.explicit_score
        for r in first.recommendations
    )
    validate_response(first)
    generate(check=True)
    events = [
        ViewingEvent.model_validate({**e, "conversation_id": "c1"})
        for e in feedback_example(first)["events"]
    ]
    second = service.recommend(next_req(first, feedback=events))
    assert second.status == "success"
    assert all(r.scores.implicit_weight == 0.2 for r in second.recommendations)
    assert service.recommend(next_req(first, feedback=events)) == second
    duplicate = service.recommend(
        req(request_id="r3", previous_state_id=second.state.state_id, feedback=events)
    )
    assert duplicate.state.feedback_version == len(events)
    assert [r.scores for r in duplicate.recommendations] == [
        r.scores for r in second.recommendations
    ]
    reopened = StateStore(service.store.path)
    assert reopened.current("c1")["events"] == service.store.current("c1")["events"]
    reopened.close()
    trace = service.store.trace("c1", "r3")
    assert trace["steps"][-1]["kind"] == "final_gate" and trace["steps"][-1]["passed"]
    assert trace["steps"][0]["snapshot"]["rows"]


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
    events = feedback_example(first)["events"]
    events[0]["listing_id"] = "invented"
    events = [
        ViewingEvent.model_validate({**e, "conversation_id": "c1"}) for e in events
    ]
    failed = service.recommend(next_req(first, feedback=events))
    assert failed.error.code == "INVALID_FEEDBACK"
    assert service.store.current("c1")["events"] == []
    assert service.store.current("c1")["state_id"] == first.state.state_id


def test_session_conflict(service):
    first = service.recommend(req())
    events = feedback_example(first)["events"][:1]
    parsed = [
        ViewingEvent.model_validate({**e, "conversation_id": "c1"}) for e in events
    ]
    second = service.recommend(next_req(first, feedback=parsed))
    assert second.status == "success"
    parsed[0].payload.duration_seconds = 99
    third = service.recommend(
        req(request_id="r3", previous_state_id=second.state.state_id, feedback=parsed)
    )
    assert third.error.code == "FEEDBACK_CONFLICT"


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
    assert haversine(a, a) == 0
    assert pairwise(a, a)[0] == pytest.approx(1)
    assert pairwise(a, b)[0] == pytest.approx(pairwise(b, a)[0])
    assert numeric_similarity(0, 3, 3)[0] == 0
    assert numeric_similarity(0, 4, 3)[0] == 0
    assert numeric_similarity(0, 1, 3)[0] > numeric_similarity(0, 2, 3)[0]
    missing = a.model_copy(update={"latitude": None})
    assert haversine(missing, a) is None
    s, c, ds = pairwise(missing, a)
    assert s == pytest.approx(0.7) and c == pytest.approx(0.7)
    with pytest.raises(ValueError):
        pairwise(a, b, scales={"bedrooms": 0})
    with pytest.raises(ValueError):
        pairwise(a, b, weights={"bedrooms": -1})
    explicit = score(a, [], [], [])
    assert explicit.combined_score == 0 and explicit.ordering == "stable_listing_id"
    zero = score(a, [], [], [(b, 0)])
    assert zero.implicit_score is None
    assert (
        score(a, [], [], [(b, 120)]).implicit_score
        == score(a, [], [], [(b, 999)]).implicit_score
    )


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
            "from dev.sharedContract.recommendationSubagentContract.request import RecommendationRequest; import sys; assert not any(k.startswith('dev.recommendationSubagent') for k in sys.modules)",
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
    assert len(pool["rows"]) == 12
    trace = service.store.resolve_reference("c1", first.execution.trace_ref)
    assert service.store.resolve_reference("c1", trace["steps"][0]["snapshot_ref"])[
        "rows"
    ]
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
    event = ViewingEvent.model_validate(
        {**feedback_example(first)["events"][0], "conversation_id": "c1"}
    )
    event.payload.duration_seconds = 100000
    requirements = req().requirements.model_copy(deep=True)
    requirements.criteria[0].value = 2000.0
    result = service.recommend(
        next_req(
            first, feedback=[event], requirements=requirements, requirements_version=2
        )
    )
    assert result.status == "no_match" and not result.recommendations


def test_inclusive_geographic_boundary(service):
    from dev.recommendationSubagent.validation.rules import evaluate

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
    raw = {**feedback_example(first)["events"][0], "conversation_id": "c1"}
    for duration in [-1, float("inf"), float("nan")]:
        raw["payload"]["duration_seconds"] = duration
        with pytest.raises(ValidationError):
            ViewingEvent.model_validate(raw)


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
