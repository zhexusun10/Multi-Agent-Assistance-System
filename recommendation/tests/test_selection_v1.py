"""P5 part 2: cold start, select-v1 selection, gates and replay (ranking/selection.py,
ranking/exploration.py; spec section 8, R3, R6, R9, R11)."""

import json
import math
import time
from collections import Counter

import numpy as np
import pytest

from recommendation.agent.controller import Completion, LiveController, ScriptedController
from recommendation.config import RecommendationConfig
from recommendation.contracts.errors import RecommendationError
from recommendation.contracts.request import RecommendationRequest
from recommendation.models.candidate import Response
from recommendation.models.listing_summary import ListingSummary
from recommendation.ranking import exploration, scoring_v2, selection
from recommendation.ranking.scoring_v2 import score_pool
from recommendation.repositories.connections import repositories
from recommendation.repositories.listing_repository import FixtureListingRepository
from recommendation.service import RecommendationService
from recommendation.state import pool_cache as pool_cache_module
from recommendation.state.store import StateStore
from recommendation.validation.gate import replay_gate
from recommendation.validation.rules import evaluate
from recommendation.validation.selection_replay import prefix_selection_input, replay_select

from .test_scoring_v2 import HARD_BEDROOMS, SOFT_CHEAP, SOFT_DISTRICT, feedback, synthetic_pool

POOL_KEY = "pool-key-under-test"
POOL_VERSION = "pool-version-under-test"
SOFT_D01 = {"criterion_id": "d01", "field": "district_code", "operator": "in", "value": ["D01"],
            "strength": "soft", "weight": 1}
SOFT_BR3 = {"criterion_id": "br3", "field": "bedrooms", "operator": "gte", "value": 3, "strength": "soft", "weight": 1}
SOFT_PRICE_MIN = {"criterion_id": "cheap", "field": "price", "operator": "lte", "value": 20000,
                  "unit": "SGD/month", "strength": "soft", "weight": 1, "utility": "minimize"}
PLAN = [SOFT_D01, SOFT_BR3]
# Explicit fixture settings (spec section 2): every expectation below is pinned to these values,
# not to the committed YAML. The soft/behaviour weights 0.6/0.4 are confirmed values and are read
# from the YAML; the select-v2 exploration window 30 and intermediate size 10 are confirmed
# values but are pinned here as well. The mpd-v1 threshold is pinned high (0.25) so that the synthetic plan
# pools (D 0.06-0.21) exercise cold-start exploration; NEAR_TIE pins the committed starting value
# 0.01, under which those pools are inactive (near-tie sampling / ranked).
EPSILON, WINDOW, THRESHOLD, TARGET, INTERMEDIATE, BINS = 0.02, 30, 0.25, 30, 10, 5
FIXTURE = {"selection.tie_max_score_span": EPSILON, "cold_start.detector.candidate_window": WINDOW,
           "cold_start.detector.min_mean_pairwise_difference": THRESHOLD,
           "cold_start.price_sampling.exploration_window": TARGET,
           "cold_start.price_sampling.intermediate_size": INTERMEDIATE, "cold_start.price_sampling.quantile_bins": BINS}
NEAR_TIE = {"cold_start.detector.min_mean_pairwise_difference": 0.01}


@pytest.fixture(autouse=True)
def fresh_memory():
    pool_cache_module.MEMORY.clear()
    yield
    pool_cache_module.MEMORY.clear()


def settings(**overrides):
    return RecommendationConfig.default().with_overrides({**FIXTURE, **overrides}, "selection test fixture")


def listing(k, district="D02", bedrooms=1.0, price=None, listing_id=None):
    return ListingSummary(
        listing_id=listing_id or f"L{k:03d}", source="synthetic", title=f"Synthetic {k}",
        price=float(1000 + 37 * k) if price is None else price, bedrooms=bedrooms,
        floor_area_sqft=600.0 + k, property_type="condo", district_code=district,
        latitude=1.30 + k * 1e-4, longitude=103.8,
    )


def plan_rows(leaders, last_tied, size):
    """Listings 1..leaders: both PLAN criteria (combined 0.6); leaders+1..last_tied: district only
    (0.3, exact ties); the rest: neither (0). Listing IDs follow k, so O follows k."""
    rows = []
    for k in range(1, size + 1):
        if k <= leaders:
            rows.append(listing(k, "D01", 3.0))
        elif k <= last_tied:
            rows.append(listing(k, "D01", 1.0))
        else:
            rows.append(listing(k, "D02", 1.0))
    return rows


def make_request(criteria=(), request_id="r-1", conversation="sel", limit=10, exited_key=None, fb=None):
    return RecommendationRequest.model_validate({
        "request_id": request_id, "conversation_id": conversation, "requirements_version": 1,
        "result_limit": limit, "requirements": {"listing_type": "RENT", "criteria": list(criteria)},
        "behavior_context": fb.model_dump(mode="json") if fb is not None else None,
        "cold_start": {"exited": exited_key is not None, "exited_pool_key": exited_key},
    })


def scored_pool(rows, req, config):
    crit = req.requirements.criteria
    results = {r.listing_id: evaluate(r, crit, []) for r in rows}
    return score_pool(rows, crit, results, {}, req.behavior_context, config)


def run(rows, criteria=(), config=None, **request_kw):
    config = config if config is not None else settings()
    req = make_request(criteria, **request_kw)
    scored = scored_pool(rows, req, config)
    return scored, selection.select(scored, req, config, POOL_KEY, POOL_VERSION)


def ids_of(rows):
    return [r.listing_id for r in rows]


# --- tie group --------------------------------------------------------------------------------
def test_tie_group_is_contiguous_span_limited_without_chaining():
    scores = [0.75, 0.625, 0.5, 0.375, 0.25]  # binary-exact differences
    assert selection.tie_group(scores, 3, 0.25) == (1, 4)  # |s - 0.5| <= 0.125
    assert selection.tie_group(scores, 3, 0.0) == (2, 3)  # only exact ties
    assert selection.tie_group([0.5] * 5, 2, 0.0) == (0, 5)
    # No chaining: 0.3125 is within epsilon/2 of 0.375 (a G member) but not of s_K = 0.4375.
    assert selection.tie_group([0.5, 0.4375, 0.375, 0.3125], 2, 0.125) == (0, 3)


def test_tie_group_boundary_is_evaluated_on_ieee_doubles_without_tolerance():
    # Decimal scores nominally exactly epsilon/2 away from s_K = 0.30: the comparison uses the
    # doubles as computed (documented pin in selection.tie_group), so a nominal boundary member can
    # fall on either side of the inclusive boundary.
    assert abs(0.31 - 0.30) > 0.01 and abs(0.30 - 0.29) > 0.01
    assert selection.tie_group([0.5, 0.31, 0.30, 0.29, 0.1], 3, 0.02) == (2, 3)  # both excluded
    assert abs(0.32 - 0.30) > 0.02 and abs(0.30 - 0.28) <= 0.02
    assert selection.tie_group([0.5, 0.32, 0.30, 0.28, 0.1], 3, 0.04) == (2, 4)  # 0.32 out, 0.28 in
    # Binary-exact differences sit exactly on the boundary and are included.
    assert selection.tie_group([0.5, 0.375, 0.25, 0.125, 0.0], 3, 0.25) == (1, 4)


# --- plan scenarios (exact ties; spec 13 P5, R9) -----------------------------------------------
@pytest.mark.parametrize("leaders,last_tied,size", [(3, 30, 40), (3, 20, 40)])
def test_plan_scenario_leaders_kept_and_seven_drawn_from_the_tie_group(leaders, last_tied, size):
    rows = plan_rows(leaders, last_tied, size)
    # Cold start inactive (D >= 0.01): near-tie sampling (no exploration).
    scored, chosen = run(rows, PLAN, config=settings(**NEAR_TIE))
    assert not chosen.summary.cold_start.active and chosen.summary.cold_start.evaluated
    group = ids_of(rows)[leaders:last_tied]
    assert chosen.summary.mode == "near_tie_sampling" and chosen.summary.leaders == 3
    assert chosen.ids[:3] == ["L001", "L002", "L003"] and len(chosen.ids) == 10
    drawn = chosen.ids[3:]
    assert len(drawn) == 7 and set(drawn) <= set(group) and drawn == [i for i in group if i in drawn]
    assert [chosen.reasons[i] for i in chosen.ids] == ["leader"] * 3 + ["tie_group_sample"] * 7
    tie = chosen.summary.tie_group
    assert (tie.size, tie.min_score, tie.max_score, tie.span, tie.epsilon) == (last_tied - 3, 0.3, 0.3, 0.0, EPSILON)
    assert chosen.summary.exploration is None
    # In cold start (D < THRESHOLD): the leaders are still kept, the other 7 come from price exploration.
    scored, cold = run(rows, PLAN)
    assert cold.summary.cold_start.active and cold.summary.mode == "cold_start_exploration"
    assert cold.ids[:3] == ["L001", "L002", "L003"]
    candidates = ids_of(rows)[3:3 + TARGET]  # R = max(|G|, min(TARGET, 40 - 3)) = TARGET items after L
    assert set(cold.ids[3:]) <= set(candidates) and len(cold.ids) == 10
    assert [cold.reasons[i] for i in cold.ids] == ["leader"] * 3 + ["price_exploration_sample"] * 7
    assert cold.summary.exploration.candidate_count == TARGET


def test_plan_scenario_all_tied_draws_ten_from_the_whole_pool():
    rows = [listing(k) for k in range(1, 41)]
    # All tied gives D = 0, below any positive threshold: only a disabled cold start samples near ties.
    _, chosen = run(rows, (), config=settings(**{"cold_start.enabled": False}))
    assert chosen.summary.mode == "near_tie_sampling" and chosen.summary.leaders == 0
    assert chosen.summary.tie_group.size == 40 and len(chosen.ids) == 10
    assert set(chosen.reasons.values()) == {"tie_group_sample"}
    assert chosen.ids == [i for i in ids_of(rows) if i in chosen.ids]
    assert len(chosen.selection_input["head"]) == 40  # whole pool stored when all tie (R6)


def test_plan_scenario_fully_distinct_selects_by_score_even_with_cold_start_enabled():
    rows = [listing(k, price=1000.0 * k) for k in range(1, 41)]
    scored, chosen = run(rows, [SOFT_PRICE_MIN], config=settings(**NEAR_TIE))
    # Around the cutoff, combined steps = soft_weight 0.6 x 1000 / 20000 = 0.03 > EPSILON / 2, so s_K
    # has no neighbour within EPSILON / 2 (prices above 20000 clamp to 0 far below the cutoff).
    assert (-np.diff(scored.fused.combined[scored.order][:11])).min() > EPSILON / 2
    cold = chosen.summary.cold_start
    assert cold.evaluated and not cold.active and cold.reason == "mean_difference_at_or_above_threshold"
    assert (cold.detector, cold.detector_version) == ("mean_pairwise_difference", "mpd-v1")
    assert cold.candidate_window == cold.evaluated_count == WINDOW and cold.threshold == 0.01
    assert cold.mean_pairwise_difference == selection.mean_pairwise_difference(
        scored.fused.combined[scored.order][:WINDOW].tolist()) >= cold.threshold
    assert chosen.summary.mode == "ranked" and chosen.ids == scored.ranked[:10] == ids_of(rows)[:10]
    assert set(chosen.reasons.values()) == {"ranked"} and chosen.summary.tie_group.size == 1


def test_plan_scenario_small_pool_returns_everything_in_score_order():
    rows = [listing(k, "D01" if k % 2 else "D02") for k in range(1, 8)]
    scored, chosen = run(rows, [SOFT_D01])
    assert chosen.summary.mode == "small_pool" and chosen.ids == scored.ranked and len(chosen.ids) == 7
    assert set(chosen.reasons.values()) == {"small_pool"} and chosen.summary.tie_group is None
    cold = chosen.summary.cold_start
    assert not cold.evaluated and cold.reason == "small_pool" and cold.detector == "mean_pairwise_difference"
    assert cold.mean_pairwise_difference is None and cold.evaluated_count is None
    _, empty = run([], [SOFT_D01])
    assert empty.summary.mode == "empty" and empty.ids == [] and empty.summary.cold_start.reason == "empty_pool"


# --- cold start diagnostics --------------------------------------------------------------------
def test_no_soft_requirements_and_all_tied_enters_price_exploration():
    rows = [listing(k) for k in range(1, 41)]
    _, chosen = run(rows, ())
    cold = chosen.summary.cold_start
    assert cold.evaluated and cold.active and cold.reason == "mean_difference_below_threshold"
    assert cold.mean_pairwise_difference == 0.0 and cold.evaluated_count == WINDOW  # window < pool of 40
    assert chosen.summary.mode == "cold_start_exploration" and len(chosen.ids) == 10
    explored = chosen.summary.exploration
    # select-v2: the window is the overall top TARGET; all tied, so no leader and every window item a candidate.
    assert (explored.window_size, explored.sampling_candidates, explored.intermediate_leaders) == (TARGET, TARGET, 0)
    assert sum(b.window_count for b in explored.bins) == sum(b.count for b in explored.bins) == TARGET
    assert sum(b.quota for b in explored.bins) == sum(b.sampled for b in explored.bins) == INTERMEDIATE
    assert explored.final_ids == explored.intermediate_ids == chosen.ids


def test_unresolved_data_is_not_treated_as_under_specification(tmp_path, monkeypatch):
    """An unresolved soft target contributes unknown utility (soft evidence coverage 0.5), the run is
    not turned into a clarification, the resolution problem is reported separately, and the
    cold-start status is still decided by D of the scores only."""
    unresolved = {"criterion_id": "near", "field": "distance", "operator": "lte", "value": 1500, "unit": "m",
                  "strength": "soft", "weight": 1, "utility": "proximity", "distance_type": "straight_line",
                  "destination_name": "Nowhere Imaginary Place"}
    listing_repo, geo_repo = repositories("fixture")
    service = RecommendationService(listing_repo, geo_repo, StateStore(tmp_path / "s.sqlite"), ScriptedController(),
                                    config=settings())
    seen = []
    original = selection.select

    def recording(scored, *args, **kwargs):
        chosen = original(scored, *args, **kwargs)
        seen.append((scored.fused.combined[scored.order].tolist(), chosen.summary.cold_start))
        return chosen

    monkeypatch.setattr(selection, "select", recording)
    try:
        for k, criteria in enumerate(([SOFT_DISTRICT, unresolved], [HARD_BEDROOMS, SOFT_DISTRICT, unresolved])):
            response = service.recommend(make_request(criteria, f"u-{k}", f"u-{k}", limit=3))
            assert response.status == "success" and response.clarifications == []
            assert [r.status for r in response.resolution] == ["unresolved"]
            cold = response.selection.cold_start
            assert cold.evaluated and cold.reason in ("mean_difference_below_threshold",
                                                      "mean_difference_at_or_above_threshold")
            # D comes from the official full-pool combined scores only (unknown utility is not dropped or
            # replaced by soft evidence coverage), and the status follows it.
            order, recorded = seen[-1]
            assert recorded == cold and cold.mean_pairwise_difference == selection.mean_pairwise_difference(order[:WINDOW])
            assert cold.evaluated_count == min(WINDOW, len(order)) and cold.active == (cold.mean_pairwise_difference < THRESHOLD)
            assert all(i.scores.soft_evidence_coverage == 0.5 for i in response.recommendations)
        assert response.pool.excluded_hard_unknown == 1  # fixture-10 (no bedrooms): outside the pool and D
    finally:
        service.close()


def test_mean_pairwise_difference_is_ginis_mean_difference():
    assert selection.mean_pairwise_difference([0.5] * 30) == 0.0  # equal scores
    assert selection.mean_pairwise_difference([0.5, 0.25]) == 0.25
    # Binary-exact: pairs |1-.5|, |1-.5|, |1-0|, |.5-.5|, |.5-0|, |.5-0| sum to 3.0 -> 2 * 3 / (4 * 3).
    assert selection.mean_pairwise_difference([1.0, 0.5, 0.5, 0.0]) == 0.5
    # Order-independent (math.fsum) and equal to the closed form on decimal scores.
    scores = [0.61, 0.6, 0.3, 0.2999, 0.0003, 0.0]
    brute = sum(abs(a - b) for a in scores for b in scores) / (len(scores) * (len(scores) - 1))
    assert math.isclose(selection.mean_pairwise_difference(scores), brute, rel_tol=1e-12)
    assert selection.mean_pairwise_difference(scores) == selection.mean_pairwise_difference(scores[::-1])
    # Fewer than two scores: no pair, no division by zero.
    assert selection.mean_pairwise_difference([0.5]) is None and selection.mean_pairwise_difference([]) is None


def test_detector_threshold_equality_is_inactive_without_tolerance():
    req = make_request(limit=1)
    at = settings(**{"cold_start.detector.min_mean_pairwise_difference": 0.25})
    cold, record = selection.evaluate_cold_start([0.5, 0.25], req, at, POOL_KEY)  # D == 0.25 exactly
    assert cold.evaluated and not cold.active and cold.mean_pairwise_difference == 0.25 == cold.threshold
    assert cold.reason == "mean_difference_at_or_above_threshold"
    above = settings(**{"cold_start.detector.min_mean_pairwise_difference": math.nextafter(0.25, 1)})
    cold, _ = selection.evaluate_cold_start([0.5, 0.25], req, above, POOL_KEY)
    assert cold.active and cold.reason == "mean_difference_below_threshold"
    below = settings(**{"cold_start.detector.min_mean_pairwise_difference": math.nextafter(0.25, 0)})
    assert not selection.evaluate_cold_start([0.5, 0.25], req, below, POOL_KEY)[0].active
    # Equal scores: D = 0 is below any positive threshold, never below 0.
    assert selection.evaluate_cold_start([0.3] * 12, req, settings(), POOL_KEY)[0].active
    zero = settings(**{"cold_start.detector.min_mean_pairwise_difference": 0.0})
    assert not selection.evaluate_cold_start([0.3] * 12, req, zero, POOL_KEY)[0].active
    # The configured window, threshold and detector identity are always reported; the request's
    # remembered-exit field is recorded as unused.
    assert (cold.detector, cold.detector_version, cold.pool_key) == ("mean_pairwise_difference", "mpd-v1", POOL_KEY)
    assert record["request_cold_start"] == {"exited": False, "exited_pool_key": None}
    assert record["request_cold_start_used"] is False


def test_detector_window_is_the_overall_top_scores_including_leaders():
    req = make_request()
    config = settings(**{"cold_start.detector.min_mean_pairwise_difference": 0.01})
    narrow = [0.9] + [0.5] * 28 + [0.49]  # 30 scores: leader 0.9 included
    cold, _ = selection.evaluate_cold_start(narrow + [0.0] * 10, req, config, POOL_KEY)
    assert cold.evaluated_count == cold.candidate_window == WINDOW
    assert cold.mean_pairwise_difference == selection.mean_pairwise_difference(narrow)
    # A 31st score outside the window never changes D; one inside it does.
    assert selection.evaluate_cold_start(narrow + [0.5] * 10, req, config, POOL_KEY)[0] == cold
    inside = selection.evaluate_cold_start(narrow[:-1] + [0.0] + [0.0] * 10, req, config, POOL_KEY)[0]
    assert inside.mean_pairwise_difference != cold.mean_pairwise_difference
    # Narrowly vs widely spread scores (no leader): below vs at or above 0.01.
    tight = [0.5 + 0.0001 * i for i in range(30)]
    wide = [0.05 * i for i in range(30)]
    assert selection.evaluate_cold_start(tight, req, config, POOL_KEY)[0].active
    assert not selection.evaluate_cold_start(wide, req, config, POOL_KEY)[0].active
    # Fewer eligible candidates than the window: all of them are used.
    cold, _ = selection.evaluate_cold_start([0.5] * 12, make_request(), config, POOL_KEY)
    assert cold.evaluated_count == 12 and cold.candidate_window == WINDOW
    # A smaller configured window takes only that many.
    small = settings(**{"cold_start.detector.candidate_window": 2,
                        "cold_start.detector.min_mean_pairwise_difference": 0.01})
    cold, _ = selection.evaluate_cold_start([0.5, 0.5, 0.0, 0.0], make_request(limit=3), small, POOL_KEY)
    assert cold.evaluated_count == 2 and cold.mean_pairwise_difference == 0.0 and cold.active


def test_detector_short_circuits_are_not_evaluated():
    config = settings()
    for scores, limit, reason in (([], 10, "empty_pool"), ([0.5] * 10, 10, "small_pool"), ([0.1, 0.9], 5, "small_pool")):
        cold, _ = selection.evaluate_cold_start(scores, make_request(limit=limit), config, POOL_KEY)
        assert not cold.evaluated and not cold.active and cold.reason == reason
        assert cold.mean_pairwise_difference is None and cold.evaluated_count is None
    disabled = settings(**{"cold_start.enabled": False})
    cold, _ = selection.evaluate_cold_start([0.5] * 40, make_request(), disabled, POOL_KEY)
    assert (cold.evaluated, cold.active, cold.reason) == (False, False, "cold_start_disabled")


def test_detector_input_is_the_unmodified_full_pool_score_order():
    rows = plan_rows(3, 30, 40)
    scored, chosen = run(rows, PLAN)
    order = scored.fused.combined[scored.order].tolist()
    cold = chosen.summary.cold_start
    assert cold.mean_pairwise_difference == selection.mean_pairwise_difference(order[:WINDOW])
    assert cold.evaluated_count == WINDOW and cold.threshold == THRESHOLD
    # The detector only reads the scores: the official order and scores are unchanged by it.
    assert scored.fused.combined[scored.order].tolist() == order


def test_d_is_request_independent_and_never_rescores(monkeypatch):
    rows = plan_rows(3, 30, 40)
    req_a = make_request(PLAN, "req-a")
    config = settings()
    scored = scored_pool(rows, req_a, config)
    # No weight-perturbation trials or rescoring at runtime: no PoolScores is built again and the stored
    # components are never recombined under other weights (the old trials called PoolScores.combine).
    monkeypatch.setattr(scoring_v2.PoolScores, "__init__", lambda *a, **k: pytest.fail("rescored"))
    monkeypatch.setattr(scoring_v2.PoolScores, "combine", lambda *a, **k: pytest.fail("reweighted"))
    monkeypatch.setattr(scoring_v2.PoolScores, "reweighted", lambda *a, **k: pytest.fail("reweighted"))
    first = selection.select(scored, req_a, config, POOL_KEY, POOL_VERSION)
    second = selection.select(scored, make_request(PLAN, "req-b", "other-conversation"), config, POOL_KEY, POOL_VERSION)
    assert first.summary.cold_start == second.summary.cold_start
    assert first.summary.seed != second.summary.seed  # the selection seed does depend on them
    assert "stability_seed" not in first.record["cold_start"] and "trials" not in first.record["cold_start"]
    assert not hasattr(selection, "top_k_stability") and not hasattr(selection, "stability_seed")


# --- exploration: bins and quotas --------------------------------------------------------------
def test_price_bins_full_pool_quantiles_duplicates_merged_last_bin_closed():
    prices = [1000.0] * 8 + [2000.0, 3000.0, None]
    edges = exploration.price_bin_edges(prices, 5)
    raw = np.quantile([p for p in prices if p is not None], np.linspace(0, 1, 6), method="linear")
    assert edges == sorted(set(map(float, raw))) and len(edges) < 6  # duplicates merged
    assert all(a < b for a, b in zip(edges, edges[1:]))
    n = exploration.numeric_bins(edges)
    assert exploration.bin_index(edges[0], edges) == 0
    assert exploration.bin_index(edges[1], edges) == 1  # [e_i, e_{i+1})
    assert exploration.bin_index(edges[-1], edges) == n - 1  # last bin closed
    assert exploration.bin_index(None, edges) == n  # null price: its own bin
    assert exploration.bin_bounds(n, edges) == (None, None)
    assert exploration.price_bin_edges([2500.0, 2500.0, None], 5) == [2500.0]
    assert exploration.bin_index(2500.0, [2500.0]) == 0 and exploration.bin_bounds(0, [2500.0]) == (2500.0, 2500.0)
    assert exploration.price_bin_edges([None, None], 5) == [] and exploration.bin_index(None, []) == 0
    with pytest.raises(ValueError):
        exploration.bin_index(99999.0, edges)


def test_price_bands_come_from_the_window_including_leaders_not_the_pool():
    rows = plan_rows(3, 20, 40)
    for k, r in enumerate(rows):  # leaders very expensive, the rest of the window cheap, the tail expensive
        price = 90000.0 + k if k < 3 else (1000.0 + k if k < TARGET else 50000.0 + k)
        rows[k] = r.model_copy(update={"price": price})
    rows[5] = rows[5].model_copy(update={"price": None})
    _, chosen = run(rows, PLAN)
    edges = chosen.selection_input["bin_edges"]
    window = [r.price for r in rows[:TARGET]]  # O follows k: the window is the first TARGET rows
    assert edges == exploration.price_bin_edges(window, BINS)  # leaders' prices included ...
    assert edges[-1] == 90002.0 and 50000.0 + TARGET not in edges  # ... the pool's tail never
    explored = chosen.summary.exploration
    bins = explored.bins
    assert bins[-1].lower is None and bins[-1].count == bins[-1].window_count == 1  # unknown-price band
    assert sum(b.window_count for b in bins) == TARGET and sum(b.count for b in bins) == TARGET - 3
    # Quotas are the largest-remainder allocation over the NON-leader counts per band (same explore stream),
    # never over the window counts: the leaders sit in the top band but do not take its slots.
    top = next(b for b in bins if b.upper == 90002.0)
    assert top.window_count == top.count + 3
    m = explored.intermediate_sampled
    expected = exploration.allocate([b.count for b in bins if b.count], m,
                                    selection.stream(chosen.selection_input["seeds"]["explore"]))
    assert [b.quota for b in bins if b.count] == expected
    assert set(explored.intermediate_ids[3:]) <= {r.listing_id for r in rows[3:TARGET]}  # only non-leaders


def test_quota_allocation_largest_remainder_with_caps_and_rng_ties():
    import random

    rng = random.Random(1)
    assert exploration.allocate([5, 3, 2], 7, rng) == [4, 2, 1]  # 3.5, 2.1, 1.4
    assert exploration.allocate([1, 10], 5, rng) == [0, 5]
    assert exploration.allocate([4, 6], 10, rng) == [4, 6] and exploration.allocate([0, 0], 0, rng) == [0, 0]
    with pytest.raises(ValueError):
        exploration.allocate([1, 1], 3, rng)
    # Exact remainder ties are broken by the RNG, not by bin order.
    winners = Counter(tuple(exploration.allocate([1, 1, 1], 2, random.Random(s))) for s in range(200))
    assert len(winners) == 3 and all(sum(w) == 2 for w in winners)
    # Totals, availability caps and proportionality on random inputs.
    gen = random.Random(7)
    for _ in range(500):
        counts = [gen.randint(0, 12) for _ in range(gen.randint(1, 7))]
        size = gen.randint(0, sum(counts))
        quotas = exploration.allocate(counts, size, random.Random(gen.random()))
        assert sum(quotas) == size and all(0 <= q <= c for q, c in zip(quotas, counts))
        if sum(counts):
            assert all(abs(q - size * c / sum(counts)) < 1 for q, c in zip(quotas, counts))


@pytest.mark.parametrize("leaders,last_tied,size,limit", [(3, 30, 40, 10), (3, 20, 40, 10), (0, 40, 40, 10),
                                                          (0, 60, 60, 10), (2, 9, 12, 5), (3, 30, 40, 1),
                                                          (3, 30, 40, 5)])
def test_exploration_window_intermediate_and_final_sizes(leaders, last_tied, size, limit):
    rows = plan_rows(leaders, last_tied, size)
    window, intermediate = min(TARGET, size), min(INTERMEDIATE, min(TARGET, size))
    for r in range(25):
        scored, chosen = run(rows, PLAN, limit=limit, request_id=f"q-{r}")
        assert chosen.summary.mode == "cold_start_exploration"
        explored = chosen.summary.exploration
        lead = explored.intermediate_leaders
        assert explored.window_size == explored.candidate_count == window
        assert explored.intermediate_limit == len(explored.intermediate_ids) == intermediate
        assert explored.intermediate_ids[:lead] == scored.ranked[:lead]  # leaders first, score order
        assert explored.sampling_candidates == window - lead == sum(b.count for b in explored.bins)
        assert sum(b.window_count for b in explored.bins) == window
        assert sum(b.quota for b in explored.bins) == sum(b.sampled for b in explored.bins) == intermediate - lead
        assert all(b.sampled == b.quota <= b.count for b in explored.bins)
        assert all(abs(b.quota - (intermediate - lead) * b.count / (window - lead)) < 1 for b in explored.bins)
        # Never drawn beyond the window; intermediate in score order; returned = its first result_limit.
        ranks = [scored.score_rank[i] for i in explored.intermediate_ids]
        assert ranks == sorted(ranks) and max(ranks) <= window and len(set(ranks)) == len(ranks)
        assert chosen.ids == explored.final_ids == explored.intermediate_ids[:limit]
        assert explored.final_leaders == min(lead, limit) == chosen.summary.leaders
        assert sum(b.final_sampled for b in explored.bins) == explored.final_sampled == len(chosen.ids) - min(lead, limit)


def test_inactive_cold_start_keeps_near_tie_sampling_and_remembered_exit_is_ignored():
    rows = plan_rows(3, 30, 40)
    _, inactive = run(rows, PLAN, config=settings(**NEAR_TIE))
    cold = inactive.summary.cold_start
    assert cold.evaluated and not cold.active and cold.reason == "mean_difference_at_or_above_threshold"
    assert inactive.summary.mode == "near_tie_sampling" and inactive.summary.exploration is None
    assert inactive.selection_input["cold_start_active"] is False and inactive.selection_input["bin_edges"] is None
    # A remembered exit (this pool or an older one) never bypasses the check: the status follows D.
    _, active = run(rows, PLAN)
    for key in (POOL_KEY, "an-older-pool-key"):
        _, exited = run(rows, PLAN, exited_key=key)
        assert exited.summary.cold_start == active.summary.cold_start and exited.summary.cold_start.active
        assert exited.summary.mode == "cold_start_exploration" and exited.ids == active.ids
        assert exited.record["cold_start"]["request_cold_start"] == {"exited": True, "exited_pool_key": key}
        assert exited.record["cold_start"]["request_cold_start_used"] is False
    # ...and it does not turn an inactive round active either.
    _, still = run(rows, PLAN, config=settings(**NEAR_TIE), exited_key=POOL_KEY)
    assert still.summary.cold_start == cold and still.summary.mode == "near_tie_sampling"
    # Cold start disabled: not evaluated, near-tie sampling still applies.
    _, disabled = run(rows, PLAN, config=settings(**{"cold_start.enabled": False}))
    assert disabled.summary.cold_start.reason == "cold_start_disabled"
    assert not disabled.summary.cold_start.evaluated and not disabled.summary.cold_start.active
    assert disabled.summary.mode == "near_tie_sampling"


def test_scoring_happens_before_sampling():
    rows = plan_rows(3, 20, 40)
    scored, chosen = run(rows, PLAN)
    assert len(scored.ranked) == 40 and chosen.summary.mode == "cold_start_exploration"
    head = chosen.selection_input["head"]
    order_scores = scored.fused.combined[scored.order].tolist()
    assert [h[0] for h in head] == scored.ranked[:len(head)] and [h[1] for h in head] == order_scores[:len(head)]
    assert chosen.ids[:3] == scored.ranked[:3]
    assert set(chosen.ids[3:]) <= set(scored.ranked[3:TARGET])  # drawn only from the window, after full scoring
    assert all(scored.score_rank[i] <= TARGET for i in chosen.ids)


# --- randomness: seeds, coverage of the tie group, no duplicates -------------------------------
def test_seeds_follow_r3():
    config = settings()
    seeds = selection.derive_seeds(config, "conv", "req", "pv")
    import hashlib

    base = hashlib.sha256(f"{config.selection.seed_namespace}|conv|req|pv|{config.selection_hash}".encode()).hexdigest()
    assert seeds["base"] == base and not seeds["override"]
    assert seeds["tie"] == hashlib.sha256(f"{base}|tie".encode()).hexdigest()
    assert set(seeds) == {"base", "tie", "explore", "override"} and seeds["explore"] != seeds["tie"]
    v1 = selection.derive_seeds(config, "conv", "req", "pv", streams=selection.STREAMS)  # select-v1 history
    assert len({v1["tie"], v1["stageA"], v1["stageB"]}) == 3 and v1["tie"] == seeds["tie"]
    assert selection.derive_seeds(config, "conv", "req2", "pv")["base"] != base
    changed = settings(**{"selection.tie_max_score_span": 0.03})
    assert selection.derive_seeds(changed, "conv", "req", "pv")["base"] != base  # selection_hash in the seed
    assert settings(**{"trace.preview_rows": 3}).selection_hash == config.selection_hash
    assert selection.derive_seeds(settings(**{"trace.preview_rows": 3}), "conv", "req", "pv")["base"] == base
    override = selection.derive_seeds(config, "conv", "req", "pv", override="fixed")
    assert override["override"] and override["base"] == selection.derive_seeds(config, "x", "y", "z", override="fixed")["base"]


@pytest.mark.parametrize("overrides", [{}, {"cold_start.enabled": False}])
def test_no_duplicates_and_nothing_outside_the_pool(overrides):
    config = settings(**overrides)
    for rows in (plan_rows(3, 30, 40), [listing(k) for k in range(1, 41)]):
        pool = set(ids_of(rows))
        for r in range(150):
            _, chosen = run(rows, PLAN, config=config, request_id=f"d-{r}")
            assert len(chosen.ids) == len(set(chosen.ids)) == 10 and set(chosen.ids) <= pool


def test_every_tie_group_member_is_selected_across_seeds_and_ids_are_not_fixed():
    rows = plan_rows(3, 30, 40)
    group = ids_of(rows)[3:30]
    counts, sets = Counter(), Counter()
    seeds = 400
    near_tie = settings(**NEAR_TIE)
    for r in range(seeds):
        _, chosen = run(rows, PLAN, config=near_tie, request_id=f"stat-{r}")
        drawn = chosen.ids[3:]
        counts.update(drawn)
        sets[tuple(drawn)] += 1
    expected = seeds * 7 / 27  # uniform without replacement: P(member drawn) = m / |G|
    sd = math.sqrt(seeds * (7 / 27) * (20 / 27))
    assert set(counts) == set(group)  # every member had a chance and was drawn
    assert all(abs(counts[i] - expected) < 5 * sd for i in group), counts
    # Not permanently the same IDs: the first 7 by listing ID (what an ID tie-break would always
    # return) almost never come out together, and nearly every draw is a different set.
    assert sets[tuple(group[:7])] <= 1 and len(sets) > seeds * 0.95
    # Cold start: every tie-group member is drawn at least once as well. (A candidate of R in a
    # price bin whose proportional share is below 1 and whose remainder is not among the largest
    # gets no quota in that pool: here L033 alone in the top bin, share 7/30. Known effect of
    # largest-remainder allocation, OPEN_ITEMS.)
    cold = Counter()
    for r in range(seeds):
        _, chosen = run(rows, PLAN, request_id=f"stat-{r}")
        cold.update(chosen.ids[3:])
    assert set(group) <= set(cold) and set(cold) <= set(ids_of(rows)[3:3 + TARGET]) and "L033" not in cold


# --- replay and gates --------------------------------------------------------------------------
# Draws for a fixed seed override, computed by the pre-change (HEAD 4d687e3) select_v1 from the same stored
# selection_input. Outside cold start select-v2 is select-v1, so the near-tie draws are unchanged; the
# select-v1 cold-start draws must still replay from a stored select-v1 input (history).
GOLDEN_DRAWS = {
    "cold_start_exploration": ["L001", "L002", "L003", "L008", "L009", "L012", "L018", "L019", "L027", "L031"],
    "near_tie_sampling": ["L001", "L002", "L003", "L011", "L017", "L018", "L019", "L025", "L026", "L027"],
}
# select-v2 cold-start draws for the same scenario and seed (pinned: replay must reproduce them).
GOLDEN_V2_COLD = ["L001", "L002", "L003", "L004", "L012", "L017", "L020", "L022", "L025", "L028"]


def test_near_tie_sampling_is_unchanged_for_equivalent_selection_inputs():
    config = settings(**NEAR_TIE)
    req = make_request(PLAN, "golden", "golden")
    scored = scored_pool(plan_rows(3, 30, 40), req, config)
    chosen = selection.select(scored, req, config, POOL_KEY, POOL_VERSION, seed_override="fixed")
    assert chosen.summary.mode == "near_tie_sampling" and chosen.ids == GOLDEN_DRAWS["near_tie_sampling"]
    assert chosen.summary.algorithm_version == "select-v2"
    assert replay_select(json.loads(json.dumps(chosen.selection_input))).ids == GOLDEN_DRAWS["near_tie_sampling"]
    # The same head under select-v1 gives the same draws.
    v1 = selection.select_v1(chosen.selection_input["head"], 40, 10, EPSILON, False, None, None,
                             chosen.selection_input["seeds"])
    assert v1.ids == chosen.ids and v1.reasons == chosen.reasons


def v1_selection_input(scored, request, config, seed_override):
    """A select-v1 selection_input as the pre-select-v2 code stored it (history fixture)."""
    import platform

    n, limit, epsilon = len(scored.ids), request.result_limit, config.selection.tie_max_score_span
    scores = scored.fused.combined[scored.order].tolist()
    prices = [scored.rows[p].price for p in scored.order]
    start, end = selection.tie_group(scores, min(limit, n), epsilon)
    length = start + max(end - start, min(TARGET, n - start))
    return {"algorithm_version": "select-v1", "python_version": platform.python_version(), "pool_size": n,
            "result_limit": limit, "K": min(limit, n), "epsilon": epsilon, "target_sample_size": TARGET,
            "quantile_bins": BINS, "cold_start_active": True,
            "head": [[scored.ranked[p], scores[p], prices[p]] for p in range(length)],
            "bin_edges": exploration.price_bin_edges(prices, BINS),
            "seeds": selection.derive_seeds(config, "golden", "golden", POOL_VERSION, seed_override,
                                            streams=selection.STREAMS)}


def test_stored_select_v1_inputs_replay_under_select_v1_semantics():
    config = settings()
    req = make_request(PLAN, "golden", "golden")
    scored = scored_pool(plan_rows(3, 30, 40), req, config)
    stored = json.loads(json.dumps(v1_selection_input(scored, req, config, "fixed")))
    replayed = replay_select(stored)
    assert replayed.mode == "cold_start_exploration" and replayed.ids == GOLDEN_DRAWS["cold_start_exploration"]
    # The new runs of the same scenario use select-v2: deterministic, and replayable from their own input.
    chosen = selection.select(scored, req, config, POOL_KEY, POOL_VERSION, seed_override="fixed")
    again = selection.select(scored, req, config, POOL_KEY, POOL_VERSION, seed_override="fixed")
    assert chosen.summary.algorithm_version == "select-v2" and chosen.ids == again.ids
    assert replay_select(json.loads(json.dumps(chosen.selection_input))).ids == chosen.ids
    if GOLDEN_V2_COLD is not None:
        assert chosen.ids == GOLDEN_V2_COLD


def test_replay_recomputes_select_v1_from_the_stored_input_only():
    near_tie = {"config": settings(**NEAR_TIE)}
    for rows, kwargs in ((plan_rows(3, 30, 40), {}), (plan_rows(3, 30, 40), near_tie),
                         ([listing(k) for k in range(1, 41)], {}), ([listing(k) for k in range(1, 8)], {}),
                         ([listing(k, price=1000.0 * k) for k in range(1, 41)], near_tie)):
        criteria = [SOFT_PRICE_MIN] if rows[1].price == 2000.0 else PLAN
        _, chosen = run(rows, criteria, **kwargs)
        stored = json.loads(json.dumps(chosen.selection_input))  # as stored in the run snapshot
        replayed = replay_select(stored)
        assert replayed.ids == chosen.ids and replayed.mode == chosen.summary.mode
        assert replayed.reasons == chosen.reasons
        assert set(stored) == {"algorithm_version", "python_version", "pool_size", "result_limit", "K", "epsilon",
                               "exploration_window", "intermediate_size", "quantile_bins", "cold_start_active",
                               "head", "bin_edges", "seeds"}
    for broken in ({"K": 9}, {"head": stored["head"][::-1]}, {"head": stored["head"][:-1]},
                   {"algorithm_version": "select-v0"}):
        with pytest.raises(RecommendationError):
            replay_select({**stored, **broken})


# --- service level (fixture data, scripted controller) -----------------------------------------
def synthetic_service(tmp_path, rows, config=None, controller=None, name="state.sqlite"):
    config = config if config is not None else settings()
    path = tmp_path / "listings.json"
    path.write_text(json.dumps([r.model_dump(exclude={"source", "normalization_warnings"}) for r in rows]))
    _, geo = repositories("fixture")
    return RecommendationService(FixtureListingRepository(path), geo, StateStore(tmp_path / name),
                                 controller or ScriptedController(), config=config)


def rank_step(service, conversation, request_id):
    steps = service.store.trace(conversation, request_id)["steps"]
    return [s for s in steps if s.get("result", {}).get("tool") == "rank_candidates"][-1]


def test_service_round_trip_cold_start_follows_d_and_ignores_the_exit_input(tmp_path):
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        first = service.recommend(make_request(PLAN, "e2e-1", "e2e"))
        assert first.status == "success" and len(first.recommendations) == 10
        sel = first.selection
        assert sel.mode == "cold_start_exploration" and sel.algorithm_version == first.execution.selection_algorithm == "select-v2"
        assert len(sel.seed) == 64 and sel.result_limit == 10 and sel.leaders == 3
        assert [i.selection_reason for i in first.recommendations] == ["leader"] * 3 + ["price_exploration_sample"] * 7
        assert [i.rank for i in first.recommendations] == list(range(1, 11))
        assert [i.score_rank for i in first.recommendations] == sorted(i.score_rank for i in first.recommendations)
        assert all(i.selection_note for i in first.recommendations)
        cold = sel.cold_start
        assert cold.evaluated and cold.active and cold.pool_key == first.pool.pool_key
        assert (cold.detector, cold.detector_version, cold.candidate_window, cold.evaluated_count) == \
            ("mean_pairwise_difference", "mpd-v1", WINDOW, WINDOW)
        assert cold.threshold == THRESHOLD and cold.mean_pairwise_difference < THRESHOLD
        assert sel.tie_group.size == 27 and sel.exploration.candidate_count == TARGET and len(sel.exploration.bins) == BINS
        step = rank_step(service, "e2e", "e2e-1")
        assert "selection" not in step["result"]["data"]  # the rank tool never selects
        selection = next(s for s in service.store.trace("e2e", "e2e-1")["steps"] if s["kind"] == "selection")
        record = selection["record"]
        assert step["result"]["data"]["scoring"]["pool_scored"] == first.pool.size == 40
        assert record["mode"] == sel.mode and record["cold_start"]["duration_ms"] >= 0
        assert record["cold_start"]["request_cold_start_used"] is False and record["duration_ms"] >= 0
        assert step["result"]["ranking_artifact_id"] == selection["ranking_artifact_id"]
        assert selection["selected_ids"] == [i.listing.listing_id for i in first.recommendations]
        trace = service.store.trace("e2e", "e2e-1")
        assert trace["context"]["selection_policy"]["algorithm_version"] == "select-v2"
        gate = [s for s in trace["steps"] if s["kind"] == "final_gate"][-1]
        assert gate["passed"] and gate["selection"]["mode"] == sel.mode
        snapshot = service.store.current("e2e")
        assert snapshot["selection_input"]["seeds"]["base"] == sel.seed
        # A stale remembered exit for this pool_key (sent by an older orchestrator) is accepted and
        # ignored: D is unchanged, so the second round is still in cold-start exploration.
        second = service.recommend(make_request(PLAN, "e2e-2", "e2e", exited_key=first.pool.pool_key)
                                   .model_copy(update={"previous_state_id": first.state.state_id}))
        assert second.status == "success" and second.pool.cache == "reused"
        assert second.selection.mode == "cold_start_exploration" and second.selection.cold_start == cold
        assert [i.selection_reason for i in second.recommendations] == ["leader"] * 3 + ["price_exploration_sample"] * 7
        second_selection = next(s for s in service.store.trace("e2e", "e2e-2")["steps"] if s["kind"] == "selection")
        assert second_selection["record"]["cold_start"]["request_cold_start"] == {
            "exited": True, "exited_pool_key": first.pool.pool_key}
    finally:
        service.close()


def test_later_round_reactivates_when_its_differentiation_is_low(tmp_path):
    """Status is derived from the current D every round: round 1 is distinguishable (inactive),
    round 2 on a pool whose top scores are tied (after a data rebuild under the same pool_key, with
    a stale remembered exit) is active again."""
    rows = [listing(k, price=1000.0 * k) for k in range(1, 41)]
    service = synthetic_service(tmp_path, rows, config=settings(**NEAR_TIE))
    try:
        first = service.recommend(make_request([SOFT_PRICE_MIN], "dv-1", "dv"))
        assert first.selection.cold_start.evaluated and not first.selection.cold_start.active
        assert first.selection.mode == "ranked"
        tied = [r.model_copy(update={"price": 1000.0}) for r in service.listing.rows]
        service.listing.rows[:] = tied
        second = service.recommend(make_request([SOFT_PRICE_MIN], "dv-2", "dv", exited_key=first.pool.pool_key)
                                   .model_copy(update={"previous_state_id": first.state.state_id}))
        assert second.pool.cache == "built" and second.pool.data_version != first.pool.data_version
        assert second.pool.pool_key == first.pool.pool_key
        cold = second.selection.cold_start
        assert cold.active and cold.mean_pairwise_difference == 0.0 and cold.reason == "mean_difference_below_threshold"
        assert second.selection.mode == "cold_start_exploration"
    finally:
        service.close()


def test_stored_legacy_cold_start_run_replays_as_stored(tmp_path):
    """A run stored before mpd-v1 (legacy C/B/S selection.cold_start, configuration with the removed keys):
    resending the same request returns the stored response unchanged through the immutable replay and
    replay_gate, which recompute only from the stored selection_input. C/B/S is never read as D."""
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        request = make_request(PLAN, "lg-1", "lg")
        first = service.recommend(request)
        assert first.selection.mode == "cold_start_exploration"
        legacy = {"evaluated": True, "active": True, "reason": "b_above_max; s_below_min", "coverage_c": 1.0,
                  "boundary_group_b": 27, "stability_s": 0.87, "thresholds": {"c": 0.8, "b": 5, "s": 0.9},
                  "exit_conditions_met": False, "pool_key": first.pool.pool_key}
        stored = first.model_dump(mode="json")
        stored["selection"]["cold_start"] = legacy
        snapshot = service.store.current("lg")
        cold = snapshot["configuration"]["cold_start"]
        del cold["detector"]
        cold.update({"reenter_on_pool_rebuild": True, "min_evidence_coverage": 0.8, "max_boundary_tie_group_size": 5,
                     "min_top_k_stability": 0.9, "stability": {"trials": 20, "weight_perturbation_fraction": 0.2}})
        service.store.db.execute("UPDATE runs_v2 SET response=?, snapshot=? WHERE conversation_id=? AND request_id=?",
                                 (json.dumps(stored), json.dumps(snapshot), "lg", "lg-1"))
        service.store.db.commit()
        again = service.recommend(request)
        assert again.model_dump(mode="json") == stored  # byte-for-byte the stored legacy response
        assert again.selection.cold_start.coverage_c == 1.0 and not hasattr(again.selection.cold_start, "detector")
        assert [i.listing.listing_id for i in again.recommendations] == [i.listing.listing_id for i in first.recommendations]
        assert service.store.trace("lg")["steps"][0]["mode"] == "immutable_replay"
    finally:
        service.close()


def test_same_request_replays_identically_and_tampering_is_rejected(tmp_path):
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        request = make_request(PLAN, "rp-1", "rp")
        first = service.recommend(request)
        again = service.recommend(request)
        assert again == first
        trace = service.store.trace("rp")
        assert trace["steps"][0]["mode"] == "immutable_replay"
        snapshot = service.store.current("rp")
        assert replay_select(snapshot["selection_input"]).ids == [i.listing.listing_id for i in first.recommendations]
        replay_gate(first, snapshot)
        data = first.model_dump(mode="json")
        for path, value in ((("selection", "mode"), "near_tie_sampling"), (("selection", "seed"), "0" * 64)):
            tampered = json.loads(json.dumps(data))
            tampered[path[0]][path[1]] = value
            with pytest.raises(RecommendationError):
                replay_gate(Response.model_validate(tampered), snapshot)
        reason = json.loads(json.dumps(data))
        reason["recommendations"][0]["selection_reason"] = "ranked"
        with pytest.raises(RecommendationError):
            replay_gate(Response.model_validate(reason), snapshot)
        for change in ({"cold_start_active": False}, {"seeds": {**snapshot["selection_input"]["seeds"],
                                                               "explore": "f" * 64}}):
            broken = json.loads(json.dumps(snapshot))
            broken["selection_input"].update(change)
            with pytest.raises(RecommendationError):
                replay_gate(first, broken)
    finally:
        service.close()


def test_new_request_can_resample_with_the_same_inputs(tmp_path):
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        previous, selections, spreads = None, set(), set()
        for r in range(6):
            body = make_request(PLAN, f"rs-{r}", "rs").model_copy(update={"previous_state_id": previous})
            response = service.recommend(body)
            previous = response.state.state_id
            selections.add(tuple(i.listing.listing_id for i in response.recommendations))
            spreads.add(response.selection.cold_start.mean_pairwise_difference)
            assert [i.listing.listing_id for i in response.recommendations][:3] == ["L001", "L002", "L003"]
        assert len(selections) > 1 and len(spreads) == 1
    finally:
        service.close()


def test_config_change_requires_a_new_request(tmp_path):
    rows = plan_rows(3, 30, 40)
    service = synthetic_service(tmp_path, rows)
    request = make_request(PLAN, "cfg-1", "cfg")
    try:
        original = service.recommend(request)
    finally:
        service.close()
    changed = settings(**{"selection.tie_max_score_span": 0.05})
    service = synthetic_service(tmp_path, rows, config=changed)
    try:
        assert service.recommend(request) == original  # same request: the stored result
        fresh = service.recommend(make_request(PLAN, "cfg-2", "cfg").model_copy(
            update={"previous_state_id": original.state.state_id}))
        assert fresh.execution.selection_hash == changed.selection_hash != original.execution.selection_hash
        assert fresh.selection.seed == selection.derive_seeds(changed, "cfg", "cfg-2", fresh.pool.pool_version)["base"]
        assert fresh.selection.seed != selection.derive_seeds(settings(), "cfg", "cfg-2",
                                                              fresh.pool.pool_version)["base"]
    finally:
        service.close()


def test_final_gate_rejects_the_score_prefix_when_it_is_not_the_selection(tmp_path, monkeypatch):
    from recommendation.tools.dispatcher import Dispatcher

    original = Dispatcher.select_ranking

    def prefix(self, artifact_id):
        original(self, artifact_id)
        self.selected = self.ranked[: self.request.result_limit]  # the score prefix, not the selection
        return self.selected

    monkeypatch.setattr(Dispatcher, "select_ranking", prefix)
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        response = service.recommend(make_request(PLAN, "px-1", "px"))
        assert response.status == "error" and response.error.code == "INVALID_SELECTION"
    finally:
        service.close()


def test_live_controller_returns_the_recorded_selection(tmp_path):
    class Model(LiveController):
        turn = 0

        def invoke(self, messages, tools, remaining):
            self.turn += 1
            ref = json.loads(messages[1]["content"])["context_ref"]
            if self.turn == 1:
                return {"content": "", "tool_calls": [
                    {"id": str(i), "name": n, "args": {"context_ref": ref}}
                    for i, n in enumerate(("resolve_entities", "build_pool", "get_geographic_evidence",
                                           "rank_candidates"))]}
            artifact = json.loads(messages[-1]["content"])["ranking_artifact_id"]
            return {"content": json.dumps({"ranking_artifact_id": artifact}), "tool_calls": []}

    service = synthetic_service(tmp_path, plan_rows(3, 30, 40), controller=Model())
    try:
        response = service.recommend(make_request(PLAN, "lv-1", "lv"))
        assert response.status == "success" and response.selection.mode == "cold_start_exploration"
        selection = next(s for s in service.store.trace("lv")["steps"] if s["kind"] == "selection")
        assert [i.listing.listing_id for i in response.recommendations] == selection["selected_ids"]
        assert selection["ranking_artifact_id"] == rank_step(service, "lv", "lv-1")["result"]["ranking_artifact_id"]
    finally:
        service.close()


def test_ranked_prefix_v1_snapshots_still_replay(tmp_path):
    listing_repo, geo_repo = repositories("fixture")
    service = RecommendationService(listing_repo, geo_repo, StateStore(tmp_path / "s.sqlite"), ScriptedController(),
                                    config=settings())
    try:
        budget = {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 3000,
                  "unit": "SGD/month", "strength": "hard"}
        response = service.recommend(make_request([budget, HARD_BEDROOMS, SOFT_DISTRICT], "old-1", "old"))
        assert response.selection.mode == "small_pool"
        snapshot = service.store.current("old")
        items = response.recommendations
        older = {**snapshot, "selection_input": prefix_selection_input(
            [i.listing.listing_id for i in items], {i.listing.listing_id: i.scores for i in items},
            {i.listing.listing_id: i.listing for i in items}, 10)}
        assert replay_select(older["selection_input"]).mode is None
        replay_gate(response, older)
    finally:
        service.close()


# --- timing (R10: scoring + diagnostics + selection) -------------------------------------------
def test_timing_full_pipeline_on_synthetic_21431_listing_pool(capsys):
    """R10 budget: scoring + diagnostics + selection < 3 s. The per-listing criterion evaluation
    (validation/rules.evaluate over the whole pool, which rank_candidates also runs before soft
    scoring) is measured separately and the end-to-end figure including it is asserted as well."""
    rows = synthetic_pool(21431)
    config = settings()
    fb = feedback([("syn-00001", 30.0), ("syn-00002", 300.0), ("syn-00003", 3000.0)],
                  [("syn-00004", ["MANUAL_SENT"]), ("syn-00005", ["CONFIRMED", "MANUAL_SENT"])])
    cases = {
        "soft+behaviour": ([HARD_BEDROOMS, {**SOFT_DISTRICT, "value": ["D05", "D09", "D10"]}, {**SOFT_CHEAP, "value": 5000}], fb),
        "no soft, all tied": ([], None),
    }
    measured = {}
    for name, (criteria, case_feedback) in cases.items():
        req = make_request(criteria, "timing", fb=case_feedback)
        context = req.behavior_context
        refs = len(context.book_viewing_listings) + len(context.listing_view_durations) if context else 0
        crit = req.requirements.criteria
        evaluate_started = time.perf_counter()
        results = {r.listing_id: evaluate(r, crit, []) for r in rows}
        started = time.perf_counter()
        scored = score_pool(rows, crit, results, {}, req.behavior_context, config)
        scored_at = time.perf_counter()
        chosen = selection.select(scored, req, config, POOL_KEY, POOL_VERSION)
        breakdowns = [scored.breakdown(i) for i in dict.fromkeys(chosen.ids + scored.ranked[:config.trace.preview_rows])]
        total = time.perf_counter() - started
        evaluate_s = started - evaluate_started
        replay_started = time.perf_counter()
        assert replay_select(json.loads(json.dumps(chosen.selection_input))).ids == chosen.ids
        replay_s = time.perf_counter() - replay_started
        assert len(chosen.ids) == 10 and breakdowns and chosen.summary.cold_start.evaluated
        measured[name] = (refs, total, evaluate_s, scored_at - started, chosen.record["cold_start"]["duration_ms"] / 1000,
                          chosen.record["duration_ms"] / 1000, replay_s, chosen.summary.mode,
                          len(chosen.selection_input["head"]))
        assert total < 3.0  # R10 scope
        assert evaluate_s + total < 3.0  # including the per-listing criterion evaluation
    assert [m[0] for m in measured.values()] == [5, 0]
    with capsys.disabled():
        for name, (refs, total, evaluate_s, scoring_s, diag_s, select_s, replay_s, mode, head) in measured.items():
            print(f"\n[P5 timing] 21431 x {refs} refs, {name}: total {total:.3f} s (scoring {scoring_s:.3f} s, "
                  f"select {select_s:.3f} s incl. diagnostics {diag_s:.3f} s), criterion evaluation {evaluate_s:.3f} s "
                  f"(end-to-end {evaluate_s + total:.3f} s), mode {mode}, head {head}, replay {replay_s:.3f} s")


# --- acceptance companion: re-sent feedback under select-v1 ------------------------------------
def test_acceptance_duplicate_feedback_tolerates_resampling_on_a_pool_larger_than_the_limit(tmp_path, monkeypatch):
    """terminal/acceptance.py duplicate_feedback re-sends the same snapshot under a NEW request_id:
    sampled items may differ (new seed, R3); only request-independent outputs must be equal."""
    from recommendation.contracts.requirements import Requirements
    from recommendation.terminal import acceptance

    path = tmp_path / "listings.json"
    path.write_text(json.dumps([r.model_dump(exclude={"source", "normalization_warnings"})
                                for r in plan_rows(3, 30, 40)]))
    _, geo = repositories("fixture")
    monkeypatch.setattr(acceptance, "repositories", lambda mode: (FixtureListingRepository(path), geo))
    report = acceptance.run_acceptance(tmp_path / "out", Requirements.model_validate({"criteria": PLAN}),
                                       data_mode="fixture", controller_mode="scripted", config=settings())
    step = report["steps"]["duplicate_feedback"]
    assert step["status"] == "passed", step
    compared = step["compared"]
    assert compared["differences"] == [] and compared["seeds"][0] != compared["seeds"][1]
    assert set(compared["selection_modes"]) <= {"cold_start_exploration", "near_tie_sampling"}
    responses = [Response.model_validate_json((tmp_path / "out" / f"{name}.response.json").read_text())
                 for name in ("feedback", "duplicate_feedback")]
    assert responses[0].pool.size == 40 > len(responses[0].recommendations)


def test_resent_feedback_comparison_ignores_resampled_items_but_not_score_changes(tmp_path):
    from recommendation.terminal.acceptance import resent_feedback_differences, scoring_summary

    service = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        first = service.recommend(make_request(PLAN, "dup-a", "dup"))
        fb = feedback([(i.listing.listing_id, 90.0 if i.rank == 1 else 30.0) for i in first.recommendations],
                      request_id="dup-a")
        a = service.recommend(make_request(PLAN, "dup-b", "dup", fb=fb).model_copy(
            update={"previous_state_id": first.state.state_id}))
        b = service.recommend(make_request(PLAN, "dup-c", "dup", fb=fb).model_copy(
            update={"previous_state_id": a.state.state_id}))
        ids = [[i.listing.listing_id for i in r.recommendations] for r in (a, b)]
        assert a.selection.mode == b.selection.mode == "cold_start_exploration" and ids[0] != ids[1]
        assert ids[0][:a.selection.leaders] == ids[1][:b.selection.leaders]
        summaries = [scoring_summary(service.store.trace("dup", r.request_id)) for r in (a, b)]
        assert summaries[0]["references"]["browsing"]["used_count"] == 10 and summaries[0] == summaries[1]
        # The previous (prefix) check reported this legitimate resample as accumulation.
        assert resent_feedback_differences(a, b, *summaries) == []
        # A changed score of a listing present in both responses is reported.
        shared = next(i for i in b.recommendations if i.listing.listing_id in ids[0])
        data = b.model_dump(mode="json")
        item = next(i for i in data["recommendations"] if i["listing"]["listing_id"] == shared.listing.listing_id)
        item["scores"]["combined_score"] = max(0.0, item["scores"]["combined_score"] - 0.01)
        assert resent_feedback_differences(a, Response.model_validate(data)) == [
            f"scores of {shared.listing.listing_id}"]
        changed = {**summaries[1], "references": {}}
        assert resent_feedback_differences(a, b, summaries[0], changed) == ["scoring summary"]
    finally:
        service.close()
