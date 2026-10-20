import math


def haversine(a, b):
    vals = (a.latitude, a.longitude, b.latitude, b.longitude)
    if any(v is None or not math.isfinite(v) for v in vals):
        return None
    if not (
        -90 <= vals[0] <= 90
        and -90 <= vals[2] <= 90
        and -180 <= vals[1] <= 180
        and -180 <= vals[3] <= 180
    ):
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, vals)
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def numeric_similarity(a, b, scale):
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("positive finite scale required")
    if a is None or b is None:
        return None, None
    if not math.isfinite(a) or not math.isfinite(b):
        raise ValueError("finite feature values required")
    diff = abs(a - b)
    return max(0, 1 - diff / scale), diff
