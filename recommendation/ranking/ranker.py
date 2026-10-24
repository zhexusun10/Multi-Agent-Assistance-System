"""Scalar reference rules of fused-v2 scoring (spec section 7, R1d).

The production path is the vectorised full-pool scorer in ranking/scoring_v2.py. These scalar
functions state the same rules for ONE soft criterion of one listing, or ONE (candidate,
reference) pair. scoring_v2 uses ``soft_utility`` (it needs the per-listing evaluate()
results), and the tests use ``pair_similarity`` as an independent oracle for the numpy
similarity arrays. Every parameter comes from recommendation/settings/recommendation.yaml.
"""

from .metrics import haversine, numeric_similarity


def soft_utility(listing, criterion, result, facts, bounds):
    """Utility in [0, 1] of one soft criterion for one listing, or None when unknown.

    threshold: pass 1 / fail 0; proximity / minimize / maximize: linear over the
    full eligible pool min/max (constant known values are neutral, 0.5). ``facts`` are the listing's
    evidence records (distance criteria read their value from them)."""
    if result.outcome not in ("pass", "fail"):
        return None
    value = getattr(listing, criterion.field, None)
    if criterion.field == "distance":
        value = next(
            (e.value for e in facts
             if e.listing_id == listing.listing_id and e.criterion_id == criterion.criterion_id),
            None,
        )
    if criterion.utility in ("proximity", "minimize", "maximize"):
        lo, hi = (bounds["distance"][criterion.criterion_id] if criterion.utility == "proximity" else bounds["numeric"][criterion.field])
        if value is None:
            return None
        # Equal known values provide no relative preference advantage.
        if hi <= lo:
            return 0.5
        s = max(0.0, min(1.0, (value - lo) / (hi - lo)))
        return s if criterion.utility == "maximize" else 1 - s
    return float(result.outcome == "pass")


def feature_similarity(feature, a, b, spreads, similarity):
    """(similarity, raw_difference, method) of one feature; similarity None = not compared.

    Numeric features: max(0, 1 - |a - b| / spread) with the spread fitted on the pool.
    property_type: exact match. geography: both coordinates -> distance normalized by the
    current pool range for that reference; else district_code equality when geography_district_fallback; else
    unavailable. A missing attribute on either side is never compared."""
    if feature == "geography":
        distance = haversine(a, b)
        if distance is not None:
            scale = spreads.get("geography_by_reference", {}).get(b.listing_id, similarity.geographic_scale_m)
            return (max(0.0, 1 - distance / scale) if scale > 0 else 0.5), distance, "haversine_pool_minmax"
        if similarity.geography_district_fallback and a.district_code is not None and b.district_code is not None:
            return float(a.district_code == b.district_code), None, "district_fallback"
        return None, None, "unavailable"
    if feature == "property_type":
        if a.property_type is None or b.property_type is None:
            return None, None, "missing"
        return float(a.property_type == b.property_type), None, "exact"
    s, diff = numeric_similarity(getattr(a, feature), getattr(b, feature), spreads[feature])
    return s, diff, (f"numeric_{similarity.numeric_scaling}" if s is not None else "missing")


def pair_similarity(a, b, weights, spreads, similarity):
    """(similarity | None, comparison_coverage | None, details) of candidate ``a`` against
    reference ``b``.

    ``weights`` are the ACTIVE feature weights (configured weight > 0, not suppressed, not
    dropped for zero variance). The similarity is renormalised over the compared features;
    comparison_coverage = compared weight / active weight. The pair is unavailable (None) when
    nothing is compared or coverage < min_comparison_coverage. Self pairs have identity similarity 1 even without descriptive features."""
    if a.listing_id == b.listing_id:
        return 1.0, 1.0, [("identity", 1.0, 0.0, "self_identity")]
    active = sum(weights.values())
    if active <= 0:
        return None, None, []
    compared = total = 0.0
    details = []
    for feature, weight in weights.items():
        s, diff, method = feature_similarity(feature, a, b, spreads, similarity)
        details.append((feature, s, diff, method))
        if s is not None:
            compared += weight
            total += weight * s
    coverage = compared / active
    if compared <= 0 or coverage < similarity.min_comparison_coverage:
        return None, coverage, details
    return total / compared, coverage, details
