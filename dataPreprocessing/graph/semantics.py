"""Pure validation/normalization rules for the Singapore PropertyGuru projection.

IDs below are deterministic local IDs, NOT official LTA identifiers. Only exact
normalized names (and reviewed aliases) merge; no fuzzy matching or geocoding.
"""
import math
import re
import unicodedata
from datetime import date, datetime

NORMALIZATION_VERSION = "sg-station-name-mode-v1"
# Reviewed spelling aliases, not guesses based on geographic proximity.
NAME_ALIASES = {"harbour front": "harbourfront"}
DISPLAY_NAMES = {"harbourfront": "HarbourFront", "one north": "one-north", "macpherson": "MacPherson"}
CODE_PATTERN = re.compile(r"^(?:(?:NS|EW|CG|CE|CC|NE|DT|TE|BP|SE|SW|PE|PW|CR|CP|JE|JS|JW|DE)\d+|STC|PTC|CG)$", re.I)


def nonnegative_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def metric_status(raw):
    return "missing" if raw is None else "reported" if nonnegative_number(raw) is not None else "invalid"


def location_for(latitude, longitude):
    """Reject non-finite, swapped, zero and out-of-region coordinates, never impute."""
    if latitude is None or longitude is None:
        return None, "missing"
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None, "invalid"
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError, OverflowError):
        return None, "invalid"
    # Conservative Singapore bounding box. Not an exact country polygon.
    if not (math.isfinite(lat) and math.isfinite(lon) and 1.15 <= lat <= 1.50 and 103.55 <= lon <= 104.20):
        return None, "invalid"
    return {"latitude": lat, "longitude": lon, "srid": 4326}, "valid"


def source_timestamp(value):
    # Preserve the source's timezone (or its lack of one); do not invent UTC.
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip()).isoformat()
        except ValueError:
            pass
    return None


def normalize_station(value):
    if not isinstance(value, str) or not value.strip():
        return None
    alias = value.strip()
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", alias))
    status = "under_construction" if re.search(r"\(\s*U\s*/\s*C\s*\)", normalized, re.I) else "unspecified"
    text = re.sub(r"\(\s*U\s*/\s*C\s*\)", "", normalized, flags=re.I).strip()
    codes = []
    # Accept multiple interchange codes, without treating a line code as a name.
    while text:
        match = re.match(r"^([A-Za-z]+\d*|STC|PTC)(?:\s*[/,]\s*|\s+)(.*)$", text)
        if not match or not CODE_PATTERN.fullmatch(match[1]):
            break
        codes.append(match[1].upper())
        text = match[2].strip()
    if CODE_PATTERN.fullmatch(text):
        return None  # A code alone is not a station name.
    suffix = re.search(r"\b(MRT|LRT)(?:\s+Station)?\s*$", text, re.I)
    # This column is nearest_mrt; bare names default to MRT, explicitly tagged.
    inferred_lrt = any(code.startswith(("BP", "SE", "SW", "PE", "PW")) or code in ("STC", "PTC") for code in codes)
    mode = suffix[1].upper() if suffix else "LRT" if inferred_lrt else "MRT"
    name = text[:suffix.start()].strip() if suffix else text
    key = re.sub(r"[^\w]+", " ", name.casefold()).strip()
    key = NAME_ALIASES.get(key, key)
    if not key:
        return None
    return {
        "station_id": f"SG:{mode}:{key.replace(' ', '-')}",
        "name": DISPLAY_NAMES.get(key, key.title()),
        "name_key": key,
        "transport_mode": mode,
        "codes": sorted(set(codes)),
        "alias": alias,
        "station_status": status,
        "identity_method": "normalized_name_and_mode",
        "normalization_version": NORMALIZATION_VERSION,
    }


def distance_label(distance, minutes):
    parts = []
    if distance is not None:
        parts.append(f"{distance:g} m")
    if minutes is not None:
        parts.append(f"{minutes:g} min")
    return "平台 " + " / ".join(parts) if parts else "平台距离未知"


LISTING_FIELDS = (
    "listing_type", "title", "property_type", "price", "currency", "psf",
    "bedrooms", "bathrooms", "floor_area_sqft", "land_area_sqft",
    "postal_code", "tenure_category", "latitude", "longitude", "url",
    "build_year", "mrt_distance_m", "mrt_walking_mins",
)
