"""Straight-line geographic facts for listings against a resolved criterion target.

One fact per (criterion, listing): value (m) or an unknown_reason, the method label, the
destination entity measured to (for a category: the nearest member) and the catalog source.
SQL listing coordinates are canonical; listings without valid coordinates are unknown (never a
failure, never zero). Graph edges (NEAR_PLACE) and platform NEAR_MRT texts are not used.
"""

import numpy as np

from .catalog import STATION_CATEGORIES, Area, Place, Region, Station
from . import distance
from .resolver import describe

POINT_METHOD = "coordinate_haversine"  # OSM place / category member (label kept from pre-P4)
EXIT_METHOD = "haversine.nearest_recorded_exit"  # TS label (src/adapters/recommendation.ts)
CENTROID_METHOD = "haversine.exit_centroid"
AREA_METHOD = "ura_polygon.zero_inside_nearest_edge_local_equirectangular"
UNSUPPORTED_MODE = "unsupported travel mode: route evidence unavailable"
LISTING_WITHOUT_COORDINATES = "listing has no valid coordinates (unknown; never 0 m)"
# Criterion evidence and tool results use the same text. (Until the P4 review the evidence kept
# the pre-P4 graph text "missing valid coordinates/mapping; absent NEAR_PLACE edge is not absence
# of amenity", although NEAR_PLACE edges are no longer read.)
MISSING_COORDINATES = LISTING_WITHOUT_COORDINATES
DESTINATION_WITHOUT_COORDINATES = "destination has no valid coordinates"


def _fact(value=None, method="unavailable", destination_id=None, reason=None, source=None):
    return {"value": value, "method": method, "destination_id": destination_id, "unknown_reason": reason,
            "source": source}


def _points(lat, lon, valid, plat, plon, ids, method, source, provenance=None):
    """Nearest of a set of points; value recomputed with the scalar haversine."""
    facts = [None] * len(lat)
    rows = np.flatnonzero(valid)
    index, _ = distance.nearest(lat[rows], lon[rows], plat, plon)
    for row, j in zip(rows, index):
        value = distance.scalar(lat[row], lon[row], plat[j], plon[j])
        facts[row] = _fact(value, method, ids[j], None, source)
        if provenance is not None:
            provenance[row] = j
    return facts


def measure(catalog, criterion, resolution, listings, station_method):
    """(facts aligned with listings, compact record) for one geographic criterion."""
    lat, lon, valid = distance.coordinates(listings)
    record = {"criterion_id": criterion.criterion_id, "resolution": resolution.summary(),
              "listings": len(listings), "method": None}
    if criterion.field == "commute" or criterion.distance_type != "straight_line":
        facts = [_fact(reason=UNSUPPORTED_MODE) for _ in listings]
    elif not resolution.resolved:
        reason = f"destination {resolution.status}: {describe(resolution)}"
        facts = [_fact(reason=reason) for _ in listings]
    else:
        facts, method, exits = _resolved(catalog, resolution, lat, lon, valid, station_method)
        record["method"] = method
        if exits is not None:  # the exit actually used: provenance only (R1c)
            record["nearest_exits"] = {listings[row].listing_id: exit_id for row, exit_id in exits.items()}
    facts = [f if f is not None else _fact(reason=MISSING_COORDINATES) for f in facts]
    record["known"] = sum(f["value"] is not None for f in facts)
    record["unknown"] = len(facts) - record["known"]
    return facts, record


def _resolved(catalog, resolution, lat, lon, valid, station_method):
    entity, n = resolution.entity, len(lat)
    if resolution.entity_kind == "category" and entity in STATION_CATEGORIES:
        stations, owner, exit_lat, exit_lon = catalog.static.mode_exits[STATION_CATEGORIES[entity]]
        return _stations(catalog, stations, owner, exit_lat, exit_lon, lat, lon, valid, station_method)
    if resolution.entity_kind == "category":
        located, plat, plon = catalog.places.categories[entity]
        facts = _points(lat, lon, valid, plat, plon, [p.place_id for p in located], POINT_METHOD,
                        catalog.places.source)
        return facts, POINT_METHOD, None
    if isinstance(entity, Station):
        owner = np.zeros(len(entity.exit_lat), int)
        return _stations(catalog, (entity,), owner, entity.exit_lat, entity.exit_lon, lat, lon, valid, station_method)
    if isinstance(entity, (Area, Region)):
        source = catalog.source_of(entity)
        values = np.full(n, np.nan)
        rows = np.flatnonzero(valid)
        values[rows] = distance.area_distance(lat[rows], lon[rows], entity.areas)
        facts = [_fact(float(values[i]), AREA_METHOD, entity.place_id, None, source) if valid[i] else None
                 for i in range(n)]
        return facts, AREA_METHOD, None
    if isinstance(entity, Place):
        if not distance.valid_point(entity.latitude, entity.longitude):
            return [_fact(reason=DESTINATION_WITHOUT_COORDINATES) for _ in range(n)], POINT_METHOD, None
        facts = _points(lat, lon, valid, np.array([entity.latitude]), np.array([entity.longitude]),
                        [entity.place_id], POINT_METHOD, catalog.places.source)
        return facts, POINT_METHOD, None
    raise ValueError(f"Unsupported resolved entity kind {resolution.entity_kind}")


def _stations(catalog, stations, owner, exit_lat, exit_lon, lat, lon, valid, station_method):
    """Nearest station: by its nearest recorded exit (TS parity) or by its exit centroid."""
    source = f"lta:{catalog.static.version['lta']['version']}"
    if station_method == "exit_centroid":
        centroids = np.array([s.centroid for s in stations], float).reshape(-1, 2)
        facts = _points(lat, lon, valid, centroids[:, 0], centroids[:, 1], [s.place_id for s in stations],
                        CENTROID_METHOD, source)
        return facts, CENTROID_METHOD, None
    if station_method != "nearest_exit":
        raise ValueError(f"Unsupported geography.station_distance_method {station_method!r}")
    # Exits are grouped by station in place_id order and by object_id inside a station, so the
    # first index on an exact tie is the smallest (station_id, exit object_id).
    exit_ids = [e for s in stations for e in s.exit_ids]
    chosen = {}
    facts = _points(lat, lon, valid, exit_lat, exit_lon, [stations[o].place_id for o in owner], EXIT_METHOD,
                    source, provenance=chosen)
    return facts, EXIT_METHOD, {row: exit_ids[j] for row, j in chosen.items()}


# --- tool helpers (search/query tools, P4 part 2) -------------------------------------------
def entity_facts(catalog, entity, listings, station_method):
    """(facts aligned with listings, method, {row: exit_id} | None) for one catalog entity, with
    exactly the distance rules of criterion evidence (the distance_to tool)."""
    from .resolver import Resolution

    lat, lon, valid = distance.coordinates(listings)
    resolution = Resolution("resolved", "tool", None, entity.place_id, entity.kind, entity=entity)
    facts, method, exits = _resolved(catalog, resolution, lat, lon, valid, station_method)
    return [f if f is not None else _fact(reason=LISTING_WITHOUT_COORDINATES) for f in facts], method, exits


def nearest_stations(catalog, listings, mode, station_method):
    """Nearest LTA station of one mode per listing (the nearest_mrt tool): facts aligned with
    listings (destination_id = the station), the method and {row: exit_id} provenance."""
    stations, owner, exit_lat, exit_lon = catalog.static.mode_exits[mode]
    lat, lon, valid = distance.coordinates(listings)
    facts, method, exits = _stations(catalog, stations, owner, exit_lat, exit_lon, lat, lon, valid, station_method)
    return [f if f is not None else _fact(reason=LISTING_WITHOUT_COORDINATES) for f in facts], method, exits
