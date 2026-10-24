"""Platform-reported nearest-MRT text of listings (SQL properties.nearest_mrt / mrt_distance_m /
mrt_walking_mins, the NEAR_MRT edge of the graph). It is listing text published by the platform:
not verified, not a straight-line distance, and NEVER used to evaluate a criterion (spec
section 6). The nearest_mrt tool shows it, separately labelled, only for listings without
coordinates. Read by ID in a READ ONLY transaction; unavailable in fixture mode.
"""

LABEL = ("platform_reported: listing text from the platform (NEAR_MRT); not verified, not a straight-line "
         "distance, never used for criteria")
SQL = (
    "SELECT listing_id, nearest_mrt, mrt_distance_m, mrt_walking_mins FROM properties "
    "WHERE listing_type=:kind AND listing_id = ANY(CAST(:ids AS BIGINT[])) ORDER BY listing_id"
)


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def platform_mrt(listing, ids):
    """{listing_id: {nearest_mrt, mrt_distance_m, mrt_walking_mins}} or None when this data mode
    has no platform transit text (fixture) or the read failed."""
    reader = getattr(listing, "platform_mrt", None)
    if callable(reader):
        return reader(ids)
    engine = getattr(listing, "engine", None)
    if engine is None:
        return None
    keys = sorted({int(i) for i in ids if str(i).isdecimal()})
    if not keys:
        return {}
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            rows = conn.execute(text(SQL), {"kind": "RENT", "ids": keys}).mappings().all()
    except Exception:
        return None
    return {
        str(r["listing_id"]): {
            "nearest_mrt": r["nearest_mrt"] if isinstance(r["nearest_mrt"], str) else None,
            "mrt_distance_m": _number(r["mrt_distance_m"]),
            "mrt_walking_mins": _number(r["mrt_walking_mins"]),
        }
        for r in rows
    }
