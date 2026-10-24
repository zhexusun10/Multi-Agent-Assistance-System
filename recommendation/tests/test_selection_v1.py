"""P5 part 2: cold start, select-v1 selection, gates and replay (ranking/selection.py,
ranking/exploration.py; spec section 8, R3, R6, R9, R11)."""

import json
import math
import time
from collections import Counter

import numpy as np
import pytest

from recommendation.agent.controller import LiveController, Proposal, ScriptedController
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
# not to the committed YAML, whose provisional values P7 replaces with calibrated ones. The C/B/S
# thresholds 0.8/5/0.9 and the soft/behaviour weights 0.6/0.4 are confirmed values and are read
# from the YAML; target_sample_size 30 is confirmed as the initial experimental value but is
# pinned here as well.
EPSILON, TRIALS, FRACTION, TARGET, BINS = 0.02, 20, 0.2, 30, 5
FIXTURE = {"selection.tie_max_score_span": EPSILON, "cold_start.stability.trials": TRIALS,
           "cold_start.stability.weight_perturbation_fraction": FRACTION,
           "cold_start.price_sampling.target_sample_size": TARGET, "cold_start.price_sampling.quantile_bins": BINS}


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
    # After the orchestrator recorded the exit for this pool: near-tie sampling (no exploration).
    scored, chosen = run(rows, PLAN, exited_key=POOL_KEY)
    group = ids_of(rows)[leaders:last_tied]
    assert chosen.summary.mode == "near_tie_sampling" and chosen.summary.leaders == 3
    assert chosen.ids[:3] == ["L001", "L002", "L003"] and len(chosen.ids) == 10
    drawn = chosen.ids[3:]
    assert len(drawn) == 7 and set(drawn) <= set(group) and drawn == [i for i in group if i in drawn]
    assert [chosen.reasons[i] for i in chosen.ids] == ["leader"] * 3 + ["tie_group_sample"] * 7
    tie = chosen.summary.tie_group
    assert (tie.size, tie.min_score, tie.max_score, tie.span, tie.epsilon) == (last_tied - 3, 0.3, 0.3, 0.0, EPSILON)
    assert chosen.summary.exploration is None
    # In cold start (B = |G| > 5): the leaders are still kept, the other 7 come from price exploration.
    scored, cold = run(rows, PLAN)
    assert cold.summary.cold_start.active and cold.summary.mode == "cold_start_exploration"
    assert cold.ids[:3] == ["L001", "L002", "L003"]
    candidates = ids_of(rows)[3:3 + TARGET]  # R = max(|G|, min(TARGET, 40 - 3)) = TARGET items after L
    assert set(cold.ids[3:]) <= set(candidates) and len(cold.ids) == 10
    assert [cold.reasons[i] for i in cold.ids] == ["leader"] * 3 + ["price_exploration_sample"] * 7
    assert cold.summary.exploration.candidate_count == TARGET


def test_plan_scenario_all_tied_draws_ten_from_the_whole_pool():
    rows = [listing(k) for k in range(1, 41)]
    _, chosen = run(rows, (), exited_key=POOL_KEY)
    assert chosen.summary.mode == "near_tie_sampling" and chosen.summary.leaders == 0
    assert chosen.summary.tie_group.size == 40 and len(chosen.ids) == 10
    assert set(chosen.reasons.values()) == {"tie_group_sample"}
    assert chosen.ids == [i for i in ids_of(rows) if i in chosen.ids]
    assert len(chosen.selection_input["head"]) == 40  # whole pool stored when all tie (R6)


def test_plan_scenario_fully_distinct_selects_by_score_even_with_cold_start_enabled():
    rows = [listing(k, price=1000.0 * k) for k in range(1, 41)]
    scored, chosen = run(rows, [SOFT_PRICE_MIN])
    # Around the cutoff, combined steps = soft_weight 0.6 x 1000 / 20000 = 0.03 > EPSILON / 2, so s_K
    # has no neighbour within EPSILON / 2 (prices above 20000 clamp to 0 far below the cutoff).
    assert (-np.diff(scored.fused.combined[scored.order][:11])).min() > EPSILON / 2
    cold = chosen.summary.cold_start
    assert (cold.coverage_c, cold.boundary_group_b, cold.stability_s) == (1.0, 1, 1.0)
    assert cold.exit_conditions_met and not cold.active and cold.reason == "exit_conditions_met"
    assert chosen.summary.mode == "ranked" and chosen.ids == scored.ranked[:10] == ids_of(rows)[:10]
    assert set(chosen.reasons.values()) == {"ranked"} and chosen.summary.tie_group.size == 1


def test_plan_scenario_small_pool_returns_everything_in_score_order():
    rows = [listing(k, "D01" if k % 2 else "D02") for k in range(1, 8)]
    scored, chosen = run(rows, [SOFT_D01])
    assert chosen.summary.mode == "small_pool" and chosen.ids == scored.ranked and len(chosen.ids) == 7
    assert set(chosen.reasons.values()) == {"small_pool"} and chosen.summary.tie_group is None
    cold = chosen.summary.cold_start
    assert not cold.evaluated and not cold.exit_conditions_met and cold.reason == "small_pool"
    _, empty = run([], [SOFT_D01])
    assert empty.summary.mode == "empty" and empty.ids == [] and empty.summary.cold_start.reason == "empty_pool"


# --- cold start diagnostics --------------------------------------------------------------------
def test_no_soft_requirements_and_all_tied_enters_price_exploration():
    rows = [listing(k) for k in range(1, 41)]
    _, chosen = run(rows, ())
    cold = chosen.summary.cold_start
    assert cold.evaluated and cold.active and not cold.exit_conditions_met
    assert cold.coverage_c == 0.0 and cold.boundary_group_b == 40 and cold.stability_s < 0.9
    assert cold.reason.split("; ")[0] == "no_soft_requirements"
    assert chosen.summary.mode == "cold_start_exploration" and len(chosen.ids) == 10
    explored = chosen.summary.exploration
    assert (explored.candidate_count, explored.sample_size) == (40, TARGET)  # stage A: TARGET of R = 40
    assert sum(b.count for b in explored.bins) == 40
    assert sum(b.quota for b in explored.bins) == TARGET and sum(b.sampled for b in explored.bins) == 10


def test_unresolved_data_is_not_treated_as_under_specification(tmp_path):
    """C is soft evidence coverage only: an unresolved soft target lowers it like any unknown
    utility (reason c_below_min), it is never reported as missing soft requirements, the run
    is not turned into a clarification, and the resolution problem is reported separately.
    Listings excluded for unknown hard data are not part of C."""
    unresolved = {"criterion_id": "near", "field": "distance", "operator": "lte", "value": 1500, "unit": "m",
                  "strength": "soft", "weight": 1, "utility": "proximity", "distance_type": "straight_line",
                  "destination_name": "Nowhere Imaginary Place"}
    listing_repo, geo_repo = repositories("fixture")
    service = RecommendationService(listing_repo, geo_repo, StateStore(tmp_path / "s.sqlite"), ScriptedController(),
                                    config=settings())
    try:
        for k, criteria in enumerate(([SOFT_DISTRICT, unresolved], [HARD_BEDROOMS, SOFT_DISTRICT, unresolved])):
            response = service.recommend(make_request(criteria, f"u-{k}", f"u-{k}", limit=3))
            assert response.status == "success" and response.clarifications == []
            assert [r.status for r in response.resolution] == ["unresolved"]
            cold = response.selection.cold_start
            assert cold.evaluated and cold.coverage_c == 0.5  # district known, target unknown
            assert "no_soft_requirements" not in cold.reason and "c_below_min" in cold.reason
            assert all(i.scores.soft_evidence_coverage == 0.5 for i in response.recommendations)
        assert response.pool.excluded_hard_unknown == 1  # fixture-10 (no bedrooms): outside the pool and C
    finally:
        service.close()


def _boundary_rows(extra_c=0, leaders=3):
    """K = 5: leaders a* (coverage 1), then the tie group b1, b2 (bedrooms unknown: coverage
    0.5) and c* (coverage 1) at 0.3, then d* at 0."""
    rows = [listing(k, "D01", 3.0, listing_id=f"a{k}") for k in range(1, leaders + 1)]
    rows += [listing(10 + k, "D01", None, listing_id=f"b{k}") for k in range(1, 3)]
    rows += [listing(20 + k, "D02", 3.0, listing_id=f"c{k}") for k in range(1, 4 + extra_c)]
    rows += [listing(30 + k, "D02", 1.0, listing_id=f"d{k}") for k in range(1, 4)]
    return rows


def test_threshold_boundaries_c_b_s_exactly_at_thresholds():
    t = selection.thresholds_of(settings())
    assert (t.c, t.b, t.s) == (0.8, 5, 0.9)
    assert selection.exit_conditions(0.8, 5, 0.9, t)
    assert not selection.exit_conditions(math.nextafter(0.8, 0), 5, 0.9, t)
    assert not selection.exit_conditions(0.8, 6, 0.9, t)
    assert not selection.exit_conditions(0.8, 5, math.nextafter(0.9, 0), t)
    assert not selection.exit_conditions(1.0, 1, None, t)  # R9: no S, no exit
    # Computed values landing exactly on the thresholds: C = mean(1, 1, 1, .5, .5) = 0.8, B = 5.
    _, chosen = run(_boundary_rows(), PLAN, limit=5)
    cold = chosen.summary.cold_start
    assert cold.coverage_c == 0.8 and cold.boundary_group_b == 5
    assert "c_below_min" not in cold.reason and "b_above_max" not in cold.reason
    assert cold.exit_conditions_met == (cold.stability_s >= 0.9)
    # One more tied listing: B = 6 > 5.
    _, chosen = run(_boundary_rows(extra_c=1), PLAN, limit=5)
    assert chosen.summary.cold_start.boundary_group_b == 6 and "b_above_max" in chosen.summary.cold_start.reason
    assert not chosen.summary.cold_start.exit_conditions_met
    # Two leaders and a third unknown-bedroom listing: top K = a1, a2, b1, b2, b3 -> C = 3.5 / 5 = 0.7.
    rows = _boundary_rows(leaders=2) + [listing(19, "D01", None, listing_id="b3")]
    _, chosen = run(rows, PLAN, limit=5)
    assert chosen.summary.cold_start.coverage_c == 0.7 and "c_below_min" in chosen.summary.cold_start.reason


def test_stability_is_computed_reweights_components_and_is_reproducible(monkeypatch):
    rows = plan_rows(3, 30, 40)
    req_a = make_request(PLAN, "req-a")
    config = settings()
    scored = scored_pool(rows, req_a, config)
    first = selection.select(scored, req_a, config, POOL_KEY, POOL_VERSION)
    second = selection.select(scored, make_request(PLAN, "req-b", "other-conversation"), config, POOL_KEY, POOL_VERSION)
    # R3: the stability seed has no request_id/conversation: identical inputs -> identical S.
    assert first.record["cold_start"]["stability_seed"] == second.record["cold_start"]["stability_seed"]
    assert first.summary.cold_start.stability_s == second.summary.cold_start.stability_s
    assert first.summary.seed != second.summary.seed  # the selection seed does depend on them
    other_feedback = make_request(PLAN, "req-a", fb=feedback([("L001", 10.0)]))
    assert selection.stability_seed(config, POOL_VERSION, other_feedback.requirements, other_feedback.behavior_context) != \
        first.record["cold_start"]["stability_seed"]
    # Trials only reweight the stored components: no PoolScores is built again.
    monkeypatch.setattr(scoring_v2.PoolScores, "__init__", lambda *a, **k: pytest.fail("rescored"))
    gen = selection.stability_generator(first.record["cold_start"]["stability_seed"])
    assert selection.top_k_stability(scored, 10, TRIALS, FRACTION, gen) == first.summary.cold_start.stability_s
    # u = 0 keeps every layer; any u renormalises every layer.
    w = scored.weights
    same = selection.perturbed_weights(w, np.zeros(len(w.soft_criteria) + 4 + len(w.features)))
    assert np.allclose(same.soft_criteria, w.soft_criteria / w.soft_criteria.sum()) and same.soft == w.soft
    moved = selection.perturbed_weights(w, np.full(len(w.soft_criteria) + 4 + len(w.features), 0.1) *
                                        np.arange(len(w.soft_criteria) + 4 + len(w.features)))
    assert math.isclose(moved.soft + moved.behavior, 1) and math.isclose(moved.appointment + moved.browsing, 1)
    assert math.isclose(moved.soft_criteria.sum(), 1) and moved.soft != w.soft


def test_stability_random_tie_break_never_fakes_stability():
    # All tied: the official order is by listing ID, but each trial breaks ties at random, so
    # S is about K / |pool| instead of 1.
    rows = [listing(k) for k in range(1, 41)]
    _, chosen = run(rows, ())
    assert chosen.summary.cold_start.stability_s < 0.5


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


def test_price_bins_come_from_the_full_pool_not_the_candidates():
    rows = plan_rows(3, 20, 40)
    for k, r in enumerate(rows):  # candidates (ranks 4 to 3 + TARGET) cheap, the tail expensive
        rows[k] = r.model_copy(update={"price": 1000.0 + k if k < 3 + TARGET else 50000.0 + k})
    rows[5] = rows[5].model_copy(update={"price": None})
    _, chosen = run(rows, PLAN)
    edges = chosen.selection_input["bin_edges"]
    assert edges[-1] == max(r.price for r in rows if r.price is not None)
    assert edges == exploration.price_bin_edges([r.price for r in rows], BINS)
    bins = chosen.summary.exploration.bins
    assert bins[-1].lower is None and bins[-1].count == 1  # unknown-price candidate bin
    assert sum(b.count for b in bins) == TARGET


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
                                                          (0, 60, 60, 10), (2, 9, 12, 5)])
def test_exploration_quota_totals_and_stage_sizes(leaders, last_tied, size, limit):
    rows = plan_rows(leaders, last_tied, size)
    for r in range(25):
        _, chosen = run(rows, PLAN, limit=limit, request_id=f"q-{r}")
        assert chosen.summary.mode == "cold_start_exploration"
        explored = chosen.summary.exploration
        m = limit - chosen.summary.leaders
        g = chosen.summary.tie_group.size
        r_size = max(g, min(TARGET, size - chosen.summary.leaders))
        assert explored.candidate_count == r_size and explored.sample_size == min(TARGET, r_size)
        assert sum(b.count for b in explored.bins) == r_size
        assert sum(b.quota for b in explored.bins) == explored.sample_size
        assert sum(b.sampled for b in explored.bins) == m == len(chosen.ids) - chosen.summary.leaders
        assert all(b.sampled <= b.quota <= b.count for b in explored.bins)
        for b in explored.bins:  # proportional (largest remainder) at each stage
            assert abs(b.quota - explored.sample_size * b.count / r_size) < 1


def test_after_exit_no_price_exploration_but_near_tie_sampling_kept():
    rows = plan_rows(3, 30, 40)
    _, exited = run(rows, PLAN, exited_key=POOL_KEY)
    cold = exited.summary.cold_start
    assert cold.evaluated and not cold.active and "exited_by_orchestrator" in cold.reason
    assert exited.summary.mode == "near_tie_sampling" and exited.summary.exploration is None
    assert exited.selection_input["cold_start_active"] is False and exited.selection_input["bin_edges"] is None
    # Pool rebuilt (another pool_key) with reenter_on_pool_rebuild: cold start re-evaluated -> active.
    _, rebuilt = run(rows, PLAN, exited_key="an-older-pool-key")
    assert rebuilt.summary.cold_start.active and "reentered_pool_rebuilt" in rebuilt.summary.cold_start.reason
    assert rebuilt.summary.mode == "cold_start_exploration"
    # Without re-entry the exit holds for any pool.
    _, kept = run(rows, PLAN, config=settings(**{"cold_start.reenter_on_pool_rebuild": False}),
                  exited_key="an-older-pool-key")
    assert not kept.summary.cold_start.active and kept.summary.mode == "near_tie_sampling"
    # Cold start disabled: not evaluated, never an exit claim, near-tie sampling still applies.
    _, disabled = run(rows, PLAN, config=settings(**{"cold_start.enabled": False}))
    assert disabled.summary.cold_start.reason == "cold_start_disabled"
    assert not disabled.summary.cold_start.evaluated and not disabled.summary.cold_start.exit_conditions_met
    assert disabled.summary.mode == "near_tie_sampling"


def test_scoring_happens_before_sampling():
    rows = plan_rows(3, 20, 40)
    scored, chosen = run(rows, PLAN)
    assert len(scored.ranked) == 40 and chosen.summary.mode == "cold_start_exploration"
    head = chosen.selection_input["head"]
    order_scores = scored.fused.combined[scored.order].tolist()
    assert [h[0] for h in head] == scored.ranked[:len(head)] and [h[1] for h in head] == order_scores[:len(head)]
    assert chosen.ids[:3] == scored.ranked[:3]
    assert set(chosen.ids[3:]) <= set(scored.ranked[3:3 + TARGET])  # drawn only from R, after full scoring
    assert all(scored.score_rank[i] <= 3 + TARGET for i in chosen.ids)


# --- randomness: seeds, coverage of the tie group, no duplicates -------------------------------
def test_seeds_follow_r3():
    config = settings()
    seeds = selection.derive_seeds(config, "conv", "req", "pv")
    import hashlib

    base = hashlib.sha256(f"{config.selection.seed_namespace}|conv|req|pv|{config.selection_hash}".encode()).hexdigest()
    assert seeds["base"] == base and not seeds["override"]
    assert seeds["tie"] == hashlib.sha256(f"{base}|tie".encode()).hexdigest()
    assert len({seeds["tie"], seeds["stageA"], seeds["stageB"]}) == 3
    assert selection.derive_seeds(config, "conv", "req2", "pv")["base"] != base
    changed = settings(**{"selection.tie_max_score_span": 0.03})
    assert selection.derive_seeds(changed, "conv", "req", "pv")["base"] != base  # selection_hash in the seed
    assert settings(**{"trace.preview_rows": 3}).selection_hash == config.selection_hash
    assert selection.derive_seeds(settings(**{"trace.preview_rows": 3}), "conv", "req", "pv")["base"] == base
    override = selection.derive_seeds(config, "conv", "req", "pv", override="fixed")
    assert override["override"] and override["base"] == selection.derive_seeds(config, "x", "y", "z", override="fixed")["base"]


@pytest.mark.parametrize("exited", [True, False])
def test_no_duplicates_and_nothing_outside_the_pool(exited):
    for rows in (plan_rows(3, 30, 40), [listing(k) for k in range(1, 41)]):
        pool = set(ids_of(rows))
        for r in range(150):
            _, chosen = run(rows, PLAN, request_id=f"d-{r}", exited_key=POOL_KEY if exited else None)
            assert len(chosen.ids) == len(set(chosen.ids)) == 10 and set(chosen.ids) <= pool


def test_every_tie_group_member_is_selected_across_seeds_and_ids_are_not_fixed():
    rows = plan_rows(3, 30, 40)
    group = ids_of(rows)[3:30]
    counts, sets = Counter(), Counter()
    seeds = 400
    for r in range(seeds):
        _, chosen = run(rows, PLAN, request_id=f"stat-{r}", exited_key=POOL_KEY)
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
def test_replay_recomputes_select_v1_from_the_stored_input_only():
    for rows, kwargs in ((plan_rows(3, 30, 40), {}), (plan_rows(3, 30, 40), {"exited_key": POOL_KEY}),
                         ([listing(k) for k in range(1, 41)], {}), ([listing(k) for k in range(1, 8)], {}),
                         ([listing(k, price=1000.0 * k) for k in range(1, 41)], {})):
        criteria = [SOFT_PRICE_MIN] if rows[1].price == 2000.0 else PLAN
        _, chosen = run(rows, criteria, **kwargs)
        stored = json.loads(json.dumps(chosen.selection_input))  # as stored in the run snapshot
        replayed = replay_select(stored)
        assert replayed.ids == chosen.ids and replayed.mode == chosen.summary.mode
        assert replayed.reasons == chosen.reasons
        assert set(stored) == {"algorithm_version", "python_version", "pool_size", "result_limit", "K", "epsilon",
                               "target_sample_size", "quantile_bins", "cold_start_active", "head", "bin_edges",
                               "seeds"}
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


def test_service_round_trip_cold_start_then_exit_with_complete_diagnostics(tmp_path):
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        first = service.recommend(make_request(PLAN, "e2e-1", "e2e"))
        assert first.status == "success" and len(first.recommendations) == 10
        sel = first.selection
        assert sel.mode == "cold_start_exploration" and sel.algorithm_version == first.execution.selection_algorithm == "select-v1"
        assert len(sel.seed) == 64 and sel.result_limit == 10 and sel.leaders == 3
        assert [i.selection_reason for i in first.recommendations] == ["leader"] * 3 + ["price_exploration_sample"] * 7
        assert [i.rank for i in first.recommendations] == list(range(1, 11))
        assert [i.score_rank for i in first.recommendations] == sorted(i.score_rank for i in first.recommendations)
        assert all(i.selection_note for i in first.recommendations)
        cold = sel.cold_start
        assert cold.evaluated and cold.active and cold.pool_key == first.pool.pool_key
        assert None not in (cold.coverage_c, cold.boundary_group_b, cold.stability_s)
        assert cold.thresholds.model_dump() == {"c": 0.8, "b": 5, "s": 0.9}
        assert sel.tie_group.size == 27 and sel.exploration.candidate_count == TARGET and len(sel.exploration.bins) == BINS
        step = rank_step(service, "e2e", "e2e-1")
        record = step["result"]["data"]["selection"]
        assert step["result"]["data"]["scoring"]["pool_scored"] == first.pool.size == 40
        assert record["mode"] == sel.mode and record["cold_start"]["trials"] == TRIALS
        assert record["cold_start"]["stability_seed"] and record["duration_ms"] >= 0
        assert step["result"]["selected_ids"] == [i.listing.listing_id for i in first.recommendations]
        trace = service.store.trace("e2e", "e2e-1")
        assert trace["context"]["selection_policy"]["algorithm_version"] == "select-v1"
        gate = [s for s in trace["steps"] if s["kind"] == "final_gate"][-1]
        assert gate["passed"] and gate["selection"]["mode"] == sel.mode
        snapshot = service.store.current("e2e")
        assert snapshot["selection_input"]["seeds"]["base"] == sel.seed
        # Simulated orchestrator exit at this pool_key (round 1 did not meet the exit conditions, so an
        # R7 orchestrator would not record it; injected to exercise near-tie sampling): no price exploration.
        second = service.recommend(make_request(PLAN, "e2e-2", "e2e", exited_key=first.pool.pool_key)
                                   .model_copy(update={"previous_state_id": first.state.state_id}))
        assert second.status == "success" and second.pool.cache == "reused"
        assert second.selection.mode == "near_tie_sampling" and second.selection.exploration is None
        assert "exited_by_orchestrator" in second.selection.cold_start.reason
        assert [i.selection_reason for i in second.recommendations] == ["leader"] * 3 + ["tie_group_sample"] * 7
        # S is identical for identical inputs although the request differs (R3).
        assert second.selection.cold_start.stability_s == cold.stability_s
    finally:
        service.close()


def test_data_version_only_rebuild_does_not_reenter_cold_start(tmp_path):
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        first = service.recommend(make_request(PLAN, "dv-1", "dv"))
        service.listing.rows[-1] = service.listing.rows[-1].model_copy(update={"title": "edited"})
        second = service.recommend(make_request(PLAN, "dv-2", "dv", exited_key=first.pool.pool_key)
                                   .model_copy(update={"previous_state_id": first.state.state_id}))
        assert second.pool.cache == "built" and second.pool.data_version != first.pool.data_version
        assert second.pool.pool_key == first.pool.pool_key
        assert not second.selection.cold_start.active and "exited_by_orchestrator" in second.selection.cold_start.reason
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
                                                               "stageB": "f" * 64}}):
            broken = json.loads(json.dumps(snapshot))
            broken["selection_input"].update(change)
            with pytest.raises(RecommendationError):
                replay_gate(first, broken)
    finally:
        service.close()


def test_new_request_can_resample_with_the_same_inputs(tmp_path):
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        previous, selections, stabilities = None, set(), set()
        for r in range(6):
            body = make_request(PLAN, f"rs-{r}", "rs").model_copy(update={"previous_state_id": previous})
            response = service.recommend(body)
            previous = response.state.state_id
            selections.add(tuple(i.listing.listing_id for i in response.recommendations))
            stabilities.add(response.selection.cold_start.stability_s)
            assert [i.listing.listing_id for i in response.recommendations][:3] == ["L001", "L002", "L003"]
        assert len(selections) > 1 and len(stabilities) == 1
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


def test_final_gate_rejects_the_score_prefix_when_it_is_not_the_selection(tmp_path):
    class PrefixController(ScriptedController):
        def propose(self, dispatcher, context, trace):
            super().propose(dispatcher, context, trace)
            return Proposal(listing_ids=dispatcher.ranked[: dispatcher.request.result_limit])

    service = synthetic_service(tmp_path, plan_rows(3, 30, 40), controller=PrefixController())
    try:
        response = service.recommend(make_request(PLAN, "px-1", "px"))
        assert response.status == "error" and response.error.code == "INVALID_PROPOSAL"
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
            selected = json.loads(messages[-1]["content"])["selected_ids"]
            return {"content": json.dumps({"listing_ids": selected}), "tool_calls": []}

    service = synthetic_service(tmp_path, plan_rows(3, 30, 40), controller=Model())
    try:
        response = service.recommend(make_request(PLAN, "lv-1", "lv"))
        assert response.status == "success" and response.selection.mode == "cold_start_exploration"
        assert [i.listing.listing_id for i in response.recommendations] == \
            rank_step(service, "lv", "lv-1")["result"]["selected_ids"]
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
