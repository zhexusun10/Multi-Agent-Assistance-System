"""Which behaviour-similarity features a requirement suppresses (spec section 1, decision 1)."""

# Criterion field -> similarity feature it constrains.
FIELD_FEATURE = {
    "price": "price",
    "bedrooms": "bedrooms",
    "floor_area_sqft": "floor_area_sqft",
    "property_type": "property_type",
    "district_code": "geography",
    "distance": "geography",
    "commute": "geography",
}
MODES = ("hard_criteria", "all_criteria", "none")


def suppressed_features(criteria, mode):
    """Features constrained by HARD criteria (hard_criteria), by any criterion
    (all_criteria, weighted-v1 behaviour) or by nothing (none)."""
    if mode not in MODES:
        raise ValueError(f"unsupported suppression mode {mode!r}")
    if mode == "none":
        return set()
    return {
        FIELD_FEATURE[c.field]
        for c in criteria
        if c.field in FIELD_FEATURE and (mode == "all_criteria" or c.strength == "hard")
    }
