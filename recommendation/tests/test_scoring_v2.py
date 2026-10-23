"""P5 part 1: full-pool fused scoring (ranking/scoring_v2.py; spec section 7, R1d, R10, R11)."""

import json
import math
import time
from pathlib import Path

import numpy as np
import pytest

from recommendation.agent.controller import ScriptedController
from recommendation.config import RecommendationConfig
from recommendation.contracts.feedback import FeedbackSnapshot
from recommendation.contracts.request import RecommendationRequest
from recommendation.contracts.requirements import Criterion
from recommendation.models.listing_summary import ListingSummary
from recommendation.ranking.ranker import pair_similarity
from recommendation.ranking.scoring_v2 import (
    FEATURES, PoolScores, browsing_weight, fit_spread, score_pool,
)
from recommendation.repositories.connections import repositories
from recommendation.service import RecommendationService
from recommendation.state.store import StateStore
from recommendation.validation.rules import evaluate

PACKAGE = Path(__file__).resolve().parents[1]
REQUIREMENTS = json.loads((PACKAGE / "examples/requirements.json").read_text())


# --- helpers (also used by test_settings / test_policy_controller / test_recommendation) -----
def feedback(browsing=(), appointments=(), request_id="previous-round"):
    """FeedbackSnapshot: browsing = [(listing_id, seconds | None (missing))], appointments =
    [(listing_id, statuses)]."""
    data = {"appointments": [
        {"listing_id": i, "statuses": list(statuses), "action_ids": [f"action-{i}-{k}" for k in range(len(statuses))]}
        for i, statuses in appointments
    ]}
    if browsing:
        data["previous_round"] = {
            "request_id": request_id,
            "shown_listing_ids": [i for i, _ in browsing],
            "browsing": [{"listing_id": i, "status": "recorded", "duration_seconds": t} if t is not None
                         else {"listing_id": i, "status": "missing"} for i, t in browsing],
        }
    return FeedbackSnapshot.model_validate(data)


def pool_scores(rows, criteria=(), browsing=(), appointments=(), config=None, evidence=None):
    """Score ``rows`` as the pool (evaluate() supplies the per-listing criterion results)."""
    config = config if config is not None else RecommendationConfig.default()
    evidence = evidence or {}
    criteria = list(criteria)
    results = {r.listing_id: evaluate(r, criteria, evidence.get(r.listing_id, [])) for r in rows}
    return score_pool(rows, criteria, results, evidence, feedback(browsing, appointments), config)


def settings(**overrides):
    return RecommendationConfig.default().with_overrides(overrides)


def fixture_rows():
    listing, geo = repositories("fixture")
    try:
        return listing.retrieve()
    finally:
        listing.close()
        geo.close()


def criteria(*items):
    return [Criterion.model_validate(c) for c in items]


SOFT_DISTRICT = {"criterion_id": "district", "field": "district_code", "operator": "in", "value": ["D05"],
                 "strength": "soft", "weight": 1}
SOFT_CHEAP = {"criterion_id": "cheap", "field": "price", "operator": "lte", "value": 2600, "unit": "SGD/month",
              "strength": "soft", "weight": 2, "utility": "minimize"}
HARD_BEDROOMS = {"criterion_id": "bedrooms", "field": "bedrooms", "operator": "gte", "value": 2, "strength": "hard"}


def make_service(tmp_path, config=None):
    listing, geo = repositories("fixture")
    return RecommendationService(listing, geo, StateStore(tmp_path / "state.sqlite"), ScriptedController(), config=config)


def request(request_id, conversation, **changes):
    body = dict(request_id=request_id, conversation_id=conversation, requirements_version=1, requirements=REQUIREMENTS)
    body.update(changes)
    return RecommendationRequest(**body)


def rank_step(service, conversation, request_id):
    trace = service.store.trace(conversation, request_id)
    return [s for s in trace["steps"] if s.get("result", {}).get("tool") == "rank_candidates"][-1]


def scalar_branch(rows, candidate, refs, weights_by_ref, scores):
    """Scalar oracle of a behaviour branch for ``candidate``: weighted mean of the available
    pair similarities (pair_similarity), or None."""
    similarity = scores.settings.ranking.similarity
    weights = dict(zip(scores.features, map(float, scores.weights.features)))
    by_id = {r.listing_id: r for r in rows}
    num = den = 0.0
    for ref in refs:
        s, _, _ = pair_similarity(by_id[candidate], by_id[ref], weights, scores.spreads, similarity)
        if s is not None:
            num += weights_by_ref[ref] * s
            den += weights_by_ref[ref]
    return num / den if den else None


# --- whole pool, formula, order ----------------------------------------------------------------
def test_whole_pool_is_scored_and_ordered_by_combined_then_id(tmp_path):
    service = make_service(tmp_path)
    try:
        first = service.recommend(request("w1", "whole"))
        shown = [i.listing.listing_id for i in first.recommendations]
        second = service.recommend(request(
            "w2", "whole", previous_state_id=first.state.state_id,
            feedback=feedback([(i, 30.0) for i in shown], [("fixture-03", ["MANUAL_SENT"])], request_id="w1")))
        assert second.status == "success"
        step = rank_step(service, "whole", "w2")
        scoring = step["result"]["data"]["scoring"]
        # P6: the slim trace has stage records instead of a dispatcher snapshot (was: snapshot ranking/scores).
        stage = [s for s in service.store.trace("whole", "w2")["stages"] if s["stage"] == "scoring"][-1]
        assert scoring["pool_scored"] == second.pool.size == stage["outputs"]["scored"] == stage["preview_total"] == 9
        # ScoreBreakdown objects only for the returned items and the preview rows (R10).
        preview = [r["listing_id"] for r in stage["preview"]]
        assert stage["outputs"]["breakdowns_built"] == len(set([i.listing.listing_id for i in second.recommendations] + preview[:service.config.trace.preview_rows]))
        ranks = [i.score_rank for i in second.recommendations]
        assert ranks == list(range(1, len(ranks) + 1))
    finally:
        service.close()
    rows = [r for r in fixture_rows() if r.listing_id not in ("fixture-10", "fixture-11", "fixture-12")]
    scores = pool_scores(rows, criteria(SOFT_DISTRICT, SOFT_CHEAP), browsing=[("fixture-01", 50), ("fixture-04", 10)],
                         appointments=[("fixture-06", ["CONFIRMED"])])
    combined = {i: scores.breakdown(i).combined_score for i in scores.ids}
    assert scores.ranked == sorted(scores.ids, key=lambda i: (-combined[i], i))
    assert scores.score_rank == {i: k for k, i in enumerate(scores.ranked, 1)}
    assert np.isfinite(scores.fused.combined).all() and len(scores.fused.combined) == len(rows)


def test_formula_missing_branches_contribute_zero_without_redistribution():
    rows = fixture_rows()[:9]
    config = RecommendationConfig.default()
    r = config.ranking
    scores = pool_scores(rows, criteria(SOFT_DISTRICT), appointments=[("fixture-02", ["MANUAL_SENT"])])
    for listing_id in scores.ids:
        b = scores.breakdown(listing_id)
        assert b.browsing_score is None  # no previous round
        appointment = b.appointment_score or 0.0
        behavior = b.behavior_score or 0.0
        assert behavior == pytest.approx(r.behavior.appointment_weight * appointment)
        assert b.combined_score == pytest.approx(r.soft_weight * b.soft_score + r.behavior_weight * behavior)
        assert (b.soft_weight, b.behavior_weight, b.appointment_weight, b.browsing_weight) == (
            r.soft_weight, r.behavior_weight, r.behavior.appointment_weight, r.behavior.browsing_weight)
        # Contributions sum to the branch scores.
        soft = sum(c.contribution for c in b.contributions if c.branch == "soft")
        assert soft == pytest.approx(b.soft_score)
        appointment_sum = sum(c.contribution for c in b.contributions if c.branch == "appointment")
        assert appointment_sum == pytest.approx(appointment)
    # The appointment listing is its own only reference: behaviour unavailable for it (R1d).
    assert scores.breakdown("fixture-02").behavior_score is None
    # No soft criteria: soft_score None (contributes 0) and coverage 0.
    no_soft = pool_scores(rows, browsing=[("fixture-01", 40)]).breakdown("fixture-03")
    assert no_soft.soft_score is None and no_soft.soft_evidence_coverage == 0
    assert no_soft.combined_score == pytest.approx(r.behavior_weight * r.behavior.browsing_weight * no_soft.browsing_score)


def test_numpy_arrays_match_the_scalar_reference():
    rows = fixture_rows()  # includes missing bedrooms (fixture-10) and coordinates (fixture-12)
    browsing = [("fixture-01", 90), ("fixture-10", 15), ("fixture-12", 400)]
    appointments = [("fixture-04", ["MANUAL_SENT"]), ("fixture-11", ["CONFIRMED", "MANUAL_REPLIED"])]
    for config in (RecommendationConfig.default(),
                   settings(**{"ranking.similarity.numeric_scaling": "std",
                               "ranking.similarity.geography_district_fallback": False,
                               "ranking.similarity.min_comparison_coverage": 0.9}),
                   settings(**{"ranking.similarity.numeric_scaling": "p05_p95", "ranking.similarity.suppression": "none"})):
        scores = pool_scores(rows, criteria(SOFT_DISTRICT), browsing=browsing, appointments=appointments, config=config)
        similarity = config.ranking.similarity
        weights = dict(zip(scores.features, map(float, scores.weights.features)))
        for i, a in enumerate(rows):
            for u, p in enumerate(scores.refs):
                s, c, _ = pair_similarity(a, rows[p], weights, scores.spreads, similarity)
                assert bool(scores.fused.pair_available[i, u]) == (s is not None), (a.listing_id, rows[p].listing_id)
                if s is not None:
                    assert scores.fused.pair[i, u] == pytest.approx(s, rel=1e-12, abs=1e-12)
                    assert scores.fused.pair_coverage[i, u] == pytest.approx(c, rel=1e-12)
        browse_w = {i: browsing_weight(t, config.ranking.browsing) for i, t in browsing}
        for a in rows:
            b = scores.breakdown(a.listing_id)
            expected_browse = scalar_branch(rows, a.listing_id, [i for i, _ in browsing], browse_w, scores)
            expected_appt = scalar_branch(rows, a.listing_id, [i for i, _ in appointments], {i: 1.0 for i, _ in appointments}, scores)
            assert (b.browsing_score is None) == (expected_browse is None)
            assert (b.appointment_score is None) == (expected_appt is None)
            if expected_browse is not None:
                assert b.browsing_score == pytest.approx(expected_browse, rel=1e-12)
                assert sum(c.contribution for c in b.contributions if c.branch == "browsing") == pytest.approx(expected_browse)
            if expected_appt is not None:
                assert b.appointment_score == pytest.approx(expected_appt, rel=1e-12)


# --- behaviour inputs ----------------------------------------------------------------------------
def test_earlier_round_browsing_has_no_effect(tmp_path):
    """Round 3 refers back to round 1 with the same snapshot in two conversations whose round-2
    snapshots differ: round 3 is scored identically (only the request's snapshot counts)."""
    service = make_service(tmp_path)
    try:
        third = {}
        second = {}
        for conversation, round2_seconds in (("early-a", 5.0), ("early-b", 900.0)):
            first = service.recommend(request(f"{conversation}-1", conversation))
            shown = [i.listing.listing_id for i in first.recommendations]
            r2 = service.recommend(request(
                f"{conversation}-2", conversation, previous_state_id=first.state.state_id,
                feedback=feedback([(i, round2_seconds if k % 2 else 1.0) for k, i in enumerate(shown)],
                                  request_id=f"{conversation}-1")))
            r3 = service.recommend(request(
                f"{conversation}-3", conversation, previous_state_id=r2.state.state_id,
                feedback=feedback([(i, 20.0 if k < 2 else None) for k, i in enumerate(shown)],
                                  request_id=f"{conversation}-1")))
            assert r2.status == r3.status == "success"
            second[conversation] = [(i.listing.listing_id, i.scores.model_dump()) for i in r2.recommendations]
            third[conversation] = [(i.listing.listing_id, i.scores.model_dump()) for i in r3.recommendations]
        assert second["early-a"] != second["early-b"]  # round-2 browsing mattered in round 2
        assert third["early-a"] == third["early-b"]  # and has no effect on round 3
    finally:
        service.close()


def test_browsing_beyond_120_seconds_still_increases_weight():
    config = RecommendationConfig.default()
    browsing = config.ranking.browsing
    assert browsing.duration_cap_seconds is None and browsing.transform == "log1p"
    series = [browsing_weight(t, browsing) for t in (1, 60, 120, 121, 600, 3600, 86400)]
    assert all(b > a for a, b in zip(series, series[1:]))
    assert browsing_weight(90, browsing) == pytest.approx(math.log1p(90 / browsing.time_scale_seconds))
    rows = fixture_rows()[:9]
    # Candidate fixture-02 is closer to reference fixture-04 (same type/district) than to fixture-03.
    by_seconds = {}
    for seconds in (60, 120, 600, 3600):
        scores = pool_scores(rows, browsing=[("fixture-04", seconds), ("fixture-03", 60)])
        by_seconds[seconds] = scores.breakdown("fixture-02").browsing_score
    assert by_seconds[60] < by_seconds[120] < by_seconds[600] < by_seconds[3600]
    # A cap applies only when configured.
    capped = settings(**{"ranking.browsing.duration_cap_seconds": 120})
    assert browsing_weight(600, capped.ranking.browsing) == browsing_weight(120, capped.ranking.browsing)


def test_single_reference_normalisation_cancels_absolute_duration():
    rows = fixture_rows()[:9]
    short = pool_scores(rows, browsing=[("fixture-04", 5)])
    long = pool_scores(rows, browsing=[("fixture-04", 5000)])
    assert np.array_equal(short.fused.branch["browsing"][0], long.fused.branch["browsing"][0])
    assert short.breakdown("fixture-02").browsing_score == long.breakdown("fixture-02").browsing_score
    # It is the plain pair similarity of that reference.
    weights = dict(zip(short.features, map(float, short.weights.features)))
    s, _, _ = pair_similarity(rows[1], rows[3], weights, short.spreads, short.settings.ranking.similarity)
    assert short.breakdown("fixture-02").browsing_score == pytest.approx(s)
    # With two references the relative duration matters.
    two_short = pool_scores(rows, browsing=[("fixture-04", 5), ("fixture-03", 50)])
    two_long = pool_scores(rows, browsing=[("fixture-04", 5000), ("fixture-03", 50)])
    assert two_short.breakdown("fixture-02").browsing_score != two_long.breakdown("fixture-02").browsing_score


def test_appointment_status_upgrade_adds_nothing(tmp_path):
    rows = fixture_rows()[:9]
    variants = [["MANUAL_SENT"], ["CONFIRMED", "MANUAL_SENT"], ["MANUAL_REPLIED", "MANUAL_SENT"],
                ["CONFIRMED", "MANUAL_REPLIED", "MANUAL_SENT"]]
    dumps = []
    for statuses in variants:
        scores = pool_scores(rows, criteria(SOFT_DISTRICT), appointments=[("fixture-04", statuses)])
        assert scores.references["appointment"]["ids"] == ["fixture-04"]
        dumps.append([scores.breakdown(i).model_dump() for i in scores.ids])
    assert all(d == dumps[0] for d in dumps)
    # Service level: the orchestrator's one entry per listing with every status.
    service = make_service(tmp_path)
    try:
        results = []
        for k, statuses in enumerate(variants):
            conversation = f"upgrade-{k}"
            first = service.recommend(request(f"{conversation}-1", conversation))
            second = service.recommend(request(f"{conversation}-2", conversation, previous_state_id=first.state.state_id,
                                               feedback=feedback(appointments=[("fixture-04", statuses)])))
            assert second.status == "success"
            results.append([(i.listing.listing_id, i.scores.model_dump()) for i in second.recommendations])
        assert all(r == results[0] for r in results)
        assert any(s["appointment_score"] is not None for _, s in results[0])
    finally:
        service.close()


def test_missing_attributes_are_not_compared_and_weights_renormalise():
    rows = fixture_rows()  # fixture-10: no bedrooms; fixture-12: no coordinates
    scores = pool_scores(rows, appointments=[("fixture-02", ["MANUAL_SENT"])])
    u = scores.ref_column[scores.index["fixture-02"]]
    weights = dict(zip(scores.features, map(float, scores.weights.features)))
    active = sum(weights.values())
    i = scores.index["fixture-10"]
    assert scores.fused.pair_coverage[i, u] == pytest.approx(1 - weights["bedrooms"] / active)
    b = scores.breakdown("fixture-10")
    bedrooms = next(c for c in b.contributions if c.feature == "bedrooms")
    assert bedrooms.unknown and bedrooms.weight == 0 and bedrooms.similarity is None and bedrooms.method == "missing"
    assert sum(c.weight for c in b.contributions if c.branch == "appointment") == pytest.approx(1)
    assert b.comparison_coverage == pytest.approx(1 - weights["bedrooms"] / active)
    # No coordinates: district_code fallback (provisional true) ...
    geo = next(c for c in scores.breakdown("fixture-12").contributions if c.feature == "geography")
    assert geo.method == "district_fallback" and geo.similarity == 1.0 and not geo.unknown  # both D21
    # ... or, without it, geography is not compared and a strict coverage gate drops the pair.
    strict = settings(**{"ranking.similarity.geography_district_fallback": False,
                         "ranking.similarity.min_comparison_coverage": 0.9})
    gated = pool_scores(rows, appointments=[("fixture-02", ["MANUAL_SENT"])], config=strict)
    b12 = gated.breakdown("fixture-12")
    assert b12.behavior_score is None and b12.comparison_coverage is None
    assert all(c.method.endswith("pair_unavailable") and c.weight == 0 for c in b12.contributions if c.branch == "appointment")
    assert gated.breakdown("fixture-01").behavior_score is not None
    # Missing and zero-second browsing records are not references; they are counted.
    counted = pool_scores(rows, browsing=[("fixture-01", None), ("fixture-03", 0.0), ("fixture-05", 12.0)])
    summary = counted.summary(10)["references"]["browsing"]
    assert counted.references["browsing"]["ids"] == ["fixture-05"]
    assert (summary["missing"], summary["zero_seconds"], summary["used_count"]) == (["fixture-01"], ["fixture-03"], 1)


def test_zero_variance_feature_is_dropped_without_division_by_zero():
    rows = [r.model_copy(update={"bedrooms": 2.0}) for r in fixture_rows()]
    scores = pool_scores(rows, browsing=[("fixture-01", 30)])
    assert scores.dropped == {"bedrooms": "zero_variance"} and "bedrooms" not in scores.features
    assert scores.spreads["bedrooms"] == 0
    assert np.isfinite(scores.fused.combined).all()
    assert scores.summary(10)["similarity"]["dropped_features"] == {"bedrooms": "zero_variance"}
    assert not any(c.feature == "bedrooms" for c in scores.breakdown("fixture-02").contributions)
    # The spread depends on numeric_scaling: mostly-equal values have iqr 0 but std > 0.
    values = np.array([2, 2, 2, 2, 2, 2, 2, 3], float)
    assert fit_spread(values, "iqr") == 0 and fit_spread(values, "std") > 0
    mostly = [r.model_copy(update={"bedrooms": 3.0 if r.listing_id == "fixture-09" else 2.0}) for r in fixture_rows()]
    # P7: explicit iqr fixture setting (the committed scaling became p05_p95; this pool's p05-p95
    # spread of bedrooms is positive too, so only iqr drops it).
    iqr = settings(**{"ranking.similarity.numeric_scaling": "iqr"})
    assert pool_scores(mostly, browsing=[("fixture-01", 30)], config=iqr).dropped == {"bedrooms": "zero_variance"}
    std = settings(**{"ranking.similarity.numeric_scaling": "std"})
    assert "bedrooms" in pool_scores(mostly, browsing=[("fixture-01", 30)], config=std).features
    # No known value at all is also dropped (non-finite spread).
    none = [r.model_copy(update={"floor_area_sqft": None}) for r in fixture_rows()]
    assert pool_scores(none, browsing=[("fixture-01", 30)]).dropped["floor_area_sqft"] == "zero_variance"


def test_fit_spread_pins_ddof_and_linear_quantiles():
    values = np.array([1.0, 2.0, 4.0, 8.0, np.nan, 100.0])
    finite = values[np.isfinite(values)]
    assert fit_spread(values, "std") == float(np.std(finite, ddof=0))
    q25, q75 = np.percentile(finite, (25, 75), method="linear")
    assert fit_spread(values, "iqr") == float(q75 - q25) == 6.0
    q05, q95 = np.percentile(finite, (5, 95), method="linear")
    assert fit_spread(values, "p05_p95") == float(q95 - q05)
    assert math.isnan(fit_spread(np.array([np.nan]), "iqr"))


@pytest.mark.parametrize("scaling", ["std", "iqr", "p05_p95"])
def test_unit_change_invariance(scaling):
    """Scaling a numeric attribute by a constant (e.g. sqm -> sqft) scales the fitted spread by
    the same constant, so every similarity is unchanged."""
    config = settings(**{"ranking.similarity.numeric_scaling": scaling})
    rows = fixture_rows()
    refs = dict(browsing=[("fixture-01", 30), ("fixture-06", 90)], appointments=[("fixture-09", ["CONFIRMED"])])
    base = pool_scores(rows, config=config, **refs)
    for factor, exact in ((2.0, True), (10.7639, False), (1 / 10.7639, False)):
        converted = [r.model_copy(update={"floor_area_sqft": None if r.floor_area_sqft is None else r.floor_area_sqft * factor,
                                          "price": None if r.price is None else r.price * factor})
                     for r in rows]
        other = pool_scores(converted, config=config, **refs)
        assert other.spreads["floor_area_sqft"] == pytest.approx(base.spreads["floor_area_sqft"] * factor, rel=1e-12)
        if exact:
            assert np.array_equal(other.sim, base.sim) and np.array_equal(other.fused.combined, base.fused.combined)
        else:
            assert np.allclose(other.sim, base.sim, rtol=0, atol=1e-12)
            assert np.allclose(other.fused.combined, base.fused.combined, rtol=0, atol=1e-12)


def test_self_pairs_are_excluded():
    rows = fixture_rows()[:9]
    alone = pool_scores(rows, browsing=[("fixture-01", 60)])
    assert alone.breakdown("fixture-01").behavior_score is None  # its own only reference
    assert all(alone.breakdown(i).behavior_score is not None for i in alone.ids if i != "fixture-01")
    assert not alone.fused.pair_available[alone.index["fixture-01"], alone.ref_column[alone.index["fixture-01"]]]
    two = pool_scores(rows, browsing=[("fixture-01", 60), ("fixture-03", 60)])
    b = two.breakdown("fixture-01")
    weights = dict(zip(two.features, map(float, two.weights.features)))
    s, _, _ = pair_similarity(rows[0], rows[2], weights, two.spreads, two.settings.ranking.similarity)
    assert b.browsing_score == pytest.approx(s)  # only the other reference counts
    assert {c.reference for c in b.contributions if c.branch == "browsing"} == {"fixture-03"}
    assert two.summary(10)["self_pairs_excluded"] == 2


def test_out_of_pool_references_are_ignored_and_counted(tmp_path):
    rows = fixture_rows()
    pool = [r for r in rows if r.listing_id != "fixture-11"]
    with_outside = pool_scores(pool, browsing=[("fixture-01", 30), ("fixture-11", 600)],
                               appointments=[("fixture-11", ["CONFIRMED"]), ("fixture-05", ["MANUAL_SENT"])])
    without = pool_scores(pool, browsing=[("fixture-01", 30)], appointments=[("fixture-05", ["MANUAL_SENT"])])
    assert np.array_equal(with_outside.fused.combined, without.fused.combined)
    refs = with_outside.summary(10)["references"]
    assert refs["browsing"]["out_of_pool"] == ["fixture-11"] and refs["browsing"]["out_of_pool_count"] == 1
    assert refs["appointment"]["out_of_pool"] == ["fixture-11"] and refs["appointment"]["used_count"] == 1
    # Service level: fixture-11 (price 5000) fails the hard budget, so it is outside the pool.
    service = make_service(tmp_path)
    try:
        first = service.recommend(request("out-1", "outside"))
        second = service.recommend(request("out-2", "outside", previous_state_id=first.state.state_id,
                                           feedback=feedback(appointments=[("fixture-11", ["CONFIRMED"])])))
        assert second.status == "success"
        assert all(i.scores.behavior_score is None for i in second.recommendations)
        appointment = rank_step(service, "outside", "out-2")["result"]["data"]["scoring"]["references"]["appointment"]
        assert appointment["out_of_pool"] == ["fixture-11"] and appointment["used_count"] == 0
    finally:
        service.close()


def test_suppression_follows_hard_criteria_only():
    rows = fixture_rows()[:9]
    soft_price = {"criterion_id": "p", "field": "price", "operator": "lte", "value": 2600, "unit": "SGD/month", "strength": "soft"}
    hard_price = {**soft_price, "strength": "hard"}
    assert "price" in pool_scores(rows, criteria(soft_price), browsing=[("fixture-01", 30)]).features
    hard = pool_scores(rows, criteria(hard_price), browsing=[("fixture-01", 30)])
    assert "price" not in hard.features and hard.suppressed == ["price"]
    every = settings(**{"ranking.similarity.suppression": "all_criteria"})
    assert "price" not in pool_scores(rows, criteria(soft_price), browsing=[("fixture-01", 30)], config=every).features


def test_weights_and_scales_are_visible_in_the_trace(tmp_path):
    service = make_service(tmp_path)
    config = service.config
    try:
        first = service.recommend(request("t1", "trace"))
        shown = [i.listing.listing_id for i in first.recommendations]
        second = service.recommend(request("t2", "trace", previous_state_id=first.state.state_id,
                                           feedback=feedback([(i, 45.0) for i in shown],
                                                             [("fixture-05", ["MANUAL_SENT"])], request_id="t1")))
        assert second.status == "success"
        trace = service.store.trace("trace", "t2")
        policy = trace["context"]["scoring_policy"]
        assert policy["weights"] == {"soft_weight": config.ranking.soft_weight, "behavior_weight": config.ranking.behavior_weight,
                                     "appointment_weight": config.ranking.behavior.appointment_weight,
                                     "browsing_weight": config.ranking.behavior.browsing_weight}
        assert policy["similarity"]["feature_weights"] == config.ranking.similarity.feature_weights.as_dict()
        scoring = rank_step(service, "trace", "t2")["result"]["data"]["scoring"]
        assert scoring["weights"]["soft_weight"] == config.ranking.soft_weight
        assert set(scoring["weights"]["features"]) == set(scoring["similarity"]["active_features"])
        assert scoring["similarity"]["spreads"]["floor_area_sqft"] > 0
        assert scoring["similarity"]["numeric_scaling"] == config.ranking.similarity.numeric_scaling
        assert scoring["similarity"]["geographic_scale_m"] == config.ranking.similarity.geographic_scale_m
        assert scoring["browsing"]["time_scale_seconds"] == config.ranking.browsing.time_scale_seconds
        assert scoring["browsing"]["duration_cap_seconds"] is None
        assert scoring["references"]["browsing"]["references"][0]["weight"] == pytest.approx(
            browsing_weight(45.0, config.ranking.browsing))
        # Item-level: scales on the contributions, the configured weights on the breakdown.
        contribution = next(c for item in second.recommendations for c in item.scores.contributions
                            if c.feature == "floor_area_sqft")
        assert contribution.scale == scoring["similarity"]["spreads"]["floor_area_sqft"]
        assert trace["configuration"] == config.to_data()
    finally:
        service.close()


def test_breakdowns_are_built_only_on_demand():
    rows = fixture_rows()[:9]
    scores = pool_scores(rows, browsing=[("fixture-01", 30)])
    assert isinstance(scores, PoolScores) and not hasattr(scores, "breakdowns")
    # Recombining under the base weights reproduces the base scores (stability extension point).
    again = scores.combine(scores.reweighted())
    assert np.array_equal(again.combined, scores.fused.combined)
    shifted = scores.combine(scores.reweighted(soft=0.0, behavior=1.0))
    assert not np.array_equal(shifted.combined, scores.fused.combined)


# --- timing (R10) --------------------------------------------------------------------------------
def synthetic_pool(n, seed=20261023):
    rng = np.random.default_rng(seed)
    types = ["condo", "hdb", "landed", "apartment"]
    rows = []
    for k in range(n):
        missing = rng.random(5) < 0.05
        rows.append(ListingSummary(
            listing_id=f"syn-{k:05d}", source="synthetic",
            price=None if missing[0] else float(rng.integers(800, 20000)),
            bedrooms=None if missing[1] else float(rng.integers(0, 6)),
            floor_area_sqft=None if missing[2] else float(rng.integers(250, 5000)),
            property_type=None if missing[3] else types[int(rng.integers(0, len(types)))],
            district_code=f"D{int(rng.integers(1, 29)):02d}",
            latitude=None if missing[4] else float(1.25 + rng.random() * 0.2),
            longitude=None if missing[4] else float(103.65 + rng.random() * 0.35),
        ))
    return rows


def test_timing_synthetic_21431_listing_pool_with_5_references(capsys):
    rows = synthetic_pool(21431)
    crit = criteria(HARD_BEDROOMS, {**SOFT_DISTRICT, "value": ["D05", "D09", "D10"]},
                    {**SOFT_CHEAP, "value": 5000})
    started = time.perf_counter()
    results = {r.listing_id: evaluate(r, crit, []) for r in rows}
    evaluate_s = time.perf_counter() - started
    fb = feedback([("syn-00001", 30.0), ("syn-00002", 300.0), ("syn-00003", 3000.0)],
                  [("syn-00004", ["MANUAL_SENT"]), ("syn-00005", ["CONFIRMED", "MANUAL_SENT"])])
    config = RecommendationConfig.default()
    started = time.perf_counter()
    scores = score_pool(rows, crit, results, {}, fb, config)
    breakdowns = [scores.breakdown(i) for i in scores.ranked[:config.trace.preview_rows]]
    scoring_s = time.perf_counter() - started
    assert len(scores.ranked) == len(rows) == 21431 and len(scores.refs) == 5
    assert np.isfinite(scores.fused.combined).all() and len(breakdowns) == config.trace.preview_rows
    with capsys.disabled():
        print(f"\n[P5 timing] synthetic pool 21431 x 5 refs: scoring {scoring_s:.3f} s "
              f"(score_pool {scores.duration_ms / 1000:.3f} s + {len(breakdowns)} breakdowns); evaluate() {evaluate_s:.3f} s")
    assert scoring_s < 3.0
