"""Cold-start diagnostics and final selection, algorithm select-v2 (spec section 8, R3, R6, R9, R11;
select-v1 is kept unchanged for replaying stored runs).

Runs after full-pool scoring (ranking/scoring_v2.py) on the score order O = sort(-combined,
listing_id). Nothing here changes an official score.

K = min(result_limit, |pool|), epsilon = selection.tie_max_score_span.

Cutoff tie group (only when |pool| > result_limit): s_K = score of O[K-1];
G = {i : |s_i - s_K| <= epsilon/2} (contiguous in O because O is sorted, span <= epsilon, no
chaining); leaders L = the items of O before G; m = K - |L| >= 1.

Cold-start detector mpd-v1 (2026-10-24; replaces the C/B/S exit checks and the remembered exit):
- Short circuits: cold_start.enabled false -> inactive (cold_start_disabled); |pool| = 0 or
  |pool| <= result_limit -> not evaluated (empty_pool / small_pool); fewer than two scores in the
  window -> not evaluated (insufficient_scores, no division by zero).
- Window: the first W = cold_start.detector.candidate_window items of O (all when fewer),
  leaders included, official combined scores as computed (no per-list rescaling). W is its own
  setting, independent of the exploration settings (price_sampling.*).
- D = 2 / (n (n - 1)) * sum_{i<j} |s_i - s_j| (Gini's mean difference). The pairwise sum is a
  math.fsum, so D is correctly rounded and does not depend on summation order.
- active = D < cold_start.detector.min_mean_pairwise_difference (reasons
  mean_difference_below_threshold / mean_difference_at_or_above_threshold). D equal to the
  threshold is inactive; the comparison is exact on IEEE doubles, without a tolerance.
- The threshold is a practical score tolerance: D measures how far apart the top scores are. It is
  not a statistical test and does not show that the requirements are sufficient or that the
  ranking is correct.
- Every evaluated round derives its status from the current D. request.cold_start
  {exited, exited_pool_key} is still accepted (compatibility) and recorded in the trace as unused;
  it never bypasses the check. No weight-perturbation trials run (they moved to
  recommendation/analysis/stability.py, where the epsilon derivation still uses them).
- D is not part of selection_input: replay uses only the stored cold_start_active.

Selection (``select_v2``, shared with replay_gate; ``select_v1`` replays select-v1 history):
- |pool| = 0 -> mode empty. |pool| <= result_limit -> every item in O order, mode small_pool.
- Not cold start (identical to select-v1): |G| == m -> L + G in O order, mode ranked. Otherwise m
  items of G drawn uniformly without replacement, ``random.Random(tie).sample(G in O order, m)``,
  mode near_tie_sampling (reasons leader / tie_group_sample).
- Cold start (user plan 2026-10-24): the exploration window W = the overall top
  ``price_sampling.exploration_window`` items of O, leaders included (all when fewer), never extended
  beyond it for boundary ties. I = min(``price_sampling.intermediate_size``, |W|). When |W| <= I every
  window item is kept in O order (mode ranked, reasons ranked). Else leaders L = the items of W before
  the tie group around W's I-th score (same epsilon rule, G cut at the window end) and the I - |L|
  remaining intermediate slots are drawn from the non-leaders of W only: five quantile price bands
  (``quantile_bins``) from the prices of the WHOLE window, leaders included (duplicate edges merged,
  unknown-price band), quotas by largest remainder over each band's NON-LEADER count, uniform
  sampling without replacement inside each band (``explore`` stream). Intermediate = L + drawn, each
  in O order; returned = intermediate[:result_limit], never resampled or rebalanced, so the returned
  price mix can differ from the intermediate one. Mode cold_start_exploration (reasons leader /
  price_exploration_sample).

Seeds (R3): base = sha256(seed_namespace|conversation_id|request_id|pool_version|selection_hash);
select-v2 streams tie / explore (select-v1: tie / stageA / stageB) = sha256(base|"tie"), ... ; each stream is random.Random(int(hex[:16], 16))
(CPython Mersenne Twister, python version recorded). A seed override (terminal set-seed, P6)
replaces ``base`` only and is recorded. A new request_id gives new seeds and can therefore
resample; replaying a request returns its stored response.

Exploration summary (select-v2, response.selection.exploration, contracts.summary.ExplorationV2):
window_size / candidate_count = |W|, sampling_candidates = non-leaders of W, intermediate_limit = I,
intermediate_ids / _leaders / _sampled, final_limit = result_limit, final_ids / _leaders / _sampled;
per band: window_count (W incl. leaders), count (non-leader candidates), quota = sampled (intermediate
slots), final_sampled (among the returned listings). select-v1 stored summaries keep their shape.

selection_input (R6, stored in the run snapshot; replay_gate uses ONLY these values): select-v2
{algorithm_version, python_version, pool_size, result_limit, K, epsilon, exploration_window,
intermediate_size, quantile_bins, cold_start_active, head: [[id, score, price|null]] (|L| + |G| outside
cold start, the window W in cold start, the whole pool when it is not larger than result_limit),
bin_edges (cold start only, from W's prices, else null), seeds {base, tie, explore, override}}.
select-v1 inputs keep target_sample_size and seeds {base, tie, stageA, stageB, override}.
"""

import hashlib
import math
import platform
import random
import time
from dataclasses import dataclass, field

from recommendation.contracts.summary import (
    DETECTOR,
    DETECTOR_VERSION,
    ColdStartDiagnostics,
    ExplorationBinV2,
    ExplorationV2,
    SelectionSummary,
    TieGroup,
)
from . import exploration

ALGORITHM_VERSION = "select-v2"
V1 = "select-v1"
STREAMS = ("tie", "stageA", "stageB")  # select-v1
STREAMS_V2 = ("tie", "explore")
MODES = ("empty", "small_pool", "ranked", "near_tie_sampling", "cold_start_exploration")


def describe(settings):
    """Effective selection / cold-start policy (trace context.selection_policy)."""
    cold, sel = settings.cold_start, settings.selection
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "epsilon": sel.tie_max_score_span,
        "tie_group": "G = {i : |s_i - s_K| <= epsilon/2} around the K-th score of O; leaders = items before G",
        "random_policy": sel.random_policy,
        "seed_namespace": sel.seed_namespace,
        "seeds": "base = sha256(namespace|conversation|request|pool_version|selection_hash); tie/explore streams",
        "cold_start_selection": "window = overall top exploration_window of O (leaders included); intermediate = "
        "leaders at the tie boundary of rank min(intermediate_size, window) + price-band sample of the window's "
        "non-leaders (bands from the window's prices, quotas by non-leader counts); returned = first result_limit",
        "cold_start": {
            "enabled": cold.enabled,
            "detector": DETECTOR,
            "detector_version": DETECTOR_VERSION,
            "candidate_window": cold.detector.candidate_window,
            "min_mean_pairwise_difference": cold.detector.min_mean_pairwise_difference,
            "statistic": "D = 2/(n(n-1)) * sum_{i<j} |s_i - s_j| over the first candidate_window combined "
            "scores of O (leaders included; all when fewer)",
            "decision": "active when D < min_mean_pairwise_difference (D equal to it is inactive); every "
            "evaluated round",
            "request_cold_start": "accepted for compatibility, not used for the decision",
            "price_sampling": cold.price_sampling.model_dump(),
        },
    }


# --- seeds and RNG streams (R3) --------------------------------------------------------------
def _sha(*parts):
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def derive_seeds(settings, conversation_id, request_id, pool_version, override=None, streams=STREAMS_V2):
    """{base, <stream>..., override} as 64-hex strings (override: bool); select-v2 streams by default."""
    if override is not None:
        base = _sha("override", str(override))
    else:
        base = _sha(settings.selection.seed_namespace, conversation_id, request_id, pool_version,
                    settings.selection_hash)
    return {"base": base, **{name: _sha(base, name) for name in streams}, "override": override is not None}


def stream(seed_hex):
    """random.Random seeded with the first 64 bits of a hex seed."""
    return random.Random(int(seed_hex[:16], 16))


# --- tie group ---------------------------------------------------------------------------------
def tie_group(scores, k, epsilon):
    """[start, end) of G in O order for the K-th score (``scores`` non-increasing).

    Determinism pin (like R11): membership |s_i - s_K| <= epsilon / 2 is evaluated on the IEEE
    double scores exactly as computed, without a comparison tolerance. A difference that is
    nominally epsilon / 2 in decimal can round either way (abs(0.51 - 0.5) is
    0.010000000000000009 > 0.01 but abs(0.41 - 0.4) is 0.009999999999999953 <= 0.01), and replay
    recomputes the same comparison from the stored head, so the boundary is reproducible."""
    s_k = scores[k - 1]
    half = epsilon / 2
    start = k - 1
    while start > 0 and abs(scores[start - 1] - s_k) <= half:
        start -= 1
    end = k
    while end < len(scores) and abs(scores[end] - s_k) <= half:
        end += 1
    return start, end


# --- cold-start detector (mpd-v1) ---------------------------------------------------------------
def mean_pairwise_difference(scores):
    """D = 2 / (n (n - 1)) * sum_{i<j} |s_i - s_j| (Gini's mean difference); None when n < 2.

    Determinism pin (like R11): the pairwise absolute differences are summed with math.fsum, which
    is correctly rounded, so D does not depend on the summation order, and the threshold
    comparison is made on that double without a tolerance."""
    n = len(scores)
    if n < 2:
        return None
    total = math.fsum(abs(a - b) for i, a in enumerate(scores) for b in scores[i + 1:])
    return 2 * total / (n * (n - 1))


def evaluate_cold_start(scores, request, settings, pool_key):
    """ColdStartDiagnostics for this run plus the record kept in the trace (``scores``: O order)."""
    cold = settings.cold_start
    detector = cold.detector
    n = len(scores)
    common = {"detector": DETECTOR, "detector_version": DETECTOR_VERSION,
              "candidate_window": detector.candidate_window,
              "threshold": detector.min_mean_pairwise_difference, "pool_key": pool_key}
    record = {"request_cold_start": request.cold_start.model_dump(mode="json"), "request_cold_start_used": False}
    if not cold.enabled:
        return ColdStartDiagnostics(evaluated=False, active=False, reason="cold_start_disabled", **common), record
    if n == 0 or n <= request.result_limit:
        return ColdStartDiagnostics(evaluated=False, active=False,
                                    reason="empty_pool" if n == 0 else "small_pool", **common), record
    started = time.perf_counter()
    window = scores[:detector.candidate_window]
    d = mean_pairwise_difference(window)
    if d is None:
        return ColdStartDiagnostics(evaluated=False, active=False, reason="insufficient_scores", **common), record
    active = d < detector.min_mean_pairwise_difference
    diagnostics = ColdStartDiagnostics(
        evaluated=True, active=active,
        reason="mean_difference_below_threshold" if active else "mean_difference_at_or_above_threshold",
        evaluated_count=len(window), mean_pairwise_difference=d, **common)
    record["duration_ms"] = (time.perf_counter() - started) * 1000
    return diagnostics, record


# --- selection (shared with replay) ------------------------------------------------------------
@dataclass
class Outcome:
    ids: list
    reasons: dict
    mode: str
    leaders: int = 0
    tie: tuple | None = None  # (start, end) of G in O
    candidates: list = field(default_factory=list)  # R (cold start)
    sample: list = field(default_factory=list)  # A (cold start)
    stage_a_quotas: dict = field(default_factory=dict)  # bin -> quota (A members per bin; select-v2: slots)
    intermediate: list = field(default_factory=list)  # select-v2 cold start: leaders + drawn, O order
    intermediate_leaders: int = 0
    window: list = field(default_factory=list)  # select-v2 cold start: W (ids, O order)


def _invalid(message):
    raise ValueError(message)


def select_v1(head, pool_size, result_limit, epsilon, cold_start_active, target, bin_edges, seeds):
    """Deterministic select-v1 over the stored head ([[id, score, price], ...] in O order)."""
    ids = [h[0] for h in head]
    k = min(result_limit, pool_size)
    if pool_size == 0:
        if head:
            _invalid("an empty pool has no head")
        return Outcome([], {}, "empty")
    if pool_size <= result_limit:
        if len(head) != pool_size:
            _invalid("a small pool stores its whole order")
        return Outcome(ids, {i: "small_pool" for i in ids}, "small_pool")
    if len(head) < k:
        _invalid("head shorter than K")
    scores = [h[1] for h in head]
    start, end = tie_group(scores, k, epsilon)
    leaders, group = ids[:start], ids[start:end]
    m = k - start
    if not cold_start_active:
        if len(head) != end:
            _invalid("head must hold exactly the leaders and the tie group")
        if len(group) == m:
            return Outcome(ids[:k], {i: "ranked" for i in ids[:k]}, "ranked", start, (start, end))
        chosen = set(stream(seeds["tie"]).sample(group, m))
        drawn = [i for i in group if i in chosen]
        reasons = {**{i: "leader" for i in leaders}, **{i: "tie_group_sample" for i in drawn}}
        return Outcome(leaders + drawn, reasons, "near_tie_sampling", start, (start, end))
    size = max(len(group), min(target, pool_size - start))
    if len(head) != start + size:
        _invalid("head must hold exactly the leaders and the exploration candidates")
    if bin_edges is None or any(a >= b for a, b in zip(bin_edges, bin_edges[1:])):
        _invalid("exploration needs strictly increasing price bin edges")
    candidates = ids[start:]
    price_of = {h[0]: h[2] for h in head}
    groups = exploration.group_by_bin(candidates, price_of, bin_edges)
    if size > target:
        in_a, quotas = exploration.stratified_sample(groups, target, stream(seeds["stageA"]))
        sample = [i for i in candidates if i in in_a]
    else:
        sample, quotas = list(candidates), [len(items) for _, items in groups]
    stage_a = {b: q for (b, _), q in zip(groups, quotas)}
    drawn_set, _ = exploration.stratified_sample(
        exploration.group_by_bin(sample, price_of, bin_edges), m, stream(seeds["stageB"]))
    drawn = [i for i in candidates if i in drawn_set]
    reasons = {**{i: "leader" for i in leaders}, **{i: "price_exploration_sample" for i in drawn}}
    return Outcome(leaders + drawn, reasons, "cold_start_exploration", start, (start, end),
                   candidates, sample, stage_a)


def select_v2(head, pool_size, result_limit, epsilon, cold_start_active, window, intermediate, bin_edges, seeds):
    """Deterministic select-v2 over the stored head ([[id, score, price], ...] in O order)."""
    if not cold_start_active or pool_size <= result_limit:
        # Outside cold start select-v2 is select-v1 (the tie stream is the only one used).
        return select_v1(head, pool_size, result_limit, epsilon, False, None, None, seeds)
    size = min(window, pool_size)
    if len(head) != size:
        _invalid("head must hold exactly the exploration window")
    if bin_edges is None or any(a >= b for a, b in zip(bin_edges, bin_edges[1:])):
        _invalid("exploration needs strictly increasing price bin edges")
    ids = [h[0] for h in head]
    limit = min(intermediate, size)
    if size <= intermediate:
        # Nothing to explore: the whole window, in official score order, then the returned prefix.
        final = ids[:result_limit]
        return Outcome(final, {i: "ranked" for i in final}, "ranked", 0, None, [], [], {}, ids, 0, ids)
    scores = [h[1] for h in head]
    start, end = tie_group(scores, limit, epsilon)  # within the window only (never beyond it)
    leaders, candidates = ids[:start], ids[start:]
    price_of = {h[0]: h[2] for h in head}
    groups = exploration.group_by_bin(candidates, price_of, bin_edges)
    drawn_set, quotas = exploration.stratified_sample(groups, limit - start, stream(seeds["explore"]))
    drawn = [i for i in candidates if i in drawn_set]
    inter = leaders + drawn
    final = inter[:result_limit]
    leader_set = set(leaders)
    reasons = {i: "leader" if i in leader_set else "price_exploration_sample" for i in final}
    return Outcome(final, reasons, "cold_start_exploration", min(start, result_limit), (start, end),
                   candidates, drawn, {b: q for (b, _), q in zip(groups, quotas)}, inter, start, ids)


def check_head(head):
    """The head is in O order (score descending, then listing ID) without duplicates."""
    if any(not isinstance(h, list) or len(h) != 3 for h in head):
        _invalid("head entries must be [listing_id, score, price]")
    order = [(-h[1], h[0]) for h in head]
    if order != sorted(order) or len({h[0] for h in head}) != len(head):
        _invalid("head is not the full-pool score order")
    if any(not (isinstance(h[1], (int, float)) and math.isfinite(h[1])) for h in head):
        _invalid("head scores must be finite")


def replay(selection_input):
    """Recompute the selection from a stored select-v1 selection_input (parameters only from it)."""
    si = selection_input
    if si.get("algorithm_version") != V1:
        _invalid("not a select-v1 selection input")
    head = si["head"]
    check_head(head)
    if si["K"] != min(si["result_limit"], si["pool_size"]):
        _invalid("K differs from min(result_limit, pool_size)")
    return select_v1(head, si["pool_size"], si["result_limit"], si["epsilon"], si["cold_start_active"],
                     si["target_sample_size"], si["bin_edges"], si["seeds"])


def replay_v2(selection_input):
    """Recompute the selection from a stored select-v2 selection_input (parameters only from it)."""
    si = selection_input
    if si.get("algorithm_version") != ALGORITHM_VERSION:
        _invalid("not a select-v2 selection input")
    head = si["head"]
    check_head(head)
    if si["K"] != min(si["result_limit"], si["pool_size"]):
        _invalid("K differs from min(result_limit, pool_size)")
    if si["cold_start_active"] and si["pool_size"] > si["result_limit"]:
        # The bands must be the window's own price quantiles (leaders included), never other edges.
        if si["bin_edges"] != exploration.price_bin_edges([h[2] for h in head], si["quantile_bins"]):
            _invalid("price band edges are not the exploration window's price quantiles")
    return select_v2(head, si["pool_size"], si["result_limit"], si["epsilon"], si["cold_start_active"],
                     si["exploration_window"], si["intermediate_size"], si["bin_edges"], si["seeds"])


# --- the rank stage's selection ----------------------------------------------------------------
@dataclass
class Selection:
    ids: list
    reasons: dict
    notes: dict
    summary: SelectionSummary
    selection_input: dict
    record: dict  # trace: diagnostics + selection details (no pool rows)
    outcome: Outcome | None = None  # select_v1 outcome (debug trace: tie group, stage A/B members)


def _bins(outcome, edges, price_of):
    """ExplorationBinV2 per numeric price band of the window (+ the unknown-price band when a window
    listing has no price): window_count (leaders included), count (non-leader candidates), quota =
    sampled (intermediate slots) and final_sampled (among the returned listings)."""
    def per_band(items):
        out = {}
        for i in items:
            b = exploration.bin_index(price_of[i], edges)
            out[b] = out.get(b, 0) + 1
        return out

    window, count = per_band(outcome.window), per_band(outcome.candidates)
    sampled = per_band(outcome.sample)
    final = per_band([i for i in outcome.ids if outcome.reasons[i] == "price_exploration_sample"])
    indexes = list(range(exploration.numeric_bins(edges)))
    unknown = exploration.numeric_bins(edges)
    if unknown in window:
        indexes.append(unknown)
    bins = []
    for b in indexes:
        lower, upper = exploration.bin_bounds(b, edges)
        bins.append(ExplorationBinV2(lower=lower, upper=upper, window_count=window.get(b, 0), count=count.get(b, 0),
                                     quota=outcome.stage_a_quotas.get(b, 0), sampled=sampled.get(b, 0),
                                     final_sampled=final.get(b, 0)))
    return bins


def _exploration(outcome, edges, price_of, result_limit):
    ranked = len(outcome.intermediate) if outcome.mode == "ranked" else 0
    count = lambda reason: sum(outcome.reasons[i] == reason for i in outcome.ids)  # noqa: E731
    return ExplorationV2(
        window_size=len(outcome.window), candidate_count=len(outcome.window),
        sampling_candidates=len(outcome.candidates), intermediate_limit=len(outcome.intermediate),
        intermediate_ids=list(outcome.intermediate), intermediate_leaders=outcome.intermediate_leaders,
        intermediate_sampled=len(outcome.sample), intermediate_ranked=ranked,
        final_limit=result_limit, final_ids=list(outcome.ids), final_leaders=count("leader"),
        final_sampled=count("price_exploration_sample"), final_ranked=count("ranked"),
        bins=_bins(outcome, edges, price_of) if outcome.mode == "cold_start_exploration" else [])


def exploration_of(selection_input):
    """The select-v2 cold-start exploration summary recomputed from a stored selection_input alone
    (None outside cold start): the final gate and replay_gate compare the reported summary with it."""
    si = selection_input
    if not si.get("cold_start_active") or si["pool_size"] <= si["result_limit"]:
        return None
    outcome = replay_v2(si)
    return _exploration(outcome, si["bin_edges"], {h[0]: h[2] for h in si["head"]}, si["result_limit"])


def _notes(outcome, score_rank, size, epsilon, edges, price_of):
    notes = {}
    tie = outcome.tie
    for listing_id, reason in outcome.reasons.items():
        r = score_rank[listing_id]
        if reason == "small_pool":
            notes[listing_id] = f"All {size} eligible listings returned (pool not larger than the result limit)"
        elif reason == "ranked":
            notes[listing_id] = f"Score rank {r} of {size} eligible listings"
        elif reason == "leader" and outcome.window:
            notes[listing_id] = (f"Clear leader: score rank {r} of {size}, ahead of the near-tie group at rank "
                                 f"{len(outcome.intermediate)} of the top-{len(outcome.window)} exploration window")
        elif reason == "leader":
            notes[listing_id] = (f"Clear leader: score rank {r} of {size}, ahead of the near-tie group at the "
                                 "result cutoff")
        elif reason == "tie_group_sample":
            notes[listing_id] = (f"Drawn uniformly from {tie[1] - tie[0]} near-tied listings at the cutoff "
                                 f"(score ranks {tie[0] + 1}-{tie[1]}, epsilon {epsilon:g}); score rank {r}")
        else:
            index = exploration.bin_index(price_of[listing_id], edges)
            lower, upper = exploration.bin_bounds(index, edges)
            closing = "]" if index == exploration.numeric_bins(edges) - 1 else ")"
            band = "unknown-price band" if lower is None else f"price band [{lower:g}, {upper:g}{closing}"
            first = outcome.intermediate_leaders + 1
            notes[listing_id] = (f"Cold-start price exploration: drawn from the {band} of the top-"
                                 f"{len(outcome.window)} window among {len(outcome.candidates)} non-leaders "
                                 f"(score ranks {first}-{len(outcome.window)}); score rank {r}")
    return notes


def select(scored, request, settings, pool_key, pool_version, seed_override=None, marks=None):
    """Diagnostics + selection for one rank stage (``scored``: ranking.scoring_v2.PoolScores).

    ``marks`` (optional dict) receives the duration in ms of each finished trace stage:
    cold_start_diagnostics, price_sampling (bin edges + stage A/B, 0 outside cold start) and
    final_selection (tie group, selection, summary, notes). A stage missing from ``marks`` after
    an exception is the stage that failed."""
    marks = {} if marks is None else marks
    started = time.perf_counter()
    n = len(scored.ids)
    limit = request.result_limit
    k = min(limit, n)
    epsilon = settings.selection.tie_max_score_span
    sampling = settings.cold_start.price_sampling
    window, intermediate = sampling.exploration_window, sampling.intermediate_size
    scores = scored.fused.combined[scored.order].tolist()
    prices = [scored.rows[p].price for p in scored.order]
    start = end = 0
    if n > limit:
        start, end = tie_group(scores, k, epsilon)
    tie_ms = (time.perf_counter() - started) * 1000
    stage_started = time.perf_counter()
    diagnostics, record = evaluate_cold_start(scores, request, settings, pool_key)
    marks["cold_start_diagnostics"] = (time.perf_counter() - stage_started) * 1000
    stage_started = time.perf_counter()
    active = diagnostics.active
    if n <= limit:
        length = n
    elif active:
        length = min(window, n)  # the exploration window: overall top W, leaders included
    else:
        length = end
    head = [[scored.ranked[p], scores[p], prices[p]] for p in range(length)]
    # Price bands from the window's prices (leaders included), never from the rest of the pool.
    edges = exploration.price_bin_edges([h[2] for h in head], sampling.quantile_bins) if active else None
    seeds = derive_seeds(settings, request.conversation_id, request.request_id, pool_version, seed_override)
    outcome = select_v2(head, n, limit, epsilon, active, window, intermediate, edges, seeds)
    sampled_ms = (time.perf_counter() - stage_started) * 1000
    marks["price_sampling"] = sampled_ms if active else 0.0
    stage_started = time.perf_counter()
    price_of = {h[0]: h[2] for h in head}
    tie = None
    if outcome.tie is not None:
        group = [h[1] for h in head[outcome.tie[0]:outcome.tie[1]]]
        tie = TieGroup(size=len(group), min_score=min(group), max_score=max(group),
                       span=max(group) - min(group), epsilon=epsilon)
    explored = _exploration(outcome, edges, price_of, limit) if active else None
    summary = SelectionSummary(mode=outcome.mode, algorithm_version=ALGORITHM_VERSION, seed=seeds["base"],
                               result_limit=limit, leaders=outcome.leaders, tie_group=tie,
                               exploration=explored, cold_start=diagnostics)
    selection_input = {
        "algorithm_version": ALGORITHM_VERSION,
        "python_version": platform.python_version(),
        "pool_size": n,
        "result_limit": limit,
        "K": k,
        "epsilon": epsilon,
        "exploration_window": window,
        "intermediate_size": intermediate,
        "quantile_bins": sampling.quantile_bins,
        "cold_start_active": active,
        "head": head,
        "bin_edges": edges,
        "seeds": seeds,
    }
    record = {
        "algorithm_version": ALGORITHM_VERSION,
        "mode": outcome.mode,
        "K": k,
        "leaders": outcome.leaders,
        "m": k - outcome.leaders if n > limit and not active else None,
        "intermediate": {"ids": list(outcome.intermediate), "leaders": outcome.intermediate_leaders,
                         "window": len(outcome.window)} if active else None,
        "tie_group": tie.model_dump() if tie else None,
        "exploration": explored.model_dump() if explored else None,
        "cold_start": {**diagnostics.model_dump(mode="json"), **record},
        "seed": seeds["base"],
        "seed_override": seeds["override"],
        "head_length": length,
        "duration_ms": (time.perf_counter() - started) * 1000,
    }
    notes = _notes(outcome, scored.score_rank, n, epsilon, edges, price_of)
    marks["final_selection"] = tie_ms + (0.0 if active else sampled_ms) + (time.perf_counter() - stage_started) * 1000
    return Selection(
        ids=outcome.ids,
        reasons=outcome.reasons,
        notes=notes,
        summary=summary,
        selection_input=selection_input,
        record=record,
        outcome=outcome,
    )
