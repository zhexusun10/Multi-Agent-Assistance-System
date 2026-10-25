"""Full-pool fused scoring, scoring_version fused-v2 (spec section 7, R1d, R10, R11).

Every eligible listing of the exploration pool is scored on every round. The scorer keeps
numpy COMPONENT arrays and derives every score from them in ``PoolScores.combine``:

    soft utilities      util / known          (pool x soft criteria)
    pair similarities   sim / compared        (features x pool x references), self pairs have identity similarity 1

so a caller can recombine the same components under other weights (the analysis-only stability
trials, recommendation/analysis/stability.py) without rescoring, and ScoreBreakdown / Contribution objects are built only
for the listings that are returned or previewed (``breakdown``).

Rules (all parameters from recommendation/settings/recommendation.yaml, no code defaults):

- soft_score: per-criterion utilities (ranking/ranker.py soft_utility), criterion weights
  normalised over the soft criteria; soft_evidence_coverage = sum of normalised weights with a
  known utility; no soft criteria -> soft_score None (contributes 0) and coverage 0.
- Similarity(candidate, reference): active features = feature_weights > 0 minus the features
  suppressed by ranking.similarity.suppression (ranking/suppression.py) minus numeric features
  whose pool spread is 0 or non-finite ("zero_variance", dropped for this pool). Numeric spread
  per numeric_scaling over the FULL pool's known values: std (ddof=0) | iqr (p75 - p25) |
  p05_p95 (p95 - p05), or minmax (default), numpy percentiles with method "linear" (R11). A missing attribute is
  not compared; the pair similarity is renormalised over compared features and its
  comparison_coverage = compared weight / active weight; below min_comparison_coverage, or
  nothing compared, the pair is unavailable. Self pairs have similarity and coverage 1, independent of missing attributes.
- appointment_score: references = appointment listings with a counted status (one entry per
  listing, so a status upgrade adds nothing) that are IN the pool (reference_scope in_pool);
  mean of the available pair similarities; none available -> None.
- browsing_score: references = previous-round browsing records with status recorded,
  duration > 0 and the listing in the pool; weight w = log1p(t / time_scale_seconds) with t
  capped only when duration_cap_seconds is non-null; weights normalised over the references
  with an available pair; none -> None. With a SINGLE available reference the normalisation
  cancels the absolute duration: browsing_score is that pair's similarity whatever the time.
- behavior = appointment_weight * (appointment or 0) + browsing_weight * (browsing or 0), None
  when both are None; combined = soft_weight * (soft or 0) + behavior_weight * (behavior or 0).
  Missing branches contribute 0 and weights are never redistributed. Clamped to [0, 1]; NaN or
  infinity never reaches a score.
- Item comparison_coverage (R11): reference-weight-weighted mean of the pair coverages over the
  references with an available pair, the effective reference weight being branch weight x
  normalised in-branch weight; None when no pair is available or the active weight is 0.
- Full-pool order O: sort by (-combined, listing_id); score_rank = 1-based position in O.

References come from behavior_context (main contract): appointments = book_viewing_listings
(saved timed viewing drafts), browsing = session-cumulative listing_view_durations. Out-of-pool,
uncounted, not interest_eligible and zero-second references are not used and are counted in
``summary()`` (recorded in the rank_candidates trace step).
"""

import math
import time
from dataclasses import dataclass, replace

import numpy as np

from ..geography.distance import coordinates, haversine_matrix
from ..models.score_breakdown import Contribution, ScoreBreakdown
from ..settings.pending import not_in_effect
from .ranker import soft_utility
from .suppression import suppressed_features

SCORING_VERSION = "fused-v2-pool-minmax"
FEATURES = ("price", "property_type", "bedrooms", "floor_area_sqft", "geography")
NUMERIC_FEATURES = ("price", "bedrooms", "floor_area_sqft")
BRANCHES = ("appointment", "browsing")
QUANTILE_METHOD = "linear"  # R11
STD_DDOF = 0  # R11
SPREAD_PERCENTILES = {"iqr": (25, 75), "p05_p95": (5, 95)}
ZERO_VARIANCE = "zero_variance"
GEO_NONE, GEO_HAVERSINE, GEO_DISTRICT = 0, 1, 2


def scoring_identifier(settings):
    """fused-v2 tagged with the digest of the result-affecting settings."""
    return f"{SCORING_VERSION}:{settings.selection_hash[:12]}"


def browsing_weight(seconds, browsing):
    """w = log1p(t / time_scale_seconds); t capped only when duration_cap_seconds is set."""
    cap = browsing.duration_cap_seconds
    t = seconds if cap is None else min(seconds, cap)
    return math.log1p(t / browsing.time_scale_seconds)


def behaviour_enabled(settings):
    return settings.ranking.behavior_weight > 0


def browsing_enabled(settings):
    return behaviour_enabled(settings) and settings.ranking.behavior.browsing_weight > 0


def fit_spread(values, scaling):
    """Spread of the finite values (std ddof=0 | iqr | p05_p95, numpy 'linear' percentiles);
    NaN when there is no finite value."""
    v = values[np.isfinite(values)]
    if not len(v):
        return math.nan
    if scaling == "minmax":
        return float(v.max() - v.min())
    if scaling == "std":
        return float(np.std(v, ddof=STD_DDOF))
    low, high = np.percentile(v, SPREAD_PERCENTILES[scaling], method=QUANTILE_METHOD)
    return float(high - low)


def describe(settings):
    """Effective scoring policy (trace context.scoring_policy and the controller context)."""
    ranking = settings.ranking
    similarity = ranking.similarity
    return {
        "scoring_version": scoring_identifier(settings),
        "formula": "combined = soft_weight*(soft or 0) + behavior_weight*(appointment_weight*(appointment or 0) "
        "+ browsing_weight*(browsing or 0)); missing branches contribute 0, weights never redistributed",
        "weights": {
            "soft_weight": ranking.soft_weight,
            "behavior_weight": ranking.behavior_weight,
            "appointment_weight": ranking.behavior.appointment_weight,
            "browsing_weight": ranking.behavior.browsing_weight,
        },
        "normalization": "full eligible pool min/max; constant known utility = 0.5",
        "legacy_unused_settings": ["ranking.soft.numeric_bounds", "ranking.similarity.geographic_scale_m"],
        "similarity": {
            "suppression": similarity.suppression,
            "feature_weights": similarity.feature_weights.as_dict(),
            "numeric_scaling": similarity.numeric_scaling,
            "quantile_method": QUANTILE_METHOD,
            "std_ddof": STD_DDOF,
            "geographic_scale_m": similarity.geographic_scale_m,
            "geography_district_fallback": similarity.geography_district_fallback,
            "min_comparison_coverage": similarity.min_comparison_coverage,
            "self_pairs": "identity similarity 1",
        },
        "browsing": {
            "transform": ranking.browsing.transform,
            "time_scale_seconds": ranking.browsing.time_scale_seconds,
            "duration_cap_seconds": ranking.browsing.duration_cap_seconds,
            "single_reference": "normalisation cancels the absolute duration",
            "scope": "session-cumulative behavior_context.listing_view_durations",
        },
        "behavior": {
            "counted_appointment_signals": list(ranking.behavior.counted_appointment_signals),
            "browsing_requires_interest_eligible": ranking.behavior.browsing_requires_interest_eligible,
            "reference_scope": ranking.behavior.reference_scope,
            "missing_branch_contribution": ranking.behavior.missing_branch_contribution,
            "redistribute_missing_branch_weight": ranking.behavior.redistribute_missing_branch_weight,
        },
        "settings_not_in_effect": not_in_effect(),
    }


@dataclass(frozen=True)
class Weights:
    """Every weight layer combine() uses; analysis stability trials pass perturbed copies (replace())."""

    soft_criteria: np.ndarray  # raw criterion weights, normalised in combine()
    soft: float
    behavior: float
    appointment: float
    browsing: float
    features: np.ndarray  # raw weights of the active features, renormalised per pair

    def as_dict(self, criterion_ids, features):
        return {
            "soft_criteria": dict(zip(criterion_ids, map(float, self.soft_criteria))),
            "soft_weight": self.soft, "behavior_weight": self.behavior,
            "appointment_weight": self.appointment, "browsing_weight": self.browsing,
            "features": dict(zip(features, map(float, self.features))),
        }


@dataclass
class Fused:
    """Scores derived from the components under one set of weights (arrays over the pool)."""

    soft: np.ndarray
    soft_coverage: np.ndarray
    soft_available: bool
    pair: np.ndarray  # (pool, refs) renormalised pair similarity, 0 where unavailable
    pair_coverage: np.ndarray
    pair_available: np.ndarray
    compared_weight: np.ndarray  # (pool, refs) sum of compared raw feature weights
    branch: dict  # name -> (score, available, coverage, denominator)
    behavior: np.ndarray
    behavior_available: np.ndarray
    comparison_coverage: np.ndarray
    comparison_available: np.ndarray
    combined: np.ndarray


def _column(rows, field):
    return np.array([getattr(r, field) if getattr(r, field) is not None else np.nan for r in rows], float)


def _codes(values):
    table = {}
    return np.array([-1 if v is None else table.setdefault(v, len(table)) for v in values], np.int64)


def _clip(values):
    return np.clip(np.nan_to_num(values, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)


def _unit(value):
    """Python float clamped to [0, 1] for the contract models."""
    return float(min(1.0, max(0.0, value)))


class PoolScores:
    """Component arrays, the base fused scores and the full-pool order of one rank stage."""

    def __init__(self, rows, criteria, results, evidence, behavior_context, settings):
        started = time.perf_counter()
        self.settings = settings
        self.rows = list(rows)
        self.ids = [r.listing_id for r in self.rows]
        self.index = {listing_id: i for i, listing_id in enumerate(self.ids)}
        n = len(self.rows)
        ranking = settings.ranking
        # --- soft components ---------------------------------------------------------------
        self.soft_criteria = [c for c in criteria if c.strength == "soft"]
        self.criterion_ids = [c.criterion_id for c in self.soft_criteria]
        m = len(self.soft_criteria)
        self.util = np.zeros((n, m))
        self.known = np.zeros((n, m), bool)
        bounds = {"numeric": {}, "distance": {}}
        for field in ("price", "bedrooms", "floor_area_sqft"):
            values = _column(self.rows, field)
            finite = values[np.isfinite(values)]
            bounds["numeric"][field] = (float(finite.min()), float(finite.max())) if len(finite) else (0., 0.)
        for c in self.soft_criteria:
            if c.utility == "proximity":
                values = [e.value for row in self.rows for e in evidence.get(row.listing_id, [])
                          if e.criterion_id == c.criterion_id and isinstance(e.value, (int, float)) and math.isfinite(e.value)]
                bounds["distance"][c.criterion_id] = (min(values), max(values)) if values else (0., 0.)
        self.normalization_bounds = bounds
        for i, row in enumerate(self.rows):
            if not m:
                break
            by_id = {r.criterion_id: r for r in results[row.listing_id]}
            facts = evidence.get(row.listing_id, [])
            for j, c in enumerate(self.soft_criteria):
                s = soft_utility(row, c, by_id[c.criterion_id], facts, bounds)
                if s is not None:
                    self.util[i, j] = s
                    self.known[i, j] = True
        # --- behaviour references (in pool only; everything else counted) ------------------
        self.references = self._references(behavior_context)
        # --- similarity components ---------------------------------------------------------
        self.suppressed = sorted(
            suppressed_features(criteria, ranking.similarity.suppression) if behaviour_enabled(settings) else ()
        )
        self.features, self.spreads, self.dropped = [], {}, {}
        self.refs = np.array(sorted({self.index[i] for b in BRANCHES for i in self.references[b]["ids"]}), np.int64)
        self.ref_column = {int(p): k for k, p in enumerate(self.refs)}
        self.sim = self.compared = self.geo_method = None
        self.notself = np.zeros((n, len(self.refs)), bool)
        feature_weights = {}
        if behaviour_enabled(settings):
            configured = ranking.similarity.feature_weights.as_dict()
            for f in FEATURES:
                if configured[f] > 0 and f not in self.suppressed:
                    if f in NUMERIC_FEATURES:
                        spread = fit_spread(_column(self.rows, f), ranking.similarity.numeric_scaling)
                        self.spreads[f] = spread if math.isfinite(spread) else None
                        if not math.isfinite(spread) or spread <= 0:
                            self.dropped[f] = ZERO_VARIANCE
                            continue
                    if f == "property_type" and len({r.property_type for r in self.rows if r.property_type is not None}) <= 1:
                        self.dropped[f] = ZERO_VARIANCE
                        continue
                    if f == "geography":
                        lat, lon, valid = coordinates(self.rows)
                        positions = set(zip(lat[valid].tolist(), lon[valid].tolist()))
                        districts = {r.district_code for r in self.rows if r.district_code is not None}
                        meaningful = len(positions) > 1 or (ranking.similarity.geography_district_fallback and not valid.all() and len(districts) > 1)
                    if f == "geography" and not meaningful:
                        self.dropped[f] = ZERO_VARIANCE
                        continue
                    self.features.append(f)
                    feature_weights[f] = configured[f]
            if len(self.refs):
                self._similarities()
        self.weights = Weights(
            soft_criteria=np.array([c.weight for c in self.soft_criteria], float),
            soft=ranking.soft_weight,
            behavior=ranking.behavior_weight,
            appointment=ranking.behavior.appointment_weight,
            browsing=ranking.behavior.browsing_weight,
            features=np.array([feature_weights[f] for f in self.features], float),
        )
        self.fused = self.combine()
        self.order = np.lexsort((np.array(self.ids, dtype=str), -self.fused.combined)) if n else np.zeros(0, np.int64)
        self.ranked = [self.ids[k] for k in self.order]
        self.score_rank = {listing_id: position for position, listing_id in enumerate(self.ranked, 1)}
        self.duration_ms = (time.perf_counter() - started) * 1000

    # --- references ----------------------------------------------------------------------
    def _references(self, behavior_context):
        """References from behavior_context (main contract): appointments = book_viewing_listings
        (counted signals, deduplicated per listing); browsing = session-cumulative
        listing_view_durations (interest_eligible when required, duration > 0). In-pool only."""
        behavior = self.settings.ranking.behavior
        counted = set(behavior.counted_appointment_signals)
        enabled = behaviour_enabled(self.settings)
        appointment = {"ids": [], "weights": [], "out_of_pool": [], "not_counted": []}
        browsing = {"ids": [], "weights": [], "seconds": [], "out_of_pool": [], "not_interest_eligible": [],
                    "zero_seconds": []}
        if behavior_context is None:
            return {"appointment": appointment, "browsing": browsing}
        for item in sorted(behavior_context.book_viewing_listings, key=lambda s: s.listing_id):
            if item.signal not in counted:
                appointment["not_counted"].append(item.listing_id)
            elif item.listing_id not in self.index:
                appointment["out_of_pool"].append(item.listing_id)
            elif enabled and item.listing_id not in appointment["ids"]:
                appointment["ids"].append(item.listing_id)  # deduplicated per listing
                appointment["weights"].append(1.0)
        for item in sorted(behavior_context.listing_view_durations, key=lambda s: s.listing_id):
            if behavior.browsing_requires_interest_eligible and not item.interest_eligible:
                browsing["not_interest_eligible"].append(item.listing_id)
            elif not item.duration_seconds > 0:
                browsing["zero_seconds"].append(item.listing_id)
            elif item.listing_id not in self.index:
                browsing["out_of_pool"].append(item.listing_id)
            elif browsing_enabled(self.settings):
                browsing["ids"].append(item.listing_id)
                browsing["seconds"].append(item.duration_seconds)
                browsing["weights"].append(browsing_weight(item.duration_seconds, self.settings.ranking.browsing))
        return {"appointment": appointment, "browsing": browsing}

    # --- similarity components -----------------------------------------------------------
    def _similarities(self):
        similarity = self.settings.ranking.similarity
        n, refs = len(self.rows), self.refs
        self.notself = np.arange(n)[:, None] != refs[None, :]
        sim = np.zeros((len(self.features), n, len(refs)))
        compared = np.zeros((len(self.features), n, len(refs)), bool)
        for k, f in enumerate(self.features):
            if f in NUMERIC_FEATURES:
                col = _column(self.rows, f)
                a, b = col[:, None], col[refs][None, :]
                both = np.isfinite(a) & np.isfinite(b)
                with np.errstate(invalid="ignore"):
                    s = np.maximum(0.0, 1.0 - np.abs(a - b) / self.spreads[f])
                sim[k] = np.where(both, s, 0.0)
                compared[k] = both
            elif f == "property_type":
                codes = _codes([r.property_type for r in self.rows])
                a, b = codes[:, None], codes[refs][None, :]
                both = (a >= 0) & (b >= 0)
                sim[k] = np.where(both & (a == b), 1.0, 0.0)
                compared[k] = both
            else:
                lat, lon, valid = coordinates(self.rows)
                both = valid[:, None] & valid[refs][None, :]
                method = np.where(both, GEO_HAVERSINE, GEO_NONE).astype(np.int8)
                distances = haversine_matrix(lat, lon, lat[refs], lon[refs])
                # Each reference is compared against the entire current eligible pool.
                # The reference itself is in-pool, so the minimum distance is zero.
                maxima = np.max(np.where(both, distances, 0.0), axis=0)
                self.spreads["geography_by_reference"] = {self.ids[p]: float(maxima[j]) for j, p in enumerate(refs)}
                with np.errstate(invalid="ignore", divide="ignore"):
                    s = np.where(maxima[None, :] > 0, 1.0 - distances / np.maximum(maxima, 1e-300), 0.5)
                s = np.where(both, s, 0.0)
                if similarity.geography_district_fallback:
                    codes = _codes([r.district_code for r in self.rows])
                    a, b = codes[:, None], codes[refs][None, :]
                    district = ~both & (a >= 0) & (b >= 0)
                    s = np.where(district, np.where(a == b, 1.0, 0.0), s)
                    method = np.where(district, GEO_DISTRICT, method).astype(np.int8)
                sim[k] = s
                compared[k] = method != GEO_NONE
                self.geo_method = method
        self.sim, self.compared = sim, compared

    # --- fusion --------------------------------------------------------------------------
    def combine(self, weights=None):
        """Fused scores from the components under ``weights`` (default: the settings)."""
        w = weights if weights is not None else self.weights
        n, u = len(self.rows), len(self.refs)
        min_coverage = self.settings.ranking.similarity.min_comparison_coverage
        # Soft branch.
        if self.soft_criteria:
            cw = w.soft_criteria / w.soft_criteria.sum()
            soft = _clip((self.util * self.known) @ cw)
            soft_coverage = _clip(self.known @ cw)
        else:
            soft, soft_coverage = np.zeros(n), np.zeros(n)
        # Pairs: renormalise over compared features, coverage gate, self pairs have identity similarity 1.
        pair = np.zeros((n, u))
        pair_coverage = np.zeros((n, u))
        pair_available = np.zeros((n, u), bool)
        compared_weight = np.zeros((n, u))
        active = float(w.features.sum()) if self.sim is not None else 0.0
        if self.sim is not None and active > 0:
            compared_weight = np.tensordot(w.features, self.compared.astype(float), axes=1)
            weighted = np.tensordot(w.features, self.sim, axes=1)
            pair_coverage = compared_weight / active
            pair_available = (compared_weight > 0) & (pair_coverage >= min_coverage)
            with np.errstate(invalid="ignore", divide="ignore"):
                pair = np.where(pair_available, weighted / np.where(compared_weight > 0, compared_weight, 1.0), 0.0)
            pair = _clip(pair)
        # Identity is known even when no descriptive feature is available.
        if u:
            identity = np.arange(n)[:, None] == self.refs[None, :]
            pair[identity] = 1.0
            pair_coverage[identity] = 1.0
            pair_available[identity] = True
        # Behaviour branches: weighted mean over references with an available pair.
        branch = {}
        for name in BRANCHES:
            ref = self.references[name]
            cols = np.array([self.ref_column[self.index[i]] for i in ref["ids"]], np.int64)
            rw = np.array(ref["weights"], float)
            if not len(cols):
                branch[name] = (np.zeros(n), np.zeros(n, bool), np.zeros(n), np.zeros(n))
                continue
            available = pair_available[:, cols]
            den = (available * rw).sum(axis=1)
            ok = den > 0
            # Normalise the weights first: one available reference gets weight exactly 1, so
            # its absolute duration cancels exactly (not only up to rounding).
            norm = available * rw / np.where(ok, den, 1.0)[:, None]
            score = (norm * pair[:, cols]).sum(axis=1)
            coverage = (norm * pair_coverage[:, cols]).sum(axis=1)
            branch[name] = (_clip(score), ok, _clip(coverage), den)
        appointment, appointment_ok, appointment_cov, _ = branch["appointment"]
        browsing, browsing_ok, browsing_cov, _ = branch["browsing"]
        behavior = _clip(w.appointment * appointment + w.browsing * browsing)
        behavior_available = appointment_ok | browsing_ok
        # Item comparison coverage: effective reference weight = branch weight x in-branch weight.
        bw = appointment_ok * w.appointment + browsing_ok * w.browsing
        plain = (appointment_ok * appointment_cov + browsing_ok * browsing_cov) / np.maximum(
            appointment_ok.astype(float) + browsing_ok, 1.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            weighted_cov = (appointment_ok * w.appointment * appointment_cov + browsing_ok * w.browsing * browsing_cov) / bw
        comparison = _clip(np.where(bw > 0, weighted_cov, plain))
        combined = _clip(w.soft * soft + w.behavior * behavior)
        return Fused(
            soft=soft, soft_coverage=soft_coverage, soft_available=bool(self.soft_criteria),
            pair=pair, pair_coverage=pair_coverage, pair_available=pair_available,
            compared_weight=compared_weight, branch=branch,
            behavior=behavior, behavior_available=behavior_available,
            comparison_coverage=comparison, comparison_available=behavior_available,
            combined=combined,
        )

    def reweighted(self, **changes):
        """Weights with some layers replaced (extension point for the analysis stability trials)."""
        return replace(self.weights, **changes)

    # --- per-item objects (returned items and trace previews only) -----------------------
    def breakdown(self, listing_id):
        i = self.index[listing_id]
        f = self.fused
        w = self.weights
        contributions = []
        if self.soft_criteria:
            cw = w.soft_criteria / w.soft_criteria.sum()
            for j, c in enumerate(self.soft_criteria):
                known = bool(self.known[i, j])
                s = _unit(self.util[i, j]) if known else None
                contributions.append(Contribution(
                    feature=c.criterion_id, branch="soft", similarity=s, weight=_unit(cw[j]),
                    contribution=_unit(cw[j] * (s or 0.0)), unknown=not known, method=c.utility,
                ))
        for name in BRANCHES:
            contributions += self._branch_contributions(i, name)
        score, ok = {}, {}
        for name in BRANCHES:
            score[name], ok[name] = f.branch[name][0][i], bool(f.branch[name][1][i])
        behaviour = bool(f.behavior_available[i])
        return ScoreBreakdown(
            soft_score=_unit(f.soft[i]) if f.soft_available else None,
            behavior_score=_unit(f.behavior[i]) if behaviour else None,
            appointment_score=_unit(score["appointment"]) if ok["appointment"] else None,
            browsing_score=_unit(score["browsing"]) if ok["browsing"] else None,
            combined_score=_unit(f.combined[i]),
            soft_weight=w.soft, behavior_weight=w.behavior,
            appointment_weight=w.appointment, browsing_weight=w.browsing,
            soft_evidence_coverage=_unit(f.soft_coverage[i]),
            comparison_coverage=_unit(f.comparison_coverage[i]) if behaviour else None,
            contributions=contributions,
            suppressed_features=list(self.suppressed),
            ordering="fused" if (f.soft_available or behaviour) else "stable_listing_id",
        )

    def _branch_contributions(self, i, name):
        """Per reference and active feature: weight = in-branch reference weight x renormalised
        feature weight, so a branch's contributions sum to its score. Self pairs contribute explicit identity evidence;
        features not compared, and pairs below min_comparison_coverage, carry weight 0."""
        ref = self.references[name]
        f = self.fused
        _, ok, _, den = f.branch[name]
        out = []
        for listing_id, rw in zip(ref["ids"], ref["weights"]):
            p = self.index[listing_id]
            if p == i:
                ref_weight = rw / den[i] if ok[i] else 0.0
                out.append(Contribution(feature="identity", branch=name, reference=listing_id,
                    similarity=1.0, weight=_unit(ref_weight), contribution=_unit(ref_weight),
                    unknown=False, method="self_identity"))
                continue
            u = self.ref_column[p]
            available = bool(f.pair_available[i, u])
            ref_weight = rw / den[i] if available and ok[i] else 0.0
            for k, feature in enumerate(self.features):
                compared = bool(self.compared[k, i, u])
                s = _unit(self.sim[k, i, u]) if compared else None
                fw = self.weights.features[k] / f.compared_weight[i, u] if compared else 0.0
                weight = _unit(ref_weight * fw)
                method, diff, scale = self._feature_detail(feature, i, p, u)
                if not available:
                    method += ";pair_unavailable"
                out.append(Contribution(
                    feature=feature, branch=name, reference=listing_id, raw_difference=diff, scale=scale,
                    similarity=s, weight=weight, contribution=_unit(weight * (s or 0.0)),
                    unknown=not compared, method=method,
                ))
        return out

    def _feature_detail(self, feature, i, p, u):
        similarity = self.settings.ranking.similarity
        a, b = self.rows[i], self.rows[p]
        if feature in NUMERIC_FEATURES:
            x, y = getattr(a, feature), getattr(b, feature)
            if x is None or y is None:
                return "missing", None, self.spreads[feature]
            return f"numeric_{similarity.numeric_scaling}", float(abs(x - y)), self.spreads[feature]
        if feature == "property_type":
            known = a.property_type is not None and b.property_type is not None
            return ("exact" if known else "missing"), None, None
        method = int(self.geo_method[i, u])
        if method == GEO_HAVERSINE:
            d = haversine_matrix(np.array([a.latitude]), np.array([a.longitude]),
                                 np.array([b.latitude]), np.array([b.longitude]))[0, 0]
            return "haversine_pool_minmax", float(d), self.spreads["geography_by_reference"][b.listing_id]
        return ("district_fallback" if method == GEO_DISTRICT else "unavailable"), None, None

    # --- trace ---------------------------------------------------------------------------
    def summary(self, preview_rows):
        """Compact record for the trace: effective weights, scales and reference accounting."""
        ranking = self.settings.ranking
        f = self.fused
        refs = {}
        for name in BRANCHES:
            ref = self.references[name]
            # Unused listings by reason (ids capped at trace.preview_rows, counts complete).
            unused = {k: v for k, v in ref.items() if k not in ("ids", "weights", "seconds")}
            entry = {"used_count": len(ref["ids"]),
                     **{f"{k}_count": len(v) for k, v in unused.items()},
                     **{k: v[:preview_rows] for k, v in unused.items()}}
            if name == "browsing":
                entry["references"] = [{"listing_id": i, "duration_seconds": s, "weight": wt}
                                       for i, s, wt in zip(ref["ids"], ref["seconds"], ref["weights"])][:preview_rows]
            else:
                entry["references"] = [{"listing_id": i} for i in ref["ids"]][:preview_rows]
            refs[name] = entry
        return {
            "scoring_version": scoring_identifier(self.settings),
            "pool_scored": len(self.rows),
            "weights": self.weights.as_dict(self.criterion_ids, self.features),
            "similarity": {
                "suppression": ranking.similarity.suppression,
                "suppressed_features": list(self.suppressed),
                "active_features": list(self.features),
                "dropped_features": dict(self.dropped),
                "numeric_scaling": ranking.similarity.numeric_scaling,
                "quantile_method": QUANTILE_METHOD,
                "spreads": dict(self.spreads),
                "geographic_scale_m": ranking.similarity.geographic_scale_m,
                "geography_district_fallback": ranking.similarity.geography_district_fallback,
                "min_comparison_coverage": ranking.similarity.min_comparison_coverage,
            },
            "browsing": {
                "transform": ranking.browsing.transform,
                "time_scale_seconds": ranking.browsing.time_scale_seconds,
                "duration_cap_seconds": ranking.browsing.duration_cap_seconds,
            },
            "references": refs,
            "self_pairs_excluded": 0,
            "self_pairs_included": len(self.refs),
            "normalization_bounds": self.normalization_bounds,
            "pairs_available": int(f.pair_available.sum()),
            "behavior_available": int(f.behavior_available.sum()),
            "duration_ms": self.duration_ms,
        }


def score_pool(rows, criteria, results, evidence, behavior_context, settings):
    """Score every listing of ``rows`` (the eligible pool) -> PoolScores (spec section 7)."""
    return PoolScores(rows, criteria, results, evidence, behavior_context, settings)
