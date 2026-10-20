import math
from .metrics import haversine, numeric_similarity
from ..models.score_breakdown import ScoreBreakdown, Contribution
from ..validation.rules import evaluate

from ..config import ScoringPolicy, DEFAULT_SCALES, DEFAULT_WEIGHTS, DEFAULT_BOUNDS

SCALES = DEFAULT_SCALES
WEIGHTS = DEFAULT_WEIGHTS
BOUNDS = DEFAULT_BOUNDS


def pairwise(a, b, suppressed=(), scales=None, weights=None):
    scales = SCALES if scales is None else scales
    weights = WEIGHTS if weights is None else weights
    if any(not math.isfinite(v) or v <= 0 for v in scales.values()):
        raise ValueError("invalid scale")
    if (
        any(not math.isfinite(v) or v < 0 for v in weights.values())
        or not math.isfinite(sum(weights.values()))
        or sum(weights.values()) <= 0
    ):
        raise ValueError("invalid weights")
    if set(weights) - set(WEIGHTS) or any(
        k not in scales for k in weights if k != "property_type"
    ):
        raise ValueError("unsupported feature or missing scale")
    active = {k: v for k, v in weights.items() if k not in suppressed and v > 0}
    total = sum(active.values())
    if not total:
        return 0.0, 0.0, []
    score = coverage = 0.0
    details = []
    for feature, v in active.items():
        w = v / total
        diff = None
        scale = scales.get(feature)
        if feature == "geography":
            diff = haversine(a, b)
            s = None if diff is None else max(0, 1 - diff / scale)
        elif feature == "property_type":
            s = (
                None
                if a.property_type is None or b.property_type is None
                else float(a.property_type == b.property_type)
            )
        else:
            s, diff = numeric_similarity(
                getattr(a, feature), getattr(b, feature), scale
            )
        contribution = w * (s or 0)
        score += contribution
        coverage += w if s is not None else 0
        details.append(
            Contribution(
                feature=feature,
                reference=b.listing_id,
                raw_difference=diff,
                scale=scale,
                similarity=s,
                weight=w,
                contribution=contribution,
                unknown=s is None,
            )
        )
    return min(1, score), min(1, coverage), details


def score(listing, criteria, evidence, viewed, policy=None):
    policy = policy or ScoringPolicy()
    soft = [c for c in criteria if c.strength == "soft"]
    explicit = None
    details = []
    coverage = 0
    explicit_coverage = 0
    if soft:
        total = sum(c.weight for c in soft)
        explicit = 0.0
        results = {r.criterion_id: r for r in evaluate(listing, soft, evidence)}
        for c in soft:
            r = results[c.criterion_id]
            s = None
            value = getattr(listing, c.field, None)
            if c.field == "distance":
                value = next(
                    (
                        e.value
                        for e in evidence
                        if e.listing_id == listing.listing_id
                        and e.criterion_id == c.criterion_id
                    ),
                    None,
                )
            if r.outcome in ("pass", "fail"):
                s = float(r.outcome == "pass")
                if c.utility == "proximity":
                    s = 1 / (1 + value / c.value)
                elif c.utility in ("minimize", "maximize"):
                    lo, hi = policy.numeric_bounds[c.field]
                    s = max(0, min(1, (value - lo) / (hi - lo)))
                    s = 1 - s if c.utility == "minimize" else s
            w = c.weight / total
            contribution = w * (s or 0)
            explicit += contribution
            explicit_coverage += w if s is not None else 0
            details.append(
                Contribution(
                    feature=c.criterion_id,
                    similarity=s,
                    weight=w,
                    contribution=contribution,
                    unknown=s is None,
                )
            )
    # Conservative explicit precedence: any explicit statement disables that inferred feature globally.
    suppressed = {
        (
            "geography"
            if c.field in ("district_code", "distance", "commute")
            else c.field
        )
        for c in criteria
    } & set(WEIGHTS)
    implicit = None
    weights = [(v, min(120, d)) for v, d in viewed if d > 0]
    denominator = sum(math.log1p(t) / math.log(121) for _, t in weights)
    if denominator and any(
        v > 0 and k not in suppressed for k, v in policy.feature_weights.items()
    ):
        implicit = 0.0
        for v, t in weights:
            w = math.log1p(t) / math.log(121) / denominator
            s, c, ds = pairwise(
                listing, v, suppressed, policy.scales, policy.feature_weights
            )
            implicit += w * s
            coverage += w * c
            details.extend(
                d.model_copy(
                    update={"weight": d.weight * w, "contribution": d.contribution * w}
                )
                for d in ds
            )
    ew, iw = (
        (0.8, 0.2)
        if explicit is not None and implicit is not None
        else (
            (1.0, 0.0)
            if explicit is not None
            else ((0.0, 1.0) if implicit is not None else (0.0, 0.0))
        )
    )
    return ScoreBreakdown(
        explicit_score=explicit,
        implicit_score=implicit,
        combined_score=min(1, ew * (explicit or 0) + iw * (implicit or 0)),
        explicit_weight=ew,
        implicit_weight=iw,
        evidence_coverage=min(1, ew * explicit_coverage + iw * coverage),
        contributions=details,
        suppressed_features=sorted(suppressed),
        ordering=(
            "weighted"
            if ew and iw
            else ("explicit" if ew else ("behavioral" if iw else "stable_listing_id"))
        ),
    )
