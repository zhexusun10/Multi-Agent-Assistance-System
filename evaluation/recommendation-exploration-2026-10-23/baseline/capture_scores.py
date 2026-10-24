"""weighted-v1 score() parity oracle over every fixture pair and suppression subset.

The four baseline rounds only exercise one suppression set ({bedrooms, geography}). This
oracle covers score() directly: 12 x 12 fixture listing pairs x 9 criteria sets (which, under
weighted-v1's all-criteria suppression, suppress {}, {bedrooms}, {property_type},
{geography}, {bedrooms, geography}, {floor_area_sqft, property_type}, {bedrooms} with a
minimize utility, {bedrooms, floor_area_sqft, property_type} and all four features) x 5
viewing patterns. Each (criteria set, viewing pattern, candidate) group stores the sha256 of
the canonical JSON of the 12 ScoreBreakdown dumps, so equality is bit-exact (json writes
floats with repr, which round-trips).

Generated from the PRE-CHANGE tree (commit 4d36507), with its default policy:

    git archive 4d36507 recommendation | tar -x -C /tmp/rec-4d36507
    PARITY_ROOT=/tmp/rec-4d36507 PARITY_LABEL="commit 4d36507 (git archive)" .venv/bin/python \
        evaluation/recommendation-exploration-2026-10-23/baseline/capture_scores.py --write

Without --write it prints the digest table for the tree at PARITY_ROOT (default: this
repository) so the two can be compared. recommendation/tests/test_settings.py recomputes the
table with the committed settings and requires every digest to match.
"""

import hashlib, json, os, platform, sys
from pathlib import Path

OUT = Path(__file__).resolve().with_name("score-parity.json")
ROOT = Path(os.environ.get("PARITY_ROOT") or Path(__file__).resolve().parents[3])

CRITERIA_SETS = {
    "none": [],
    "soft-bedrooms": [
        {"criterion_id": "bd", "field": "bedrooms", "operator": "gte", "value": 2, "strength": "soft"},
    ],
    "soft-property-type": [
        {"criterion_id": "pt", "field": "property_type", "operator": "eq", "value": "condo", "strength": "soft"},
    ],
    "soft-district": [
        {"criterion_id": "d", "field": "district_code", "operator": "in", "value": ["D05"], "strength": "soft"},
    ],
    "hard-price-soft-bedrooms-district": [
        {"criterion_id": "p", "field": "price", "operator": "lte", "value": 3000, "unit": "SGD/month", "strength": "hard"},
        {"criterion_id": "bd", "field": "bedrooms", "operator": "gte", "value": 2, "strength": "soft", "weight": 2},
        {"criterion_id": "d", "field": "district_code", "operator": "in", "value": ["D05"], "strength": "soft"},
    ],
    "soft-floor-area-property-type": [
        {"criterion_id": "fa", "field": "floor_area_sqft", "operator": "gte", "value": 800, "unit": "sqft", "strength": "soft"},
        {"criterion_id": "pt", "field": "property_type", "operator": "eq", "value": "hdb", "strength": "soft"},
    ],
    "soft-price-minimize-hard-bedrooms": [
        {"criterion_id": "p", "field": "price", "operator": "lte", "value": 4000, "unit": "SGD/month",
         "strength": "soft", "utility": "minimize"},
        {"criterion_id": "bd", "field": "bedrooms", "operator": "gte", "value": 2, "strength": "hard"},
    ],
    "soft-three-features": [
        {"criterion_id": "fa", "field": "floor_area_sqft", "operator": "gte", "value": 700, "unit": "sqft",
         "strength": "soft", "utility": "maximize"},
        {"criterion_id": "bd", "field": "bedrooms", "operator": "gte", "value": 3, "strength": "soft"},
        {"criterion_id": "pt", "field": "property_type", "operator": "eq", "value": "condo", "strength": "soft"},
    ],
    "all-four-features": [
        {"criterion_id": "fa", "field": "floor_area_sqft", "operator": "gte", "value": 700, "unit": "sqft", "strength": "soft"},
        {"criterion_id": "bd", "field": "bedrooms", "operator": "gte", "value": 2, "strength": "soft"},
        {"criterion_id": "pt", "field": "property_type", "operator": "eq", "value": "condo", "strength": "soft"},
        {"criterion_id": "d", "field": "district_code", "operator": "in", "value": ["D21"], "strength": "hard"},
    ],
}
# (label, [(reference index or "pair", seconds)]): "pair" is the paired listing b.
VIEWING = {
    "0.5s": [["pair", 0.5]],
    "30s": [["pair", 30]],
    "90s": [["pair", 90]],
    "200s": [["pair", 200]],
    "multi": [["pair", 40], [0, 100]],
}


def digest(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def table(score_fn=None):
    """{"<criteria set>|<viewing>|<candidate id>": sha256 over the 12 paired score dumps}."""
    sys.path.insert(0, str(ROOT))
    from recommendation.contracts.requirements import Criterion
    from recommendation.repositories.connections import repositories
    from recommendation.ranking.ranker import score

    score_fn = score_fn or score
    listing, geographic = repositories("fixture")
    try:
        rows = listing.retrieve()
        result = {}
        for name, raw in CRITERIA_SETS.items():
            criteria = [Criterion.model_validate(c) for c in raw]
            for label, pattern in VIEWING.items():
                for a in rows:
                    dumps = []
                    for b in rows:
                        viewed = [(b if ref == "pair" else rows[ref], seconds) for ref, seconds in pattern]
                        dumps.append([b.listing_id, score_fn(a, criteria, [], viewed).model_dump(mode="json")])
                    result[f"{name}|{label}|{a.listing_id}"] = digest(dumps)
        return result
    finally:
        listing.close()
        geographic.close()


def main():
    result = table()
    if "--write" in sys.argv:
        OUT.write_text(json.dumps({
            "generated_from": os.environ.get("PARITY_LABEL") or str(ROOT),
            "python": platform.python_version(),
            "criteria_sets": CRITERIA_SETS,
            "viewing": VIEWING,
            "digests": result,
        }, indent=1, sort_keys=True) + "\n")
        print(f"wrote {OUT} ({len(result)} groups)")
    else:
        print(json.dumps(result, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
