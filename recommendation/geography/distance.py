"""Straight-line distances (spec section 6, R1c, R10). One convention for everything:
spherical haversine with R = 6371008.8 m (TS src/domain/spatial.ts, ranking.metrics.haversine).
Neo4j point.distance (about 0.112 % larger) is never used.

- point (OSM place, station exit, exit centroid): haversine. Nearest-of-many is selected with a
  vectorised numpy matrix (rows chunked); the reported value is recomputed with the scalar
  ``ranking.metrics.haversine`` for the chosen point, so it is identical to the pre-P4 values.
- URA area: 0 when the point is inside (even-odd rule over ALL rings of all polygons, so holes
  and MultiPolygons are respected), otherwise the distance to the nearest polygon edge.
- URA region: minimum over its areas (internal edges between adjacent areas lie inside the
  region, so they never undercut the outer boundary).

Edge distance uses a local equirectangular projection centred on each listing point:
x = R·cos(lat_p)·Δlon, y = R·Δlat (radians), then the planar point-to-segment distance.
Accuracy (documented, measured in VALIDATION.md P4): the only first-order error is the change of
cos(lat) between the listing and the edge, |tan(lat)·Δlat| ≈ 2.3e-2·Δlat(rad); across Singapore
(Δlat ≤ 0.35°) that is ≤ 1.4e-4 relative (≤ 0.014 %, ≤ 1.4 m at 10 km); spherical excess is
negligible (< 1e-6 relative below 50 km). Points exactly on an edge may test either side
(distance ≈ 0 either way).

Performance: a bounding-box prefilter per area and per block of BLOCK_SEGMENTS segments
(point-in-polygon and edge distance), lower-bound pruning against the best distance found so
far, and numpy chunks of at most CHUNK_ELEMENTS point×segment pairs.
"""

import math

import numpy as np

from ..ranking.metrics import haversine
from .catalog import valid_point

R = 6371008.8
K = R * math.pi / 180.0  # metres per degree of latitude
CHUNK_ELEMENTS = 1_000_000  # ~1e6 point x segment (or point x point) pairs per numpy chunk


class _Point:
    __slots__ = ("latitude", "longitude")

    def __init__(self, latitude, longitude):
        self.latitude, self.longitude = latitude, longitude


def scalar(lat1, lon1, lat2, lon2):
    """Canonical scalar haversine (ranking.metrics.haversine)."""
    return haversine(_Point(lat1, lon1), _Point(lat2, lon2))


def coordinates(listings):
    """(lat, lon, valid mask) arrays for listings; invalid/missing coordinates are masked."""
    lat = np.array([r.latitude if r.latitude is not None else np.nan for r in listings], float)
    lon = np.array([r.longitude if r.longitude is not None else np.nan for r in listings], float)
    valid = np.array([valid_point(r.latitude, r.longitude) for r in listings], bool)
    return lat, lon, valid


def haversine_matrix(lat, lon, plat, plon):
    """(n, m) haversine distances between n points and m points (degrees in, metres out)."""
    lat1, lon1 = np.radians(lat)[:, None], np.radians(lon)[:, None]
    lat2, lon2 = np.radians(plat)[None, :], np.radians(plon)[None, :]
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return R * 2 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def nearest(lat, lon, plat, plon):
    """Index of the nearest of m points for each of n points (first index on exact ties, so
    callers order the points by ID) and the vectorised distance."""
    n, m = len(lat), len(plat)
    index = np.full(n, -1, int)
    dist = np.full(n, np.inf)
    if not n or not m:
        return index, dist
    rows = max(1, CHUNK_ELEMENTS // m)
    for start in range(0, n, rows):
        d = haversine_matrix(lat[start:start + rows], lon[start:start + rows], plat, plon)
        index[start:start + rows] = d.argmin(axis=1)
        dist[start:start + rows] = d.min(axis=1)
    return index, dist


# --- polygons ------------------------------------------------------------------------------
def points_in_area(lat, lon, area):
    """Even-odd point-in-polygon over every ring of the area (holes and MultiPolygons)."""
    inside = np.zeros(len(lat), bool)
    min_lon, min_lat, max_lon, max_lat = area.bbox
    candidates = np.flatnonzero((lon >= min_lon) & (lon <= max_lon) & (lat >= min_lat) & (lat <= max_lat))
    if not len(candidates):
        return inside
    crossings = np.zeros(len(candidates), int)
    px, py = lon[candidates], lat[candidates]
    for start, stop, _b_min_lon, b_min_lat, b_max_lon, b_max_lat in area.blocks:
        # An edge crosses the +x ray only if min(y) <= py < max(y) and px < max(x).
        pick = np.flatnonzero((py >= b_min_lat) & (py < b_max_lat) & (px < b_max_lon))
        if not len(pick):
            continue
        seg = area.segments[start:stop]
        rows = max(1, CHUNK_ELEMENTS // len(seg))
        for c0 in range(0, len(pick), rows):
            sub = pick[c0:c0 + rows]
            x, y = px[sub][:, None], py[sub][:, None]
            xi, yi, xj, yj = seg[:, 0][None, :], seg[:, 1][None, :], seg[:, 2][None, :], seg[:, 3][None, :]
            spans = (yi > y) != (yj > y)
            with np.errstate(divide="ignore", invalid="ignore"):
                cross = spans & (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
            crossings[sub] += cross.sum(axis=1)
    inside[candidates] = crossings % 2 == 1
    return inside


def _segment_distance(lat, lon, seg):
    """(n, s) planar distances from n points to s segments in each point's local projection."""
    cos = np.cos(np.radians(lat))[:, None]
    ax = (seg[:, 0][None, :] - lon[:, None]) * cos * K
    ay = (seg[:, 1][None, :] - lat[:, None]) * K
    bx = (seg[:, 2][None, :] - lon[:, None]) * cos * K
    by = (seg[:, 3][None, :] - lat[:, None]) * K
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(length > 0, -(ax * dx + ay * dy) / length, 0.0)
    t = np.clip(t, 0.0, 1.0)
    return np.hypot(ax + t * dx, ay + t * dy)


def _box_lower_bound(lat, lon, box):
    """Projected distance from each point to a lon/lat box (a lower bound for anything inside it,
    up to the projection accuracy above)."""
    min_lon, min_lat, max_lon, max_lat = box
    cos = np.cos(np.radians(lat))
    dx = np.maximum(np.maximum(min_lon - lon, lon - max_lon), 0) * cos * K
    dy = np.maximum(np.maximum(min_lat - lat, lat - max_lat), 0) * K
    return np.hypot(dx, dy)


# Slack on bbox lower bounds so projection rounding can never prune the true nearest edge.
_PRUNE_SLACK = 1.0 + 1e-6


def edge_distance(lat, lon, areas, prune=True):
    """Distance from each point to the nearest edge of any of the areas (no inside test)."""
    best = np.full(len(lat), np.inf)
    if not len(lat):
        return best
    order = sorted(areas, key=lambda a: float(np.median(_box_lower_bound(lat, lon, a.bbox))))
    for area in order:
        idx = np.arange(len(lat))
        if prune:
            idx = idx[_box_lower_bound(lat, lon, area.bbox) < best * _PRUNE_SLACK]
        if not len(idx):
            continue
        blocks = area.blocks
        if prune:
            bounds = np.stack([_box_lower_bound(lat[idx], lon[idx], b[2:]) for b in blocks], axis=1)
            blocks = [blocks[j] for j in np.argsort(bounds.min(axis=0), kind="stable")]
            columns = {b[0]: j for j, b in enumerate(area.blocks)}
        for block in blocks:
            start, stop = block[0], block[1]
            sub = idx
            if prune:
                sub = idx[bounds[:, columns[start]] < best[idx] * _PRUNE_SLACK]
            if not len(sub):
                continue
            seg = area.segments[start:stop]
            rows = max(1, CHUNK_ELEMENTS // len(seg))
            for c0 in range(0, len(sub), rows):
                part = sub[c0:c0 + rows]
                d = _segment_distance(lat[part], lon[part], seg).min(axis=1)
                best[part] = np.minimum(best[part], d)
    return best


def area_distance(lat, lon, areas, prune=True):
    """0 inside any of the areas, else the nearest-edge distance; NaN for NaN coordinates.

    ``areas`` is one URA area (``area.areas``) or all areas of a region (``region.areas``)."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    result = np.full(len(lat), np.nan)
    ok = np.flatnonzero(np.isfinite(lat) & np.isfinite(lon))
    if not len(ok):
        return result
    plat, plon = lat[ok], lon[ok]
    inside = np.zeros(len(ok), bool)
    for area in areas:
        todo = np.flatnonzero(~inside)
        if not len(todo):
            break
        inside[todo] |= points_in_area(plat[todo], plon[todo], area)
    out = np.flatnonzero(~inside)
    values = np.zeros(len(ok))
    if len(out):
        values[out] = edge_distance(plat[out], plon[out], areas, prune=prune)
    result[ok] = values
    return result
