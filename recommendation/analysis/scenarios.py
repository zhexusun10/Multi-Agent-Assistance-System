"""Representative requirement scenarios for the P7 calibration and evaluation.

Hard scenarios cover: no hard criterion, budget, budget + bedrooms, budget + bedrooms + district,
property type, MRT-category distance, named-station distance, URA region (area) membership and an
amenity-category distance. Soft sets cover: none (every score equal, so D = 0 and cold start stays
active), a single categorical preference, and a mixed numeric + geographic preference set. Values
are ordinary user-style requirements chosen before looking at any result (they are inputs, not
tuned).
"""


def hard(criterion_id, field, operator, value, **extra):
    unit = {"price": "SGD/month", "floor_area_sqft": "sqft", "distance": "m"}.get(field)
    body = {"criterion_id": criterion_id, "field": field, "operator": operator, "value": value,
            "strength": "hard", **extra}
    if unit:
        body["unit"] = unit
    if field == "distance":
        body["distance_type"] = "straight_line"
    return body


def soft(criterion_id, field, operator, value, weight=1, **extra):
    body = hard(criterion_id, field, operator, value, **extra)
    body.update(strength="soft", weight=weight)
    return body


BUDGET_3000 = hard("budget", "price", "lte", 3000)
BUDGET_4000 = hard("budget", "price", "lte", 4000)
BEDROOMS_2 = hard("bedrooms", "bedrooms", "gte", 2)

HARD_SCENARIOS = {
    "none": [],
    "budget": [BUDGET_3000],
    "budget_bedrooms": [BUDGET_3000, BEDROOMS_2],
    "budget_bedrooms_district": [BUDGET_4000, BEDROOMS_2, hard("district", "district_code", "in", ["D15", "D16"])],
    "property_type": [hard("budget", "price", "lte", 3500), hard("type", "property_type", "eq", "hdb flat")],
    "mrt_distance": [BUDGET_4000, hard("mrt", "distance", "lte", 600, category="MRTStation")],
    "station_distance": [hard("budget", "price", "lte", 5000),
                         hard("bishan", "distance", "lte", 1000, destination_id="LTA:MRT:BISHAN")],
    "region": [BUDGET_4000, hard("west", "distance", "lte", 0, destination_id="URA:REGION:WEST")],
    "amenity_distance": [BUDGET_4000, hard("hospital", "distance", "lte", 1500, category="Hospital")],
}

SOFT_SETS = {
    "no_soft": [],
    "district": [soft("pref-district", "district_code", "in", ["D05", "D10", "D15", "D19"])],
    "mixed": [
        soft("cheaper", "price", "lte", 20000, utility="minimize", weight=1),
        soft("pref-bedrooms", "bedrooms", "gte", 2, weight=1),
        soft("near-mrt", "distance", "lte", 800, category="MRTStation", utility="proximity", weight=2),
    ],
}

# Scenarios used by the multi-round evaluation (subset: attribute-only, geographic, no hard).
EVALUATION_SCENARIOS = ("budget_bedrooms", "mrt_distance", "property_type", "none")


def requirements(hard_name, soft_name):
    return {"listing_type": "RENT", "criteria": HARD_SCENARIOS[hard_name] + SOFT_SETS[soft_name]}
