"""Cold-start diagnostics and final selection, algorithm select-v1 (spec section 8, R3, R6, R9, R11).

Runs after full-pool scoring (ranking/scoring_v2.py) on the score order O = sort(-combined,
listing_id). Nothing here changes an official score.

K = min(result_limit, |pool|), epsilon = selection.tie_max_score_span.

Cutoff tie group (only when |pool| > result_limit): s_K = score of O[K-1];
G = {i : |s_i - s_K| <= epsilon/2} (contiguous in O because O is sorted, span <= epsilon, no
chaining); leaders L = the items of O before G; m = K - |L| >= 1.

Cold-start diagnostics (evaluated only when cold_start.enabled and |pool| > result_limit, R9):
- C = mean soft_evidence_coverage over O[:K]; 0 with reason ``no_soft_requirements`` when the
  request has no soft criterion. C is soft evidence coverage only: unresolved geographic targets
  or missing listing data lower it like any unknown utility, they are never reported as the user
  under-specifying, and resolution problems are reported separately (response.resolution).
- B = |G| (1 when the cutoff is fully distinguishable).
- S = mean over ``stability.trials`` of |T0 & Tt| / K, T0 = O[:K], Tt = top K after multiplying
  every weight by (1 + u), u ~ U[-f, f] (f = weight_perturbation_fraction): each soft criterion
  weight, soft/behaviour, appointment/browsing and each active similarity feature weight, each
  layer renormalised. The components are reweighted (PoolScores.combine), never rescored. Ties in
  a trial are broken by a per-trial random key, so the listing-ID order can never fake
  stability. RNG: numpy Generator(PCG64) seeded from the R3 stability seed (pool_version,
  requirements, feedback, selection_hash; NOT request_id), so S is reproducible for identical
  inputs.
- exit_conditions_met = C >= c_min and B <= b_max and S >= s_min; false whenever the diagnostics
  were not evaluated or S is None (R9).
cold_start_active = enabled and |pool| > result_limit and not exit_conditions_met and not
(request.cold_start.exited and (not reenter_on_pool_rebuild or exited_pool_key == pool_key)).
Re-entry depends on pool_key only: a data_version-only rebuild or a cache eviction keeps the
pool_key and does not re-trigger cold start (R9). Without soft criteria C = 0, so the exit
conditions can never be met (known effect, R9).

Selection (``select_v1``, shared with replay_gate):
- |pool| = 0 -> mode empty. |pool| <= result_limit -> every item in O order, mode small_pool.
- Not cold start: |G| == m -> L + G in O order, mode ranked. Otherwise m items of G drawn
  uniformly without replacement, ``random.Random(tie).sample(G in O order, m)``, mode
  near_tie_sampling (reasons leader / tie_group_sample).
- Cold start: R = the first max(|G|, min(target_sample_size, |pool| - |L|)) items after L
  (R contains G). Price bins over the FULL pool (ranking/exploration.py). Stage A: when |R| >
  target_sample_size, a proportional stratified sample of target_sample_size from R (stageA
  stream), else A = R. Stage B: the same allocation of m from A (stageB stream). Mode
  cold_start_exploration (reasons leader / price_exploration_sample).
- Display order: leaders in O order, then the drawn items in O order.

Seeds (R3): base = sha256(seed_namespace|conversation_id|request_id|pool_version|selection_hash);
tie / stageA / stageB = sha256(base|"tie"), ... ; each stream is random.Random(int(hex[:16], 16))
(CPython Mersenne Twister, python version recorded). A seed override (terminal set-seed, P6)
replaces ``base`` only and is recorded. A new request_id gives new seeds and can therefore
resample; replaying a request returns its stored response.

Exploration summary (response.selection.exploration): candidate_count = |R|, sample_size = |A|;
per price bin: count = candidates of R in the bin, quota = the bin's stage-A allocation (its
members in A; equal to count when |R| <= target_sample_size), sampled = the bin's displayed
items (stage-B allocation). So sum(count) = |R|, sum(quota) = |A|, sum(sampled) = m.

selection_input (R6, stored in the run snapshot; replay_gate uses ONLY these values):
{algorithm_version, python_version, pool_size, result_limit, K, epsilon, target_sample_size,
quantile_bins, cold_start_active, head: [[id, score, price|null]] of length |L| + |G| (|L| + |R|
in cold start, the whole pool when it is not larger than result_limit), bin_edges (cold start
only, else null), seeds {base, tie, stageA, stageB, override}}.
"""

import hashlib
import math
import platform
import random
import time
from dataclasses import dataclass, field, replace

import numpy as np

from recommendation.contracts.summary import (
    ColdStartDiagnostics,
    ColdStartThresholds,
    Exploration,
    ExplorationBin,
    SelectionSummary,
    TieGroup,
)
from ..tracing.trace import digest
from . import exploration

ALGORITHM_VERSION = "select-v1"
STREAMS = ("tie", "stageA", "stageB")
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
        "seeds": "base = sha256(namespace|conversation|request|pool_version|selection_hash); tie/stageA/stageB "
        "streams; stability seed without request_id",
        "cold_start": {
            "enabled": cold.enabled,
            "reenter_on_pool_rebuild": cold.reenter_on_pool_rebuild,
            "reentry": "pool_key change only (a data_version-only rebuild does not re-trigger)",
            "thresholds": thresholds_of(settings).model_dump(),
            "price_sampling": cold.price_sampling.model_dump(),
            "stability": cold.stability.model_dump(),
            "no_soft_requirements": "C = 0, so cold start cannot be exited without soft criteria",
        },
    }


# --- seeds and RNG streams (R3) --------------------------------------------------------------
def _sha(*parts):
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def derive_seeds(settings, conversation_id, request_id, pool_version, override=None):
    """{base, tie, stageA, stageB, override} as 64-hex strings (override: bool)."""
    if override is not None:
        base = _sha("override", str(override))
    else:
        base = _sha(settings.selection.seed_namespace, conversation_id, request_id, pool_version,
                    settings.selection_hash)
    return {"base": base, **{name: _sha(base, name) for name in STREAMS}, "override": override is not None}


def stream(seed_hex):
    """random.Random seeded with the first 64 bits of a hex seed."""
    return random.Random(int(seed_hex[:16], 16))


def stability_seed(settings, pool_version, requirements, feedback):
    """R3 stability seed: no request_id, so identical inputs give the identical S."""
    return _sha(settings.selection.seed_namespace, "stability", pool_version,
                digest(requirements.model_dump(mode="json")), digest(feedback.model_dump(mode="json")),
                settings.selection_hash)


def stability_generator(seed_hex):
    return np.random.Generator(np.random.PCG64(int(seed_hex[:16], 16)))


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


# --- diagnostics -------------------------------------------------------------------------------
def exit_conditions(c, b, s, thresholds):
    """C >= c_min and B <= b_max and S >= s_min; False when any value is missing (R9)."""
    if c is None or b is None or s is None:
        return False
    return c >= thresholds.c and b <= thresholds.b and s >= thresholds.s


def thresholds_of(settings):
    cold = settings.cold_start
    return ColdStartThresholds(c=cold.min_evidence_coverage, b=cold.max_boundary_tie_group_size,
                               s=cold.min_top_k_stability)


def _renormalised(values):
    total = float(values.sum())
    return values / total if total > 0 else values


def perturbed_weights(weights, u):
    """Weights with every entry multiplied by (1 + u) and each layer renormalised.

    ``u`` layout: soft criteria, (soft, behaviour), (appointment, browsing), active features."""
    ns, nf = len(weights.soft_criteria), len(weights.features)
    soft_criteria = _renormalised(weights.soft_criteria * (1 + u[:ns]))
    fusion = _renormalised(np.array([weights.soft, weights.behavior]) * (1 + u[ns:ns + 2]))
    branch = _renormalised(np.array([weights.appointment, weights.browsing]) * (1 + u[ns + 2:ns + 4]))
    features = _renormalised(weights.features * (1 + u[ns + 4:ns + 4 + nf]))
    return replace(weights, soft_criteria=soft_criteria, soft=float(fusion[0]), behavior=float(fusion[1]),
                   appointment=float(branch[0]), browsing=float(branch[1]), features=features)


def top_k_stability(scored, k, trials, fraction, generator):
    """S: mean overlap of the perturbed top K with T0 = O[:K] (random tie-break per trial)."""
    n = len(scored.ids)
    in_t0 = np.zeros(n, bool)
    in_t0[scored.order[:k]] = True
    width = len(scored.weights.soft_criteria) + 4 + len(scored.weights.features)
    overlap = 0.0
    for _ in range(trials):
        u = generator.uniform(-fraction, fraction, size=width)
        combined = scored.combine(perturbed_weights(scored.weights, u)).combined
        keys = generator.random(n)
        top = np.lexsort((keys, -combined))[:k]
        overlap += int(in_t0[top].sum()) / k
    return overlap / trials


def evaluate_cold_start(scored, request, settings, pool_key, pool_version, k, tie_size):
    """ColdStartDiagnostics for this run plus the record kept in the trace."""
    cold = settings.cold_start
    thresholds = thresholds_of(settings)
    n = len(scored.ids)
    limit = request.result_limit
    common = {"thresholds": thresholds, "pool_key": pool_key}
    if not cold.enabled:
        return ColdStartDiagnostics(evaluated=False, active=False, reason="cold_start_disabled",
                                    exit_conditions_met=False, **common), {}
    if n == 0 or n <= limit:
        return ColdStartDiagnostics(evaluated=False, active=False,
                                    reason="empty_pool" if n == 0 else "small_pool",
                                    exit_conditions_met=False, **common), {}
    started = time.perf_counter()
    reasons = []
    if scored.soft_criteria:
        c = float(np.mean(scored.fused.soft_coverage[scored.order[:k]]))
    else:
        c = 0.0
        reasons.append("no_soft_requirements")
    b = tie_size
    seed = stability_seed(settings, pool_version, request.requirements, request.feedback)
    s = top_k_stability(scored, k, cold.stability.trials, cold.stability.weight_perturbation_fraction,
                        stability_generator(seed))
    met = exit_conditions(c, b, s, thresholds)
    if met:
        reasons.append("exit_conditions_met")
    else:
        if c < thresholds.c and "no_soft_requirements" not in reasons:
            reasons.append("c_below_min")
        if b > thresholds.b:
            reasons.append("b_above_max")
        if s < thresholds.s:
            reasons.append("s_below_min")
    state = request.cold_start
    exited = state.exited and (not cold.reenter_on_pool_rebuild or state.exited_pool_key == pool_key)
    if exited:
        reasons.append("exited_by_orchestrator")
    elif state.exited:
        reasons.append("reentered_pool_rebuilt")  # exited at another pool_key, reenter_on_pool_rebuild
    active = not met and not exited
    diagnostics = ColdStartDiagnostics(
        evaluated=True, active=active, reason="; ".join(reasons), coverage_c=min(1.0, max(0.0, c)),
        boundary_group_b=b, stability_s=min(1.0, max(0.0, s)), exit_conditions_met=met, **common)
    record = {"stability_seed": seed, "trials": cold.stability.trials,
              "weight_perturbation_fraction": cold.stability.weight_perturbation_fraction,
              "request_cold_start": state.model_dump(mode="json"),
              "duration_ms": (time.perf_counter() - started) * 1000}
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
    stage_a_quotas: dict = field(default_factory=dict)  # bin -> quota (A members per bin)


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
    if si.get("algorithm_version") != ALGORITHM_VERSION:
        _invalid("not a select-v1 selection input")
    head = si["head"]
    check_head(head)
    if si["K"] != min(si["result_limit"], si["pool_size"]):
        _invalid("K differs from min(result_limit, pool_size)")
    return select_v1(head, si["pool_size"], si["result_limit"], si["epsilon"], si["cold_start_active"],
                     si["target_sample_size"], si["bin_edges"], si["seeds"])


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
    """ExplorationBin per numeric price bin (+ the unknown-price bin when a candidate has no price)."""
    count, sampled = {}, {}
    for i in outcome.candidates:
        b = exploration.bin_index(price_of[i], edges)
        count[b] = count.get(b, 0) + 1
    drawn = set(outcome.ids) - {i for i, r in outcome.reasons.items() if r == "leader"}
    for i in drawn:
        b = exploration.bin_index(price_of[i], edges)
        sampled[b] = sampled.get(b, 0) + 1
    indexes = list(range(exploration.numeric_bins(edges)))
    unknown = exploration.numeric_bins(edges)
    if unknown in count:
        indexes.append(unknown)
    bins = []
    for b in indexes:
        lower, upper = exploration.bin_bounds(b, edges)
        bins.append(ExplorationBin(lower=lower, upper=upper, count=count.get(b, 0),
                                   sampled=sampled.get(b, 0), quota=outcome.stage_a_quotas.get(b, 0)))
    return bins


def _notes(outcome, score_rank, size, epsilon, edges, price_of):
    notes = {}
    tie = outcome.tie
    for listing_id, reason in outcome.reasons.items():
        r = score_rank[listing_id]
        if reason == "small_pool":
            notes[listing_id] = f"All {size} eligible listings returned (pool not larger than the result limit)"
        elif reason == "ranked":
            notes[listing_id] = f"Score rank {r} of {size} eligible listings"
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
            band = "unknown-price bin" if lower is None else f"price bin [{lower:g}, {upper:g}{closing}"
            first = outcome.leaders + 1
            notes[listing_id] = (f"Cold-start price exploration: drawn from the {band} among "
                                 f"{len(outcome.candidates)} candidates (score ranks {first}-"
                                 f"{first + len(outcome.candidates) - 1}); score rank {r}")
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
    scores = scored.fused.combined[scored.order].tolist()
    prices = [scored.rows[p].price for p in scored.order]
    start = end = 0
    if n > limit:
        start, end = tie_group(scores, k, epsilon)
    tie_ms = (time.perf_counter() - started) * 1000
    stage_started = time.perf_counter()
    diagnostics, record = evaluate_cold_start(scored, request, settings, pool_key, pool_version, k, end - start)
    marks["cold_start_diagnostics"] = (time.perf_counter() - stage_started) * 1000
    stage_started = time.perf_counter()
    active = diagnostics.active
    if n <= limit:
        length = n
    elif active:
        length = start + max(end - start, min(sampling.target_sample_size, n - start))
    else:
        length = end
    head = [[scored.ranked[p], scores[p], prices[p]] for p in range(length)]
    edges = exploration.price_bin_edges(prices, sampling.quantile_bins) if active else None
    seeds = derive_seeds(settings, request.conversation_id, request.request_id, pool_version, seed_override)
    outcome = select_v1(head, n, limit, epsilon, active, sampling.target_sample_size, edges, seeds)
    sampled_ms = (time.perf_counter() - stage_started) * 1000
    marks["price_sampling"] = sampled_ms if active else 0.0
    stage_started = time.perf_counter()
    price_of = {h[0]: h[2] for h in head}
    tie = None
    if outcome.tie is not None:
        group = scores[start:end]
        tie = TieGroup(size=end - start, min_score=min(group), max_score=max(group),
                       span=max(group) - min(group), epsilon=epsilon)
    explored = None
    if outcome.mode == "cold_start_exploration":
        explored = Exploration(candidate_count=len(outcome.candidates), sample_size=len(outcome.sample),
                               bins=_bins(outcome, edges, price_of))
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
        "target_sample_size": sampling.target_sample_size,
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
        "m": k - outcome.leaders if n > limit else None,
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
