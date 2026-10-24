"""select-v2 cold-start selection (user plan 2026-10-24 + price-band change): overall top-30 exploration window
incl. leaders, intermediate 10, final = intermediate[:result_limit]; bands from the window's prices, quotas by
non-leader counts. Pure-algorithm tests use constructed heads; service tests use synthetic pools."""
import json
import random

import pytest

from recommendation.contracts.errors import RecommendationError
from recommendation.ranking import exploration, selection
from recommendation.validation import gate as gate_module
from recommendation.validation.selection_replay import replay_select

from .test_selection_v1 import (PLAN, POOL_KEY, POOL_VERSION, listing, make_request, plan_rows, run,
                                scored_pool, settings, synthetic_service)

EPS = 0.02
SEEDS = {"base": "0" * 64, "tie": "1" * 64, "explore": "2" * 64, "override": False}


def head_of(scores, prices=None):
    prices = prices or [1000.0 + k for k in range(len(scores))]
    return [[f"L{k:03d}", s, p] for k, (s, p) in enumerate(zip(scores, prices), 1)]


def v2(head, pool, limit, window=30, intermediate=10, seeds=SEEDS):
    edges = exploration.price_bin_edges([h[2] for h in head], 5)
    return selection.select_v2(head, pool, limit, EPS, True, window, intermediate, edges, seeds)


def test_never_draws_beyond_the_window_even_when_ties_extend_past_rank_30():
    # 3 leaders, then 60 tied scores: the boundary tie group would run to rank 63; the window stops at 30.
    head = head_of([0.9, 0.8, 0.7] + [0.3] * 27)  # the stored head IS the window (top 30 of a 63-pool)
    for r in range(50):
        out = v2(head, 63, 10, seeds={**SEEDS, "explore": f"{r:064x}"})
        assert out.window == [h[0] for h in head] and set(out.intermediate) <= set(out.window)
        assert out.intermediate[:3] == ["L001", "L002", "L003"] and out.intermediate_leaders == 3
        assert out.tie == (3, 30)  # cut at the window end
    with pytest.raises(ValueError):  # a head that is not exactly the window is rejected
        v2(head_of([0.9, 0.8, 0.7] + [0.3] * 40), 63, 10)


@pytest.mark.parametrize("pool", [11, 15, 29])
def test_pools_of_11_to_29_use_the_whole_pool_as_window(pool):
    head = head_of([0.5] * pool)
    out = v2(head, pool, 5)
    assert len(out.window) == pool and len(out.intermediate) == 10 and out.ids == out.intermediate[:5]
    assert len(set(out.intermediate)) == 10 and out.intermediate == sorted(out.intermediate)


@pytest.mark.parametrize("pool,limit", [(8, 5), (10, 3), (6, 1)])
def test_ten_or_fewer_candidates_keep_the_official_order(pool, limit):
    head = head_of([0.5 - 0.01 * k for k in range(pool)])
    out = v2(head, pool, limit)
    assert out.mode == "ranked" and out.intermediate == [h[0] for h in head]
    assert out.ids == out.intermediate[:limit] and set(out.reasons.values()) == {"ranked"}


@pytest.mark.parametrize("limit", [1, 5, 10])
def test_leaders_count_toward_the_intermediate_and_the_final_prefix_is_not_rebalanced(limit):
    # Seven distinct leaders, then a tie: intermediate = 7 leaders + 3 sampled; returned = first `limit`.
    head = head_of([0.99 - 0.05 * k for k in range(7)] + [0.3] * 23)
    out = v2(head, 100, limit)
    assert out.intermediate_leaders == 7 and len(out.intermediate) == 10
    assert out.ids == out.intermediate[:limit]
    assert all(out.reasons[i] == "leader" for i in out.ids[:7])
    if limit <= 7:  # only leaders returned: the sampled part is dropped, never redrawn or rebalanced
        assert set(out.reasons.values()) == {"leader"} and out.leaders == limit
    assert [int(i[1:]) for i in out.intermediate] == sorted(int(i[1:]) for i in out.intermediate)


def test_bands_from_window_prices_and_quotas_from_non_leader_counts_with_repeated_and_missing_prices():
    prices = [9000.0, 9000.0, 9000.0] + [2000.0] * 10 + [2500.0] * 8 + [3000.0] * 6 + [None] * 3
    head = head_of([0.9, 0.8, 0.7] + [0.3] * 27, prices)
    edges = exploration.price_bin_edges(prices, 5)
    assert edges == sorted(set(edges)) and 9000.0 in edges  # duplicate edges merged; leaders' prices included
    out = v2(head, 200, 10)
    groups = exploration.group_by_bin(out.candidates, {h[0]: h[2] for h in head}, edges)
    expected = exploration.allocate([len(g) for _, g in groups], 7, selection.stream(SEEDS["explore"]))
    assert [out.stage_a_quotas[b] for b, _ in groups] == expected  # largest remainder over non-leaders
    assert sum(expected) == 7 and out.candidates == [h[0] for h in head[3:]]
    unknown = exploration.numeric_bins(edges)
    assert unknown in out.stage_a_quotas  # the unknown-price band takes part in the allocation


def test_no_duplicates_and_deterministic_for_identical_inputs():
    head = head_of([0.9] + [0.4] * 29, [random.Random(k).choice([1000.0, 2000.0, None]) for k in range(30)])
    first, again = v2(head, 80, 10), v2(head, 80, 10)
    assert first.ids == again.ids and first.intermediate == again.intermediate
    assert len(set(first.intermediate)) == len(first.intermediate) == 10


def test_non_cold_start_selection_equals_select_v1():
    rows = plan_rows(3, 30, 40)
    config = settings(**{"cold_start.enabled": False})
    req = make_request(PLAN, "eq", "eq")
    scored = scored_pool(rows, req, config)
    chosen = selection.select(scored, req, config, POOL_KEY, POOL_VERSION, seed_override="x")
    si = chosen.selection_input
    v1 = selection.select_v1(si["head"], 40, 10, si["epsilon"], False, None, None, si["seeds"])
    assert (v1.ids, v1.reasons, v1.mode) == (chosen.ids, chosen.reasons, chosen.summary.mode)


def test_service_response_carries_labelled_window_intermediate_and_final_counts(tmp_path):
    svc = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        response = svc.recommend(make_request(PLAN, "lab-1", "lab", limit=5))
        explored = response.selection.exploration
        assert response.selection.mode == "cold_start_exploration" and response.selection.algorithm_version == "select-v2"
        assert (explored.window_size, explored.intermediate_limit, explored.final_limit) == (30, 10, 5)
        assert [i.listing.listing_id for i in response.recommendations] == explored.final_ids == explored.intermediate_ids[:5]
        assert explored.intermediate_leaders == 3 and explored.final_leaders == 3 and explored.final_sampled == 2
        assert response.execution.strategy == "full-pool fused-v2 scoring + select-v2 selection"
        snapshot = svc.store.current("lab")
        assert replay_select(snapshot["selection_input"]).ids == explored.final_ids
    finally:
        svc.close()


def _selected_dispatcher(rows, limit=10):
    from .test_geo_tools import call
    from recommendation.tools.dispatcher import Dispatcher
    from recommendation.repositories.connections import repositories
    from recommendation.repositories.listing_repository import FixtureListingRepository
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "listings.json"
    path.write_text(json.dumps([r.model_dump(exclude={"source", "normalization_warnings"}) for r in rows]))
    _, geo = repositories("fixture")
    d = Dispatcher(make_request(PLAN, "g-1", "g", limit=limit), {}, FixtureListingRepository(path), geo,
                   {"trace_id": "run-g", "steps": []}, config=settings())
    for name in ("resolve_entities", "build_pool", "get_geographic_evidence", "rank_candidates"):
        call(d, name)
    d.select_ranking(d.artifact.artifact_id)
    return d


@pytest.mark.parametrize("tamper", ["reorder", "drop", "outside_window", "intermediate"])
def test_final_validation_rejects_wrong_final_ids_order_or_prefix(tamper):
    d = _selected_dispatcher(plan_rows(3, 30, 40), limit=5)
    gate_module.final_gate(d, {"steps": []})  # the untouched selection passes
    explored = d.selection.summary.exploration
    if tamper == "reorder":
        d.selected = list(reversed(d.selected))
    elif tamper == "drop":
        d.selected = d.selected[:-1]
    elif tamper == "outside_window":
        d.selected = d.selected[:-1] + [d.ranked[35]]
    else:
        d.selection.summary.exploration = explored.model_copy(update={"intermediate_ids": explored.intermediate_ids[:-1]})
    with pytest.raises(RecommendationError) as rejected:
        gate_module.final_gate(d, {"steps": []})
    assert rejected.value.detail.code == "INVALID_SELECTION"


def test_tampered_stored_window_is_rejected_on_replay():
    _, chosen = run(plan_rows(3, 30, 40), PLAN)
    stored = json.loads(json.dumps(chosen.selection_input))
    for broken in ({"head": stored["head"][:-1]}, {"exploration_window": 20}, {"intermediate_size": 9},
                   {"seeds": {**stored["seeds"], "explore": "f" * 64}}):
        try:
            replayed = replay_select({**stored, **broken})
        except RecommendationError:
            continue
        assert replayed.ids != chosen.ids  # a changed input never reproduces the stored selection silently


@pytest.mark.parametrize("limit", [1, 5, 10])
def test_intermediate_is_in_score_order_even_when_prices_do_not_follow_scores(limit):
    rng = random.Random(4)
    prices = [rng.choice([1000.0, 2000.0, 3000.0, None]) for _ in range(30)]
    head = head_of([0.9, 0.8] + [0.3] * 28, prices)
    for r in range(30):
        out = v2(head, 90, limit, seeds={**SEEDS, "explore": f"{r + 7:064x}"})
        ranks = [int(i[1:]) for i in out.intermediate]
        assert ranks == sorted(ranks) and out.ids == out.intermediate[:limit]


def test_a_small_window_counts_its_listings_as_ranked_not_sampled():
    from recommendation.ranking.selection import _exploration

    head = head_of([0.5 - 0.01 * k for k in range(8)])
    out = v2(head, 8, 5)
    explored = _exploration(out, exploration.price_bin_edges([h[2] for h in head], 5), {h[0]: h[2] for h in head}, 5)
    assert (explored.intermediate_ranked, explored.intermediate_sampled, explored.intermediate_leaders) == (8, 0, 0)
    assert (explored.final_ranked, explored.final_sampled, explored.final_leaders) == (5, 0, 0)


def test_replay_rejects_band_edges_that_are_not_the_windows_price_quantiles():
    rows = [listing(k, price=1000.0 + 10 * k) for k in range(1, 201)]  # all tied: cold start over 200
    _, chosen = run(rows, ())
    stored = json.loads(json.dumps(chosen.selection_input))
    pool_edges = exploration.price_bin_edges([r.price for r in rows], 5)  # the semantics the user replaced
    assert pool_edges != stored["bin_edges"]
    with pytest.raises(RecommendationError, match="window's price quantiles"):
        replay_select({**stored, "bin_edges": pool_edges})
    with pytest.raises(RecommendationError):
        replay_select({**stored, "quantile_bins": 4})


@pytest.mark.parametrize("tamper", ["head", "window_size", "final_order", "intermediate_tail", "leaders", "bins"])
def test_final_validation_compares_the_whole_summary_with_the_replay(tamper):
    d = _selected_dispatcher(plan_rows(3, 30, 40), limit=5)
    gate_module.final_gate(d, {"steps": []})
    explored = d.selection.summary.exploration
    if tamper == "head":
        d.ranked = list(reversed(d.ranked))  # the stored head is no longer the current ranking's prefix
    elif tamper == "window_size":
        d.selection.summary.exploration = explored.model_copy(update={"window_size": 29})
    elif tamper == "final_order":
        d.selection.summary.exploration = explored.model_copy(update={"final_ids": list(reversed(explored.final_ids))})
    elif tamper == "intermediate_tail":
        tail = list(explored.intermediate_ids)
        tail[8], tail[9] = tail[9], tail[8]
        d.selection.summary.exploration = explored.model_copy(update={"intermediate_ids": tail})
    elif tamper == "leaders":
        d.selection.summary.exploration = explored.model_copy(update={"intermediate_leaders": 0, "intermediate_sampled": 10})
    else:
        d.selection.summary.exploration = explored.model_copy(update={"bins": [b.model_copy(update={"quota": 0, "sampled": 0})
                                                                              for b in explored.bins]})
    with pytest.raises(RecommendationError) as rejected:
        gate_module.final_gate(d, {"steps": []})
    assert rejected.value.detail.code == "INVALID_SELECTION"


def test_a_stored_select_v2_response_with_an_altered_summary_fails_replay(tmp_path):
    from recommendation.models.candidate import Response
    from recommendation.validation.gate import replay_gate

    svc = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        response = svc.recommend(make_request(PLAN, "rg-1", "rg", limit=5))
        snapshot = svc.store.current("rg")
        replay_gate(response, snapshot)
        data = response.model_dump(mode="json")
        data["selection"]["exploration"]["intermediate_ids"][9], data["selection"]["exploration"]["intermediate_ids"][8] = \
            data["selection"]["exploration"]["intermediate_ids"][8], data["selection"]["exploration"]["intermediate_ids"][9]
        with pytest.raises(RecommendationError, match="exploration summary"):
            replay_gate(Response.model_validate(data), snapshot)
    finally:
        svc.close()


def test_stored_select_v1_cold_start_response_parses_and_redumps_as_stored():
    from pathlib import Path
    from recommendation.models.candidate import Response

    stored = json.loads((Path(__file__).resolve().parents[2] / "tests/fixtures/recommendation-v2/"
                         "legacy-select-v1-cold-start.json").read_text())["response"]
    parsed = Response.model_validate(stored)
    assert parsed.selection.algorithm_version == "select-v1" and not hasattr(parsed.selection.exploration, "window_size")
    assert parsed.selection.exploration.sample_size == stored["selection"]["exploration"]["sample_size"]
    assert parsed.model_dump(mode="json") == stored  # accepted as stored, never reinterpreted as select-v2
