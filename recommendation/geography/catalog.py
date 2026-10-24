"""Geographic catalog: LTA stations + exits, URA planning areas + regions, OSM places.

Static part (identical in fixture and database mode, read from the repository files that the
TS LocationService also reads):

- knowledge/locations/lta-stations.json: 188 stations (LTA:MRT:* / LTA:LRT:*) with 613 exits;
  data/datasets/lta_mrt_exits.geojson must hash to its ``raw_sha256`` (TS parity);
- knowledge/locations/ura-areas.json: 55 planning areas; polygons from
  data/datasets/ura_planning_areas.geojson, which must hash to its ``raw_sha256``; the 5
  regions are the union of their areas (TS: ``URA:REGION:<REGION without ' REGION'>``);
- knowledge/locations/aliases.json (station aliases) and area-aliases.json (area/region
  aliases, also applied to same-named stations, as in TS).

Places: OSM places from Neo4j (``MATCH (p:Place) WHERE p.active = true``) in database mode, the
fixture repository's synthetic places in fixture mode.

Both parts are cached per PROCESS (module level, never per request): the static part keyed by
the (mtime, size) of its files and identified by their versions/hashes; places keyed by their
source and version (Neo4j ``Dataset.snapshot_sha256`` / digest of the fixture list).
``version_of`` gives the catalog version that feeds pool_key and data_version.
"""

import hashlib, json, threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..tracing.trace import digest
from .normalize import normalize_place

ROOT = Path(__file__).resolve().parents[2]
LOCATIONS = ROOT / "knowledge/locations"
DATASETS = ROOT / "data/datasets"
CATALOG_FORMAT = "geo-catalog-v1"  # bump when catalog parsing or indexing changes
# The 10 OSM place categories of the Neo4j Place projection (data/graph/places.py).
OSM_CATEGORIES = ("CampusBuilding", "Clinic", "FoodCourt", "Hospital", "Library", "Mall", "Market", "Park",
                  "School", "Supermarket")
# Station categories: nearest LTA station of one mode (TS MRTStation excludes LRT).
STATION_CATEGORIES = {"MRTStation": "MRT", "LRTStation": "LRT"}
ID_NAMESPACES = ("OSM:", "LTA:", "URA:")
BLOCK_SEGMENTS = 256  # polygon segments per pruning block (distance.py)
PLACE_CACHE_ENTRIES = 8


class CatalogError(Exception):
    """A catalog source is missing, inconsistent or fails its hash check."""


def _read(path):
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CatalogError(f"Geographic catalog file unavailable: {path.relative_to(ROOT)}") from exc


def _json(path):
    return json.loads(_read(path).decode("utf-8"))


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_point(lat, lon):
    """Finite WGS84 coordinates in range (same rule as ranking.metrics.haversine)."""
    try:
        return (lat is not None and lon is not None and np.isfinite(lat) and np.isfinite(lon)
                and -90 <= lat <= 90 and -180 <= lon <= 180)
    except TypeError:
        return False


# --- entities ------------------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class Station:
    place_id: str
    name: str
    mode: str  # MRT | LRT
    base: str  # name without ' MRT STATION' / ' LRT STATION'
    exit_ids: tuple  # 'LTA:EXIT:<object_id>' (provenance only, never an entity)
    exit_codes: tuple
    exit_lat: np.ndarray
    exit_lon: np.ndarray
    keys: frozenset  # normalised name + aliases

    kind = "lta_station"

    @property
    def centroid(self):
        """Arithmetic mean of the exits (TS entity point; not an official station point)."""
        return float(self.exit_lat.mean()), float(self.exit_lon.mean())


@dataclass(frozen=True, eq=False)
class Area:
    place_id: str
    name: str
    region: str
    code: str
    polygons: tuple  # each polygon: tuple of rings, each ring an (k, 2) array of [lon, lat]
    keys: frozenset
    bbox: tuple  # (min_lon, min_lat, max_lon, max_lat)
    segments: np.ndarray  # (s, 4): lon_a, lat_a, lon_b, lat_b over ALL rings (holes included)
    blocks: tuple  # ((start, stop, min_lon, min_lat, max_lon, max_lat), ...)

    kind = "ura_area"

    @property
    def areas(self):
        return (self,)


@dataclass(frozen=True, eq=False)
class Region:
    place_id: str
    name: str
    areas: tuple
    keys: frozenset

    kind = "ura_region"


@dataclass(frozen=True, eq=False)
class Place:
    place_id: str
    name: str | None
    name_local: str | None
    full_name: str | None
    alt_names: tuple
    category: str | None
    latitude: float | None
    longitude: float | None

    kind = "osm_place"


def area_segments(polygons):
    """All ring segments of a (multi)polygon and pruning blocks of BLOCK_SEGMENTS segments."""
    parts = []
    for polygon in polygons:
        for ring in polygon:
            if len(ring) < 2:
                continue
            if not np.array_equal(ring[0], ring[-1]):  # GeoJSON rings are closed; close if not
                ring = np.vstack([ring, ring[:1]])
            parts.append(np.hstack([ring[:-1], ring[1:]]))
    segments = np.vstack(parts) if parts else np.zeros((0, 4))
    blocks = []
    for start in range(0, len(segments), BLOCK_SEGMENTS):
        chunk = segments[start:start + BLOCK_SEGMENTS]
        lons, lats = chunk[:, [0, 2]], chunk[:, [1, 3]]
        blocks.append((start, start + len(chunk), float(lons.min()), float(lats.min()),
                       float(lons.max()), float(lats.max())))
    return segments, tuple(blocks)


def _keys(names):
    return frozenset(k for k in (normalize_place(n) for n in names if isinstance(n, str)) if k)


# --- static catalog ------------------------------------------------------------------------
@dataclass(eq=False)
class StaticCatalog:
    stations: tuple
    areas: tuple
    regions: tuple
    version: dict
    by_id: dict = field(default_factory=dict)
    mode_exits: dict = field(default_factory=dict)  # mode -> (station index per exit, lat, lon)

    def __post_init__(self):
        self.by_id = {e.place_id: e for e in (*self.stations, *self.areas, *self.regions)}
        for mode in ("MRT", "LRT"):
            stations = [s for s in self.stations if s.mode == mode]
            owner = np.concatenate([np.full(len(s.exit_lat), i) for i, s in enumerate(stations)]) if stations else np.zeros(0, int)
            lat = np.concatenate([s.exit_lat for s in stations]) if stations else np.zeros(0)
            lon = np.concatenate([s.exit_lon for s in stations]) if stations else np.zeros(0)
            self.mode_exits[mode] = (tuple(stations), owner.astype(int), lat, lon)


STATIC_FILES = {
    "stations": LOCATIONS / "lta-stations.json",
    "exits": DATASETS / "lta_mrt_exits.geojson",
    "areas": LOCATIONS / "ura-areas.json",
    "polygons": DATASETS / "ura_planning_areas.geojson",
    "aliases": LOCATIONS / "aliases.json",
    "area_aliases": LOCATIONS / "area-aliases.json",
}


def load_static(files=None):
    files = files or STATIC_FILES
    raw = {name: _read(path) for name, path in files.items()}
    lta = json.loads(raw["stations"])
    if _sha256(raw["exits"]) != lta.get("raw_sha256"):
        raise CatalogError("LOCATION_CATALOG_HASH_MISMATCH: lta_mrt_exits.geojson does not match lta-stations.json")
    ura = json.loads(raw["areas"])
    if _sha256(raw["polygons"]) != ura.get("raw_sha256"):
        raise CatalogError("AREA_CATALOG_HASH_MISMATCH: ura_planning_areas.geojson does not match ura-areas.json")
    aliases = json.loads(raw["aliases"])
    area_aliases = json.loads(raw["area_aliases"])
    stations = []
    for s in lta["stations"]:
        mode = "MRT" if s["name"].endswith(" MRT STATION") else "LRT" if s["name"].endswith(" LRT STATION") else None
        if mode is None or not s.get("reference_points"):
            raise CatalogError(f"Unexpected LTA station entry {s.get('place_id')}")
        base = s["name"][: -len(f" {mode} STATION")]
        points = [p for p in s["reference_points"] if valid_point(p.get("latitude"), p.get("longitude"))]
        if not points:
            raise CatalogError(f"LTA station {s['place_id']} has no valid exit coordinates")
        points.sort(key=lambda p: p["object_id"])
        stations.append(Station(
            place_id=s["place_id"], name=s["name"], mode=mode, base=base,
            exit_ids=tuple(f"LTA:EXIT:{p['object_id']}" for p in points),
            exit_codes=tuple(p.get("exit_code") for p in points),
            exit_lat=np.array([p["latitude"] for p in points], float),
            exit_lon=np.array([p["longitude"] for p in points], float),
            keys=_keys([s["name"], *aliases.get(base, []), *area_aliases.get(base, [])]),
        ))
    if len({s.place_id for s in stations}) != len(stations):
        raise CatalogError("DUPLICATE_STATION_ID")
    features = json.loads(raw["polygons"])["features"]
    areas = []
    for a in ura["areas"]:
        geometry = features[a["geometry_index"]]["geometry"]
        coordinates = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        polygons = tuple(tuple(np.array([pt[:2] for pt in ring], float) for ring in polygon) for polygon in coordinates)
        segments, blocks = area_segments(polygons)
        everything = np.vstack([ring for polygon in polygons for ring in polygon])
        areas.append(Area(
            place_id=a["place_id"], name=a["name"], region=a["region"], code=a.get("code"), polygons=polygons,
            keys=_keys([a["name"], *area_aliases.get(a["name"], [])]),
            bbox=(float(everything[:, 0].min()), float(everything[:, 1].min()),
                  float(everything[:, 0].max()), float(everything[:, 1].max())),
            segments=segments, blocks=blocks,
        ))
    regions = []
    for region in sorted({a.region for a in areas}):
        place_id = "URA:REGION:" + region.replace(" REGION", "", 1).replace(" ", "_")
        regions.append(Region(place_id=place_id, name=region,
                              areas=tuple(a for a in areas if a.region == region),
                              keys=_keys([region, *area_aliases.get(region, [])])))
    version = {
        "format": CATALOG_FORMAT,
        "lta": {"version": lta.get("version"), "raw_sha256": lta.get("raw_sha256"), "file_sha256": _sha256(raw["stations"])},
        "ura": {"version": ura.get("version"), "raw_sha256": ura.get("raw_sha256"), "file_sha256": _sha256(raw["areas"])},
        "aliases_sha256": _sha256(raw["aliases"]),
        "area_aliases_sha256": _sha256(raw["area_aliases"]),
    }
    return StaticCatalog(tuple(sorted(stations, key=lambda s: s.place_id)),
                         tuple(sorted(areas, key=lambda a: a.place_id)),
                         tuple(sorted(regions, key=lambda r: r.place_id)), version)


_LOCK = threading.Lock()
_STATIC = {}  # one entry: file-stat key -> StaticCatalog
_PLACES = OrderedDict()  # (source, version) -> PlaceCatalog, bounded


def static_catalog():
    """The process-wide static catalog; reloaded only when one of its files changes."""
    try:
        key = tuple((name, path.stat().st_mtime_ns, path.stat().st_size) for name, path in STATIC_FILES.items())
    except OSError as exc:
        raise CatalogError("Geographic catalog file unavailable") from exc
    with _LOCK:
        cached = _STATIC.get(key)
        if cached is None:
            cached = load_static()
            _STATIC.clear()
            _STATIC[key] = cached
        return cached


# --- places --------------------------------------------------------------------------------
@dataclass(eq=False)
class PlaceCatalog:
    places: tuple
    source: str  # Evidence.source for place distances
    version: object  # JSON-serialisable version identity
    namespaces: tuple = ("OSM:",)
    by_id: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)  # normalised name -> sorted place ids
    categories: dict = field(default_factory=dict)  # category -> (places with coordinates, lat, lon)
    category_counts: dict = field(default_factory=dict)

    def __post_init__(self):
        self.places = tuple(sorted(self.places, key=lambda p: p.place_id))
        self.by_id = {p.place_id: p for p in self.places}
        names = {}
        for p in self.places:
            for key in _keys([p.name, p.name_local, p.full_name, *p.alt_names]):
                names.setdefault(key, set()).add(p.place_id)
        self.names = {k: sorted(v) for k, v in names.items()}
        for category in sorted({p.category for p in self.places if p.category}):
            members = [p for p in self.places if p.category == category]
            located = tuple(p for p in members if valid_point(p.latitude, p.longitude))
            self.category_counts[category] = len(members)
            self.categories[category] = (located, np.array([p.latitude for p in located], float),
                                         np.array([p.longitude for p in located], float))


def place_from_row(row):
    alt = row.get("alt_names") or ()
    return Place(
        place_id=str(row["place_id"]), name=row.get("name"), name_local=row.get("name_local"),
        full_name=row.get("full_name"), alt_names=tuple(a for a in (alt if isinstance(alt, (list, tuple)) else [alt]) if a),
        category=row.get("category"),
        latitude=float(row["latitude"]) if row.get("latitude") is not None else None,
        longitude=float(row["longitude"]) if row.get("longitude") is not None else None,
    )


def place_catalog(source, version, load, namespaces=("OSM:",), cacheable=True):
    """Process-cached PlaceCatalog for (source, version); ``load()`` returns place rows."""
    key = (source, json.dumps(version, sort_keys=True, default=str))
    if cacheable:
        with _LOCK:
            if key in _PLACES:
                _PLACES.move_to_end(key)
                return _PLACES[key]
    catalog = PlaceCatalog(tuple(place_from_row(r) for r in load()), source, version, tuple(namespaces))
    if cacheable:
        with _LOCK:
            _PLACES[key] = catalog
            while len(_PLACES) > PLACE_CACHE_ENTRIES:
                _PLACES.popitem(last=False)
    return catalog


# --- combined ------------------------------------------------------------------------------
@dataclass(eq=False)
class GeoCatalog:
    static: StaticCatalog
    places: PlaceCatalog

    @property
    def version(self):
        return version_of(self.static, self.places.version, self.places.source)

    def entity(self, entity_id):
        return self.static.by_id.get(entity_id) or self.places.by_id.get(entity_id)

    def supported_namespace(self, entity_id):
        return entity_id.startswith(ID_NAMESPACES) or entity_id.startswith(self.places.namespaces)

    def source_of(self, entity):
        if isinstance(entity, Station):
            return f"lta:{self.static.version['lta']['version']}"
        if isinstance(entity, (Area, Region)):
            return f"ura:{self.static.version['ura']['version']}"
        return self.places.source


def version_of(static, places_version, places_source):
    return digest({"format": CATALOG_FORMAT, "static": static.version,
                   "places": {"source": places_source, "version": places_version}})
