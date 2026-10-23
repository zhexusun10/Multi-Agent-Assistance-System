"""P7 calibration analyses on REAL data (read-only) -> one JSON report.

Usage (repository root):
  .venv/bin/python -m recommendation.analysis.calibrate [--data database|fixture] [--out FILE]

Every section states its data basis:
- ``real``: statistics of the live listing table / catalog / pools built by production code;
- ``real listings, synthetic references``: the same pools scored against behaviour references
  drawn by a fixed-seed RNG from the pool (no real feedback data exists, so these sections
  characterise the scorer on real attributes, never user preference or satisfaction);
- ``design``: closed-form curves (browsing weights) with no data at all.

The service is run only for the trace-size and resolution-budget sections, with the
ScriptedController (or an offline fake model that never calls an LLM) and a TEMPORARY state file.
The settings are read from the committed YAML (or --config) and varied only in memory.
"""

import argparse
import json
import math
import tempfile
import time
from collections import Counter
from pathlib import Path

import numpy as np

from ..config import RecommendationConfig
from ..geography.distance import haversine_matrix
from ..geography.resolver import resolve
from ..geography.search import search
from ..ranking import exploration, selection
from ..ranking.scoring_v2 import FEATURES, NUMERIC_FEATURES, fit_spread
from . import stats
from .live import Data
from .scenarios import HARD_SCENARIOS, SOFT_SETS

SEED = 20261023
K = 10
EPSILONS = (0.0, 0.0025, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1)
FRACTIONS = (0.05, 0.1, 0.2, 0.3)
SCALINGS = ("std", "iqr", "p05_p95")
BINS = (2, 3, 4, 5, 6, 8, 10)
TARGETS = (10, 20, 30, 40, 60)
TRIALS = (5, 10, 20, 40, 80)
SEEDS_PER_CELL = 100
CONVERGENCE_SEEDS = 30
REFERENCE_TRIALS = 1000
FIELDS = ("price", "bedrooms", "floor_area_sqft", "property_type", "district_code", "latitude", "postal_code", "title")


# --- synthetic behaviour references --------------------------------------------------------
def synthetic_feedback(scored, rng, shown=10, appointments=3):
    """Previous round = ``shown`` listings drawn from the top 30 of the score order with
    recorded durations (log-normal around 30 s, one zero), plus ``appointments`` drawn from the
    top 100. Real pool listings, SYNTHETIC behaviour."""
    head = scored.ranked[:30]
    if len(head) < 2:
        return None
    picks = list(rng.choice(head, size=min(shown, len(head)), replace=False))
    durations = np.exp(rng.normal(math.log(30), 1.0, size=len(picks)))
    durations[-1] = 0.0
    browsing = [{"listing_id": i, "status": "recorded", "duration_seconds": float(round(t, 1))}
                for i, t in zip(picks, durations)]
    pool = scored.ranked[:100]
    booked = list(rng.choice(pool, size=min(appointments, len(pool)), replace=False))
    return {"previous_round": {"request_id": "synthetic-round", "shown_listing_ids": picks, "browsing": browsing},
            "appointments": [{"listing_id": i, "statuses": ["MANUAL_SENT"], "action_ids": [f"a-{i}"]} for i in booked]}


# --- 1. data profile ---------------------------------------------------------------------------
def data_profile(data):
    rows = data.rows
    prices = [r.price for r in rows]
    return {
        "basis": "real",
        "rows": len(rows),
        "retrieve_ms": data.retrieve_ms,
        "missingness": stats.missingness(rows, FIELDS),
        "coordinates_share": sum(r.latitude is not None and r.longitude is not None for r in rows) / max(len(rows), 1),
        "price": {"summary": stats.summarize(prices), "duplicates": stats.duplicates(prices),
                  "outliers": stats.outliers(prices, thresholds=(20000, 50000, 100000, 1000000))},
        "bedrooms": stats.duplicates([r.bedrooms for r in rows], top=12),
        "floor_area_sqft": {"summary": stats.summarize([r.floor_area_sqft for r in rows]),
                            "outliers": stats.outliers([r.floor_area_sqft for r in rows], thresholds=(10000, 50000))},
        "property_type": stats.duplicates([r.property_type for r in rows], top=12),
        "district_code": {"distinct": len({r.district_code for r in rows if r.district_code})},
        "text_lengths": {f: stats.summarize([len(getattr(r, f)) for r in rows if getattr(r, f)])
                         for f in ("title", "url")},
    }


# --- 2. pools ----------------------------------------------------------------------------------
def pool_report(build):
    rows = build.rows
    prices = [r.price for r in rows]
    return {
        "basis": "real",
        "hard_criteria": [c["criterion_id"] for c in build.request.model_dump(mode="json")["requirements"]["criteria"]],
        **build.partition,
        "build_ms": build.build_ms,
        "content_bytes": len(json.dumps(build.content)),
        "missingness": stats.missingness(rows, FIELDS[:6]),
        "price": {"summary": stats.summarize(prices), "duplicates": stats.duplicates(prices, top=5),
                  "outliers": stats.outliers(prices, thresholds=(20000, 100000))},
        "resolution": {k: r.summary() for k, r in build.resolutions.items()},
    }


# --- 3. numeric normalisation ------------------------------------------------------------------
def _pairs(n, rng, count=20000):
    if n < 2:
        return np.zeros(0, int), np.zeros(0, int)
    a = rng.integers(0, n, size=count)
    b = rng.integers(0, n, size=count)
    keep = a != b
    return a[keep], b[keep]


def normalisation(build, rng):
    """Spread per scheme and its effect on random pool pairs: share of pairs at similarity 0
    (saturated: |a - b| >= spread), median similarity, and the spread ratio without the top 1 %
    (outlier sensitivity: 1 = insensitive)."""
    out = {}
    for f in NUMERIC_FEATURES:
        col = np.array([getattr(r, f) if getattr(r, f) is not None else np.nan for r in build.rows], float)
        known = col[np.isfinite(col)]
        a, b = _pairs(len(known), rng)
        trimmed = known[known <= np.percentile(known, 99)] if len(known) else known
        out[f] = {"known": int(len(known))}
        for scheme in SCALINGS:
            spread = fit_spread(col, scheme)
            entry = {"spread": None if not math.isfinite(spread) else spread,
                     "zero_variance": not (math.isfinite(spread) and spread > 0)}
            if not entry["zero_variance"] and len(a):
                sim = np.maximum(0.0, 1 - np.abs(known[a] - known[b]) / spread)
                trimmed_spread = fit_spread(trimmed, scheme)
                entry.update(saturated_share=float((sim == 0).sum() / len(sim)), median_similarity=float(np.median(sim)),
                             mean_similarity=float(sim.mean()),
                             spread_without_top_1pct_ratio=float(trimmed_spread / spread) if spread else None)
            out[f][scheme] = entry
    return out


def scheme_rank_agreement(data, build, settings, feedback):
    """Top-10 / top-30 overlap of the full combined order under each scheme vs iqr (same
    synthetic references), and the features each scheme drops for zero variance."""
    orders, dropped = {}, {}
    for scheme in SCALINGS:
        variant = settings.with_overrides({"ranking.similarity.numeric_scaling": scheme})
        scored, _ = data.score(build, [], variant, feedback)
        behaviour = np.lexsort((np.array(scored.ids, dtype=str), -scored.fused.behavior))
        orders[scheme] = [scored.ids[i] for i in behaviour]
        dropped[scheme] = dict(scored.dropped)
    base = orders["iqr"]
    return {"dropped": dropped,
            "behaviour_top10_overlap_vs_iqr": {s: stats.overlap(o, base, 10) for s, o in orders.items()},
            "behaviour_top30_overlap_vs_iqr": {s: stats.overlap(o, base, 30) for s, o in orders.items()}}


# --- 4. soft scores and ties -------------------------------------------------------------------
def score_structure(scored):
    f = scored.fused
    ordered = f.combined[scored.order].tolist()
    out = {"pool_scored": len(scored.ids),
           "combined": stats.summarize(ordered, percentiles=(0, 50, 90, 99, 100)),
           "tie_profile": stats.tie_profile(ordered, K, EPSILONS)}
    if scored.soft_criteria:
        out["soft"] = stats.summarize(f.soft.tolist(), percentiles=(0, 25, 50, 75, 90, 100))
        out["soft_top_exact_ties"] = int((f.soft == f.soft.max()).sum()) if len(f.soft) else 0
        out["C_top10"] = float(np.mean(f.soft_coverage[scored.order[:K]])) if len(scored.ids) else None
        out["soft_coverage"] = stats.summarize(f.soft_coverage.tolist(), percentiles=(0, 50, 100))
    return out


# --- 5. similarity contributions ---------------------------------------------------------------
def similarity_contributions(scored, settings):
    """Per active feature over (pool x references, self pairs excluded): comparison rate, mean
    and std of the feature similarity where compared, the weight-x-std share (how much of the
    pair-similarity variation each feature can drive), and the top-10 behaviour-order overlap
    when the feature is removed (rank influence; 1 = no influence)."""
    if scored.sim is None or not len(scored.refs):
        return {"available": False}
    mask_base = scored.notself
    weights = scored.weights.features / scored.weights.features.sum()
    rows = {}
    for k, f in enumerate(scored.features):
        mask = scored.compared[k] & mask_base
        vals = scored.sim[k][mask]
        rows[f] = {"weight": float(weights[k]), "compared_rate": float(mask.sum() / mask_base.sum()),
                   "mean": float(vals.mean()) if len(vals) else None, "std": float(vals.std()) if len(vals) else None}
    drive = {f: (r["weight"] * (r["std"] or 0.0) * r["compared_rate"]) for f, r in rows.items()}
    total = sum(drive.values()) or 1.0
    base = np.lexsort((np.array(scored.ids, dtype=str), -scored.fused.behavior))
    for k, f in enumerate(scored.features):
        rows[f]["drive_share"] = drive[f] / total
        if len(scored.features) > 1:
            w = scored.weights.features.copy()
            w[k] = 0.0
            fused = scored.combine(scored.reweighted(features=w))
            order = np.lexsort((np.array(scored.ids, dtype=str), -fused.behavior))
            rows[f]["top10_overlap_without"] = stats.overlap([scored.ids[i] for i in order],
                                                             [scored.ids[i] for i in base], 10)
    groups = {"numeric": [f for f in rows if f in NUMERIC_FEATURES], "categorical": [f for f in rows if f == "property_type"],
              "geographic": [f for f in rows if f == "geography"]}
    f = scored.fused
    notself = mask_base & (f.compared_weight > 0)
    coverage = f.pair_coverage[mask_base]
    out = {
        "available": True, "references": int(len(scored.refs)), "features": rows,
        "drive_share_by_group": {g: sum(rows[x]["drive_share"] for x in fs) for g, fs in groups.items()},
        "pair_coverage": stats.summarize(coverage.tolist(), percentiles=(0, 5, 25, 50, 100)),
        "unavailable_share_by_min_coverage": {
            f"{c:g}": float(((f.compared_weight[mask_base] == 0) | (coverage < c)).sum() / mask_base.sum())
            for c in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)},
        "compared_pairs": int(notself.sum()),
    }
    if scored.geo_method is not None:
        method = scored.geo_method[mask_base]
        out["geography_method_share"] = {"haversine": float((method == 1).mean()), "district_fallback": float((method == 2).mean()),
                                         "unavailable": float((method == 0).mean())}
    return out


def geographic_distances(build, rng):
    """Pairwise straight-line distances between random pool listings with coordinates and the
    share of pairs a geographic_scale_m would map to similarity 0."""
    located = [r for r in build.rows if r.latitude is not None and r.longitude is not None]
    if len(located) < 2:
        return {"located": len(located)}
    lat = np.array([r.latitude for r in located])
    lon = np.array([r.longitude for r in located])
    a, b = _pairs(len(located), rng, 5000)
    d = np.array([haversine_matrix(lat[i:i + 1], lon[i:i + 1], lat[j:j + 1], lon[j:j + 1])[0, 0] for i, j in zip(a, b)])
    return {"located": len(located), "pair_distance_m": stats.summarize(d.tolist(), percentiles=(5, 25, 50, 75, 95)),
            "zero_similarity_share_by_scale": {f"{s:g}": float((d >= s).mean()) for s in (1000, 2000, 3000, 5000, 8000)}}


# --- 6. cold start: stability, epsilon, exploration --------------------------------------------
def stability_and_epsilon(scored, request, settings, label):
    """C, B per epsilon, S per perturbation fraction (20 trials, fixed seed), the exit decision
    per epsilon at the configured thresholds, the score noise near the cutoff under the
    configured fraction (|delta combined| of score ranks K-5 .. K+20 over 20 trials) and, for
    every pair in that window, how often the perturbation flips the pair's order by score gap
    (the gap below which two scores are not reliably distinguishable: the epsilon basis)."""
    n = len(scored.ids)
    if n <= K:
        return {"label": label, "small_pool": True, "pool": n}
    k = K
    ordered = scored.fused.combined[scored.order].tolist()
    c = float(np.mean(scored.fused.soft_coverage[scored.order[:k]])) if scored.soft_criteria else 0.0
    thresholds = selection.thresholds_of(settings)
    s_by_f = {}
    for fraction in FRACTIONS:
        gen = np.random.Generator(np.random.PCG64(SEED))
        s_by_f[f"{fraction:g}"] = selection.top_k_stability(scored, k, 20, fraction, gen)
    fraction = settings.cold_start.stability.weight_perturbation_fraction
    gen = np.random.Generator(np.random.PCG64(SEED + 1))
    window = scored.order[max(0, k - 5):k + 20]
    width = len(scored.weights.soft_criteria) + 4 + len(scored.weights.features)
    deltas = []
    base = scored.fused.combined[window]
    gaps = base[:, None] - base[None, :]  # window is in O order: gaps >= 0 above the diagonal
    upper = np.triu(np.ones_like(gaps, bool), 1) & (gaps > 0)
    flips = np.zeros_like(gaps)
    for _ in range(20):
        u = gen.uniform(-fraction, fraction, size=width)
        combined = scored.combine(selection.perturbed_weights(scored.weights, u)).combined
        deltas.extend(np.abs(combined[window] - base).tolist())
        p = combined[window]
        flips += (p[:, None] < p[None, :])
    flips /= 20
    flip_by_gap = {}
    edges = (0.0, 0.0025, 0.005, 0.01, 0.02, 0.03, 0.05, math.inf)
    for lo, hi in zip(edges, edges[1:]):
        cell = upper & (gaps > lo) & (gaps <= hi) if lo > 0 else upper & (gaps <= hi)
        flip_by_gap[f"({lo:g},{hi:g}]"] = {"pairs": int(cell.sum()),
                                           "flip_rate": float(flips[cell].mean()) if cell.any() else None}
    s = s_by_f.get(f"{fraction:g}")
    if s is None:
        s = selection.top_k_stability(scored, k, 20, fraction, np.random.Generator(np.random.PCG64(SEED)))
    per_eps = {}
    for e in EPSILONS:
        start, end = stats.tie_group_bounds(ordered, k, e)
        per_eps[f"{e:g}"] = {"B": end - start, "exit": selection.exit_conditions(c, end - start, s, thresholds)}
    return {"label": label, "pool": n, "C": c, "S_by_fraction": s_by_f, "per_epsilon": per_eps,
            "cutoff_noise": stats.summarize(deltas, percentiles=(50, 75, 90)),
            "pair_flip_rate_by_gap": flip_by_gap,
            "s_k": ordered[k - 1], "span_top_to_k": ordered[0] - ordered[k - 1]}


def stability_convergence(scored, settings, label):
    """Variance of the S estimate vs number of trials (30 independent seeds per trial count),
    agreement of the exit test S >= s_min with a 1000-trial reference, and the cost per trial."""
    n = len(scored.ids)
    if n <= K:
        return {"label": label, "small_pool": True}
    fraction = settings.cold_start.stability.weight_perturbation_fraction
    s_min = settings.cold_start.min_top_k_stability
    reference = selection.top_k_stability(scored, K, REFERENCE_TRIALS, fraction, np.random.Generator(np.random.PCG64(SEED)))
    out = {"label": label, "pool": n, "reference_trials": REFERENCE_TRIALS, "S_reference": reference, "per_trials": {}}
    for trials in TRIALS:
        estimates, started = [], time.perf_counter()
        for seed in range(CONVERGENCE_SEEDS):
            gen = np.random.Generator(np.random.PCG64(SEED + 1000 + seed))
            estimates.append(selection.top_k_stability(scored, K, trials, fraction, gen))
        per_trial_ms = (time.perf_counter() - started) * 1000 / (CONVERGENCE_SEEDS * trials)
        agree = sum((e >= s_min) == (reference >= s_min) for e in estimates) / len(estimates)
        out["per_trials"][str(trials)] = {**stats.estimate_spread(estimates), "decision_agreement": agree,
                                          "ms_per_trial": per_trial_ms}
    return out


def exploration_grid(scored, request, settings, label):
    """Cold-start price exploration for each (quantile_bins, target_sample_size): bin balance of
    the candidate set R, the share of non-empty R bins that get a displayed item, the share of R
    drawn at least once over 100 seeds, the displayed price span relative to R's, and the score
    loss sum(drawn + leaders) vs the deterministic top K. Seeds: R3 derivation with synthetic
    request IDs (selection code unchanged)."""
    n = len(scored.ids)
    if n <= K:
        return {"label": label, "small_pool": True}
    epsilon = settings.selection.tie_max_score_span
    scores = scored.fused.combined[scored.order].tolist()
    prices = [scored.rows[p].price for p in scored.order]
    start, end = selection.tie_group(scores, K, epsilon)
    top_k = sum(scores[:K])
    out = {"label": label, "pool": n, "leaders": start, "B": end - start, "cells": {}}
    for bins in BINS:
        edges = exploration.price_bin_edges(prices, bins)
        for target in TARGETS:
            if target < K:
                continue
            length = start + max(end - start, min(target, n - start))
            head = [[scored.ranked[p], scores[p], prices[p]] for p in range(length)]
            candidates = [h[0] for h in head[start:]]
            price_of = {h[0]: h[2] for h in head}
            groups = exploration.group_by_bin(candidates, price_of, edges)
            r_counts = [len(items) for _, items in groups]
            drawn_ever, bins_hit, spans, losses = set(), [], [], []
            r_prices = [price_of[i] for i in candidates if price_of[i] is not None]
            r_span = (max(r_prices) - min(r_prices)) if r_prices else 0.0
            score_of = {h[0]: h[1] for h in head}
            for s in range(SEEDS_PER_CELL):
                seeds = selection.derive_seeds(settings, "calibration", f"r{s}", "pool")
                outcome = selection.select_v1(head, n, K, epsilon, True, target, edges, seeds)
                drawn = outcome.ids[outcome.leaders:]
                drawn_ever.update(drawn)
                hit = {exploration.bin_index(price_of[i], edges) for i in drawn}
                bins_hit.append(len(hit) / len(groups))
                dp = [price_of[i] for i in drawn if price_of[i] is not None]
                spans.append(((max(dp) - min(dp)) / r_span) if dp and r_span > 0 else 0.0)
                losses.append(1 - sum(score_of[i] for i in outcome.ids) / top_k if top_k > 0 else 0.0)
            out["cells"][f"bins={bins},target={target}"] = {
                "effective_bins": exploration.numeric_bins(edges), "R": len(candidates),
                "R_bin_balance": stats.bin_balance(r_counts), "R_nonempty_bins": len(groups),
                "share_R_bins_displayed": float(np.mean(bins_hit)), "share_R_ever_drawn": len(drawn_ever) / len(candidates),
                "displayed_price_span_vs_R": float(np.mean(spans)), "score_loss_mean": float(np.mean(losses)),
                "score_loss_max": float(np.max(losses)),
            }
    return out


# --- 7. entity resolution budgets --------------------------------------------------------------
def narrowing_options(hits, informed):
    """(axis, value) searches that can narrow ``hits``. ``informed`` (the model read the
    candidates of resolve_entities): only axes whose values differ among the candidates;
    otherwise every kind and category seen."""
    kinds = sorted({e.kind for e in hits})
    categories = sorted({getattr(e, "category", None) for e in hits} - {None})
    options = [("kind", k) for k in kinds] if (len(kinds) > 1 or not informed) else []
    options += [("category", c) for c in categories] if (len(categories) > 1 or not informed) else []
    return options


def disambiguation_attempts(catalog, name, informed=False, budget=10):
    """Offline fake-model policy for an ambiguous name, counted in search_places calls.

    naive (informed False): search the plain name first, then narrow by every kind and every
    place category seen, until exactly one candidate remains (bind) or the options run out.
    informed: the model already saw the candidates in the resolve_entities result, so it only
    issues narrowed searches along axes that differ among them. Returns (searches, resolved)."""
    hits = search(catalog, name)
    used = 0
    if not informed:
        used = 1
        if len(hits) == 1:
            return used, True
    for axis, value in narrowing_options(hits, informed):
        if used >= budget:
            break
        used += 1
        narrowed = search(catalog, name, kind=value if axis == "kind" else None,
                          category=value if axis == "category" else None)
        if len(narrowed) == 1:
            return used, True
    return used, False


def resolution_budgets(data, settings):
    """Real catalog: how many entities share a normalised name (max_candidates), which names
    resolve ambiguous, and how many searches the narrowing policy needs (max_search_attempts,
    max_resolution_tool_calls)."""
    catalog = data.geographic.catalog()
    st = catalog.static
    keys = {}
    for e in (*st.stations, *st.areas, *st.regions):
        for key in e.keys:
            keys.setdefault(key, set()).add(e.place_id)
    for key, ids in catalog.places.names.items():
        keys.setdefault(key, set()).update(ids)
    sizes = [len(v) for v in keys.values()]
    ambiguous = sorted(s for s in sizes if s > 1)
    names = sorted({e.name for e in (*st.stations, *st.areas, *st.regions, *catalog.places.places) if e.name})
    status, candidates = Counter(), []
    attempts = {"naive": Counter(), "informed": Counter()}
    unresolvable = Counter()
    narrowable = []
    for name in names:
        r = resolve(catalog, destination_name=name, max_candidates=1000)
        status[r.status] += 1
        if r.status == "ambiguous":
            candidates.append(len(r.candidates))
            for policy in attempts:
                used, ok = disambiguation_attempts(catalog, name, informed=policy == "informed")
                if ok:
                    attempts[policy][used] += 1
                    if policy == "informed":
                        narrowable.append(name)
                else:
                    unresolvable[policy] += 1
    cap = settings.entity_resolution.max_candidates
    resolved_by = {policy: {str(a): sum(c for u, c in counts.items() if u <= a) for a in range(1, 6)}
                   for policy, counts in attempts.items()}
    return {
        "basis": "real catalog",
        "entities": {"stations": len(st.stations), "areas": len(st.areas), "regions": len(st.regions),
                     "places": len(catalog.places.places)},
        "name_keys": len(sizes), "ambiguous_keys": len(ambiguous),
        "ambiguous_key_sizes": dict(sorted(Counter(ambiguous).items())),
        "ambiguous_keys_fully_listed_by_max_candidates": {
            str(c): sum(s <= c for s in ambiguous) / len(ambiguous) if ambiguous else 1.0 for c in (3, 5, 8, 10, 20)},
        "display_names": len(names), "resolve_status": dict(status),
        "ambiguous_candidate_counts": stats.summarize(candidates, percentiles=(50, 90, 95, 99, 100)),
        "narrowing_policy": {"resolved_within_searches": resolved_by, "not_narrowable": dict(unresolvable),
                             "ambiguous_names": len(candidates), "narrowable_names": narrowable[:20]},
        "configured": {"max_candidates": cap, "max_search_attempts": settings.entity_resolution.max_search_attempts,
                       "max_resolution_tool_calls": settings.controller.max_resolution_tool_calls,
                       "max_tool_calls": settings.controller.max_tool_calls},
    }


def fake_model_counts(data, settings, names):
    """Tool / turn counts of real runs (temporary state) for the scripted controller and for an
    offline fake model that resolves, narrows each ambiguous criterion with search_places,
    binds and then runs the pipeline (no LLM is called)."""
    from ..agent.controller import LiveController, ScriptedController
    from ..service import RecommendationService
    from ..state.store import StateStore
    from ..tools.registry import PIPELINE

    class Narrower(LiveController):
        def __init__(self):
            super().__init__()
            self.turn = 0
            self.queue = []

        def invoke(self, messages, tools, remaining):
            self.turn += 1
            ref = json.loads(messages[1]["content"])["context_ref"]
            if self.turn == 1:
                return {"content": "", "tool_calls": [{"id": "t1", "name": "resolve_entities", "args": {"context_ref": ref}}]}
            last = json.loads(messages[-1]["content"])
            if self.turn == 2:
                catalog = data.geographic.catalog()
                for view in last["data"]["resolution"]:
                    if view["status"] == "ambiguous" and view["bindable"]:
                        name = view["original"]["destination_name"]
                        hits = [catalog.entity(c["entity_id"]) for c in view["candidates"]]
                        self.queue += [(view["criterion_id"], name, {axis: value})
                                       for axis, value in narrowing_options(hits, informed=True)]
            if "data" in last and isinstance(last.get("data"), dict) and last["data"].get("bindable"):
                criterion = self.pending
                self.queue = [q for q in self.queue if q[0] != criterion]
                return {"content": "", "tool_calls": [{"id": f"b{self.turn}", "name": "bind_entity", "args": {
                    "context_ref": ref, "criterion_id": criterion, "entity_id": last["data"]["candidates"][0]["entity_id"]}}]}
            if self.queue:
                criterion, name, narrow = self.queue.pop(0)
                self.pending = criterion
                return {"content": "", "tool_calls": [{"id": f"s{self.turn}", "name": "search_places", "args": {
                    "context_ref": ref, "query": name, "criterion_id": criterion, **narrow}}]}
            if "selected_ids" in last and last.get("tool") == "rank_candidates":
                return {"content": json.dumps({"listing_ids": last["selected_ids"]}), "tool_calls": []}
            return {"content": "", "tool_calls": [{"id": f"p{self.turn}-{i}", "name": n, "args": {"context_ref": ref}}
                                                  for i, n in enumerate(PIPELINE[1:])]}

    out = {}
    with tempfile.TemporaryDirectory(prefix="p7-resolution-") as tmp:
        for label, criteria, controller in [("scripted/budget_bedrooms", HARD_SCENARIOS["budget_bedrooms"], ScriptedController())] + [
                (f"fake-model/{name}", [{"criterion_id": "target", "field": "distance", "operator": "lte", "value": 1500,
                                         "unit": "m", "strength": "hard", "destination_name": name,
                                         "distance_type": "straight_line"}], Narrower()) for name in names]:
            service = RecommendationService(data.listing, data.geographic, StateStore(Path(tmp) / f"{len(out)}.sqlite"),
                                            controller, config=settings)
            body = {"request_id": f"res-{len(out)}", "conversation_id": f"res-{len(out)}", "requirements_version": 1,
                    "requirements": {"listing_type": "RENT", "criteria": criteria}}
            from recommendation.contracts.request import RecommendationRequest

            response = service.recommend(RecommendationRequest.model_validate(body))
            trace = service.store.trace(body["conversation_id"], body["request_id"])
            tools = [s["tool"] for s in trace["steps"] if s["kind"] == "tool"]
            out[label] = {"status": response.status, "tool_calls": response.execution.tool_calls,
                          "model_calls": response.execution.model_calls,
                          "resolution_tool_calls": sum(t in ("search_places", "bind_entity") for t in tools),
                          "tools": tools, "resolution": [r.status for r in response.resolution]}
            service.store.close()
    return out


# --- 8. trace limits ---------------------------------------------------------------------------
def _max_string(value):
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return max((_max_string(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return max((_max_string(v) for v in value), default=0)
    return 0


def trace_sizes(data, settings):
    """Stored trace bytes (scripted controller, temporary state) per preview_rows and, in debug
    mode, per debug_max_rows; and the longest string any stored tool step holds."""
    from recommendation.contracts.request import RecommendationRequest
    from ..agent.controller import ScriptedController
    from ..service import RecommendationService
    from ..state.store import StateStore

    cases = [("none/mixed", HARD_SCENARIOS["none"] + SOFT_SETS["mixed"]),
             ("mrt_distance/district", HARD_SCENARIOS["mrt_distance"] + SOFT_SETS["district"])]
    variants = [("summary", {"trace.preview_rows": p}) for p in (5, 10, 20)]
    variants += [("debug", {"trace.mode": "debug", "trace.debug_max_rows": d}) for d in (50, 100, 200, 400)]
    out = {}
    with tempfile.TemporaryDirectory(prefix="p7-trace-") as tmp:
        for case, criteria in cases:
            for mode, overrides in variants:
                variant = settings.with_overrides(overrides)
                conv = f"trace-{len(out)}"
                service = RecommendationService(data.listing, data.geographic, StateStore(Path(tmp) / f"{conv}.sqlite"),
                                                ScriptedController(), config=variant)
                response = service.recommend(RecommendationRequest.model_validate({
                    "request_id": conv, "conversation_id": conv, "requirements_version": 1,
                    "requirements": {"listing_type": "RENT", "criteria": criteria}}))
                trace = service.store.trace(conv, conv)
                service.store.close()
                tool_steps = [s for s in trace["steps"] if s["kind"] == "tool"]
                out[f"{case}/{mode}/{json.dumps(overrides, sort_keys=True)}"] = {
                    "status": response.status, "error": response.error.code if response.error else None,
                    "pool": response.pool.size if response.pool else None,
                    "trace_bytes": len(json.dumps(trace)),
                    "debug_bytes": len(json.dumps(trace.get("debug"))) if trace.get("debug") else 0,
                    "longest_tool_string": max((_max_string(s) for s in tool_steps), default=0),
                }
    return out


# --- 9. cache ----------------------------------------------------------------------------------
def cache_facts(data, builds):
    times = []
    for _ in range(5):
        started = time.perf_counter()
        data.listing.fingerprint()
        times.append((time.perf_counter() - started) * 1000)
    return {"basis": "real (fingerprint timing, pool content sizes) + design (TTL argument: no TTL experiment)",
            "fingerprint_ms": stats.p50_p95(times),
            "pool_content_bytes": {name: len(json.dumps(b.content)) for name, b in builds.items()},
            "note": "Freshness is enforced by data_version (fingerprint) on every lookup and in the final gate; "
                    "a TTL would add no freshness, only earlier eviction."}


# --- driver ------------------------------------------------------------------------------------
def run(data, settings, sections=None, log=print):
    wanted = set(sections or ("profile", "pools", "normalisation", "scores", "similarity", "stability",
                              "convergence", "exploration", "resolution", "trace", "cache", "browsing"))
    rng = np.random.default_rng(SEED)
    report = {"settings": settings.identity(), "seed": SEED, "data_mode": data.mode,
              "basis_note": "real = live data; synthetic references = fixed-seed behaviour references over real "
                            "listings (no real feedback exists); design = closed-form curves"}
    if "profile" in wanted:
        report["profile"] = data_profile(data)
        log("profile done")
    builds = {name: data.build(name, criteria, settings) for name, criteria in HARD_SCENARIOS.items()}
    report["pools"] = {name: pool_report(b) for name, b in builds.items()}
    log("pools done")
    if "browsing" in wanted:
        report["browsing_curves"] = {"basis": "design (no real dwell data)", "curves": stats.browsing_curves(
            (5, 10, 20, 30, 60, 120), (1, 3, 5, 10, 20, 30, 60, 120, 240, 600, 1800))}
    scored_cache = {}

    def scored(name, soft_name, with_refs):
        key = (name, soft_name, with_refs)
        if key not in scored_cache:
            base, request = data.score(builds[name], SOFT_SETS[soft_name], settings)
            feedback = synthetic_feedback(base, np.random.default_rng(SEED + len(name))) if with_refs else None
            scored_cache[key] = data.score(builds[name], SOFT_SETS[soft_name], settings, feedback) if feedback else (base, request)
            scored_cache[key] = (*scored_cache[key], feedback)
        return scored_cache[key]

    if "normalisation" in wanted:
        report["normalisation"] = {"basis": "real (pairs) + real listings, synthetic references (rank agreement)"}
        for name, b in builds.items():
            entry = {"spreads": normalisation(b, rng)}
            if len(b.rows) > K:
                _, _, feedback = scored(name, "no_soft", True)
                if feedback:
                    entry["rank_agreement"] = scheme_rank_agreement(data, b, settings, feedback)
            report["normalisation"][name] = entry
        log("normalisation done")
    if "scores" in wanted:
        report["scores"] = {"basis": "real listings; synthetic references where with_refs"}
        for name in builds:
            for soft_name in SOFT_SETS:
                for refs in (False, True):
                    s, _, _ = scored(name, soft_name, refs)
                    report["scores"][f"{name}/{soft_name}/{'refs' if refs else 'no_refs'}"] = score_structure(s)
        log("scores done")
    if "similarity" in wanted:
        report["similarity"] = {"basis": "real listings, synthetic references"}
        for name, b in builds.items():
            s, _, _ = scored(name, "no_soft", True)
            entry = similarity_contributions(s, settings)
            no_fallback = settings.with_overrides({"ranking.similarity.geography_district_fallback": False})
            if entry.get("available"):
                alt, _ = data.score(b, [], no_fallback, scored(name, "no_soft", True)[2])
                entry["without_district_fallback"] = {
                    "geography_unavailable_share": float((alt.geo_method[alt.notself] == 0).mean()) if alt.geo_method is not None else None,
                    "pair_unavailable_share": float((~alt.fused.pair_available[alt.notself]).mean())}
                entry["with_district_fallback_pair_unavailable_share"] = float((~s.fused.pair_available[s.notself]).mean())
            entry["geographic_distances"] = geographic_distances(b, rng)
            report["similarity"][name] = entry
        log("similarity done")
    if "stability" in wanted:
        report["stability"] = {"basis": "real listings; synthetic references where with_refs"}
        for name in builds:
            for soft_name in SOFT_SETS:
                for refs in (False, True):
                    s, request, _ = scored(name, soft_name, refs)
                    label = f"{name}/{soft_name}/{'refs' if refs else 'no_refs'}"
                    report["stability"][label] = stability_and_epsilon(s, request, settings, label)
        log("stability done")
    if "convergence" in wanted:
        report["convergence"] = {"basis": "real listings; synthetic references"}
        for name, soft_name, refs in (("none", "mixed", True), ("budget", "district", True), ("mrt_distance", "mixed", False),
                                      ("budget_bedrooms", "mixed", True), ("amenity_distance", "district", True)):
            s, _, _ = scored(name, soft_name, refs)
            label = f"{name}/{soft_name}/{'refs' if refs else 'no_refs'}"
            report["convergence"][label] = stability_convergence(s, settings, label)
        log("convergence done")
    if "exploration" in wanted:
        report["exploration"] = {"basis": "real listings and prices; selection code with synthetic request seeds"}
        for name in builds:
            for soft_name, refs in (("no_soft", False), ("district", False), ("mixed", False), ("mixed", True)):
                s, request, _ = scored(name, soft_name, refs)
                label = f"{name}/{soft_name}/{'refs' if refs else 'no_refs'}"
                report["exploration"][label] = exploration_grid(s, request, settings, label)
        log("exploration done")
    if "resolution" in wanted:
        report["resolution"] = resolution_budgets(data, settings)
        if data.mode == "database":
            report["resolution"]["runs"] = fake_model_counts(
                data, settings, ("Lee Kong Chian School of Business", "Wet Market", "Bedok Mall"))
        log("resolution done")
    if "trace" in wanted:
        report["trace"] = {"basis": "real runs (scripted, temporary state)", "runs": trace_sizes(data, settings),
                           "text": report.get("profile", {}).get("text_lengths")}
        log("trace done")
    if "cache" in wanted:
        report["cache"] = cache_facts(data, builds)
        log("cache done")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", choices=("database", "fixture"), default="database")
    parser.add_argument("--config", default=None, help="alternative settings file (default: committed YAML)")
    parser.add_argument("--out", default=None)
    parser.add_argument("--sections", default=None, help="comma-separated subset")
    args = parser.parse_args(argv)
    settings = RecommendationConfig.from_file(args.config)
    data = Data(args.data)
    try:
        report = run(data, settings, args.sections.split(",") if args.sections else None)
    finally:
        data.close()
    text = json.dumps(report, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n")
    else:
        print(text)
    return report


if __name__ == "__main__":
    main()
