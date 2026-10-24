"""P4 part 1 (spec section 6, R1c, R10 URA part, R12): native geography catalog, resolver and
distances, and their use in the pool / evidence path (fixture data mode, scripted controller,
private state files behind the conftest isolation guard)."""
import json, math, time
from pathlib import Path

import numpy as np
import pytest

from recommendation.agent.controller import ScriptedController
from recommendation.config import RecommendationConfig
from recommendation.contracts.request import RecommendationRequest
from recommendation.contracts.requirements import Criterion
from recommendation.geography import catalog as geo_catalog
from recommendation.geography import distance
from recommendation.geography.catalog import GeoCatalog, area_segments, place_catalog, static_catalog
from recommendation.geography.evidence import AREA_METHOD, CENTROID_METHOD, EXIT_METHOD, MISSING_COORDINATES, measure
from recommendation.geography.normalize import explicit_station, is_generic_mrt, normalize_place, requested_mode
from recommendation.geography.resolver import resolve
from recommendation.models.listing_summary import ListingSummary
from recommendation.repositories.connections import repositories
from recommendation.repositories.geographic_repository import Neo4jGeographicRepository
from recommendation.service import RecommendationService
from recommendation.state import pool_cache as pool_cache_module
from recommendation.state.store import StateStore

ROOT = Path(__file__).resolve().parents[2]
MAX = 5  # entity_resolution.max_candidates in the committed YAML (asserted below)


@pytest.fixture(autouse=True)
def fresh_memory():
    pool_cache_module.MEMORY.clear()
    yield
    pool_cache_module.MEMORY.clear()


def places(rows, namespaces=("OSM:",)):
    return place_catalog("test:places", json.dumps(rows, sort_keys=True), lambda: rows, namespaces)


# A small OSM-like place list (real IDs/names of the live graph) for name resolution tests.
OSM_ROWS = [
    {"place_id": "OSM:way:54519165", "name": "National University of Singapore", "name_local": "新加坡国立大学",
     "category": "School", "latitude": 1.2966, "longitude": 103.7764},
    {"place_id": "OSM:way:115105262", "name": "Nanyang Technological University", "name_local": "南洋理工大学",
     "category": "School", "latitude": 1.3483, "longitude": 103.6831},
    {"place_id": "OSM:node:1", "name": "Cold Storage", "category": "Supermarket", "latitude": 1.30, "longitude": 103.80},
    {"place_id": "OSM:node:2", "name": "Cold Storage", "category": "Supermarket", "latitude": 1.31, "longitude": 103.81},
    {"place_id": "OSM:node:3", "name": "Kopi Place", "alt_names": ["Kopi Corner"], "full_name": "Kopi Place Food Court",
     "category": "FoodCourt", "latitude": 1.32, "longitude": 103.82},
    {"place_id": "OSM:node:4", "name": "Nowhere Clinic", "category": "Clinic", "latitude": None, "longitude": None},
]


@pytest.fixture(scope="module")
def cat():
    return GeoCatalog(static_catalog(), places(OSM_ROWS))


def listing(listing_id, lat, lon):
    return ListingSummary.model_validate({"listing_id": listing_id, "listing_type": "RENT", "latitude": lat,
                                          "longitude": lon, "price": 2000, "source": "test"})


def distance_criterion(**target):
    return Criterion.model_validate({"criterion_id": "geo", "field": "distance", "operator": "lte", "value": 5000,
                                     "unit": "m", "strength": "hard", "distance_type": "straight_line", **target})


# --- normalisation parity ------------------------------------------------------------------
def test_normalization_matches_shared_ts_fixture():
    """R12: the same fixture is run against TS normalizePlace (tests/native-geography-parity.test.ts)."""
    fixture = json.loads((ROOT / "tests/fixtures/place-normalization.json").read_text())
    assert len(fixture["cases"]) >= 300
    mismatches = [(c["input"], c["expected"], normalize_place(c["input"])) for c in fixture["cases"]
                  if normalize_place(c["input"]) != c["expected"]]
    assert not mismatches
    # JS regex semantics that differ from Python defaults are covered by explicit cases.
    inputs = {c["input"] for c in fixture["cases"]}
    assert {"Clementi\x85MRT", "Clementi﻿MRT", "clementi mrt\n", "ΟΔΟΣ", "İstanbul"} <= inputs


# --- catalog -------------------------------------------------------------------------------

def test_mention_rules_match_shared_ts_fixture():
    """Explicit-station, rail-mode and generic-MRT rules (TS geo-intake isExplicitStation /
    isGenericMrt, locations stationMode) checked by the same fixture as the TS test."""
    fixture = json.loads((ROOT / "tests/fixtures/place-mention-rules.json").read_text())
    assert len(fixture["cases"]) >= 70
    for c in fixture["cases"]:
        got = (explicit_station(c["input"]), requested_mode(c["input"]), is_generic_mrt(c["input"]))
        assert got == (c["explicit_station"], c["requested_mode"], c["generic_mrt"]), c["input"]

def test_static_catalog_counts_cache_and_version():
    first = static_catalog()
    assert (len(first.stations), sum(len(s.exit_ids) for s in first.stations), len(first.areas), len(first.regions)) \
        == (188, 613, 55, 5)
    assert {r.place_id for r in first.regions} == {
        "URA:REGION:CENTRAL", "URA:REGION:EAST", "URA:REGION:NORTH", "URA:REGION:NORTH-EAST", "URA:REGION:WEST"}
    assert sum(len(a.areas) for a in first.regions) == 55
    assert sum(len(a.segments) for a in first.areas) == 40505 - sum(len(p) for a in first.areas for p in a.polygons)
    assert static_catalog() is first  # process-level cache (never per request)
    assert first.version["lta"]["version"].startswith("lta-mrt-exits-")
    assert first.version["ura"]["version"].startswith("ura-mp2019-")


def test_static_catalog_hash_check(tmp_path):
    files = dict(geo_catalog.STATIC_FILES)
    tampered = tmp_path / "ura_planning_areas.geojson"
    tampered.write_bytes(files["polygons"].read_bytes() + b"\n")
    with pytest.raises(geo_catalog.CatalogError, match="AREA_CATALOG_HASH_MISMATCH"):
        geo_catalog.load_static({**files, "polygons": tampered})
    exits = tmp_path / "lta_mrt_exits.geojson"
    exits.write_bytes(b"{}")
    with pytest.raises(geo_catalog.CatalogError, match="LOCATION_CATALOG_HASH_MISMATCH"):
        geo_catalog.load_static({**files, "exits": exits})


def test_place_catalog_cache_and_catalog_version():
    listing_repo, geo = repositories("fixture")
    v1 = geo.catalog_version()
    assert geo.place_catalog() is geo.place_catalog()  # cached by (source, version)
    assert geo.catalog_version() == v1
    geo.places.append({**geo.places[0], "place_id": "fixture-other"})
    assert geo.catalog_version() != v1  # place change -> new catalog version (pool_key/data_version)
    assert "fixture-other" in geo.place_catalog().by_id

    repo = Neo4jGeographicRepository(None, "neo4j")
    datasets = [{"dataset_id": "osm_sg_places", "snapshot_sha256": "a" * 64}]
    calls = []

    def query(q, **params):
        calls.append(q)
        if "Dataset" in q:
            return datasets
        return [{"place_id": "OSM:node:9", "name": "X", "category": "Park", "latitude": 1.3, "longitude": 103.8}]

    repo.query = query
    version = repo.catalog_version()
    first = repo.place_catalog()
    assert repo.place_catalog() is first and sum("p.active" in q for q in calls) == 1  # one place read per snapshot
    datasets[0]["snapshot_sha256"] = "b" * 64
    assert repo.catalog_version() != version and repo.place_catalog() is not first
    datasets.clear()
    from recommendation.state.data_version import VersionUnavailable
    with pytest.raises(VersionUnavailable):
        repo.catalog_version()


# --- resolution ----------------------------------------------------------------------------
def test_committed_max_candidates():
    assert RecommendationConfig.default().entity_resolution.max_candidates == MAX


def test_exact_match_every_area_region_and_station(cat):
    """TS parity (tests/locations.test.ts): every area by name and first alias, every region,
    every station by its name (explicit station), MRT/LRT twins by mode."""
    aliases = json.loads((ROOT / "knowledge/locations/area-aliases.json").read_text())
    for area in cat.static.areas:
        for name in (area.name, aliases[area.name][0]):
            r = resolve(cat, destination_name=name, max_candidates=MAX)
            assert (r.status, r.entity_id, r.entity_kind) == ("resolved", area.place_id, "ura_area"), name
    for region in cat.static.regions:
        for name in (region.name, aliases[region.name][0]):
            assert resolve(cat, destination_name=name, max_candidates=MAX).entity_id == region.place_id, name
    for station in cat.static.stations:
        r = resolve(cat, destination_name=station.name, max_candidates=MAX)
        assert (r.entity_id, r.entity_kind, r.reason) == (station.place_id, "lta_station", "unique_exact_station_match")
    for name in ("BUKIT PANJANG", "CHOA CHU KANG", "PUNGGOL"):
        base = name.replace(" ", "_")
        assert resolve(cat, destination_name=name + " MRT", max_candidates=MAX).entity_id == f"LTA:MRT:{base}"
        assert resolve(cat, destination_name=name + " LRT", max_candidates=MAX).entity_id == f"LTA:LRT:{base}"
    r = resolve(cat, destination_id="URA:AREA:CLEMENTI", max_candidates=MAX)
    assert (r.status, r.method, r.reason) == ("resolved", "id", "verified_id")


def test_alias_resolution_area_vs_station(cat):
    for name in ("金文泰", "Clementi", "克莱门特", "金文台", "clementi"):
        assert resolve(cat, destination_name=name, max_candidates=MAX).entity_id == "URA:AREA:CLEMENTI", name
    for name in ("Clementi MRT", "ClementiMRT", "金文泰地铁站", "克莱门特MRT", "CLEMENTI MRT STATION"):
        r = resolve(cat, destination_name=name, max_candidates=MAX)
        assert (r.entity_id, r.entity_kind) == ("LTA:MRT:CLEMENTI", "lta_station"), name
    assert resolve(cat, destination_name="西区", max_candidates=MAX).entity_id == "URA:REGION:WEST"
    assert resolve(cat, destination_name="Kopi Corner", max_candidates=MAX).entity_id == "OSM:node:3"  # alt_names
    assert resolve(cat, destination_name="Kopi Place Food Court", max_candidates=MAX).entity_id == "OSM:node:3"
    assert resolve(cat, destination_name="南洋理工大学", max_candidates=MAX).entity_id == "OSM:way:115105262"


def test_multiple_candidates_are_ambiguous_never_first_pick(cat):
    same_name = resolve(cat, destination_name="cold storage", max_candidates=MAX)
    assert (same_name.status, same_name.entity_id) == ("ambiguous", None)
    assert [c[0] for c in same_name.candidates] == ["OSM:node:1", "OSM:node:2"]
    assert [c[2] for c in same_name.candidates] == ["osm_place", "osm_place"]
    assert same_name.reason == "multiple_exact_matches (2)"
    limited = resolve(cat, destination_name="Cold Storage", max_candidates=1)
    assert len(limited.candidates) == 1 and limited.reason == "multiple_exact_matches (2)"
    assert limited.matches == ("OSM:node:1", "OSM:node:2")  # every match kept for ID/bind checks
    # TS rule (geo-intake isExplicitStation): only MRT/LRT/地铁站/地铁 make a station request, so
    # without them the URA area wins (area before station); with them the mode picks one twin.
    for name in ("Bukit Panjang", "Bukit Panjang Station", "武吉班让站"):
        assert resolve(cat, destination_name=name, max_candidates=MAX).entity_id == "URA:AREA:BUKIT_PANJANG", name
    assert resolve(cat, destination_name="Clementi Station", max_candidates=MAX).entity_id == "URA:AREA:CLEMENTI"
    assert resolve(cat, destination_name="Bukit Panjang MRT", max_candidates=MAX).entity_id == "LTA:MRT:BUKIT_PANJANG"
    assert resolve(cat, destination_name="Bukit Panjang LRT", max_candidates=MAX).entity_id == "LTA:LRT:BUKIT_PANJANG"


def test_no_match_and_absent_place_are_unresolved_not_invented(cat):
    for name in ("Atlantis", "NUS ISS", "NUS-ISS", "Institute of Systems Science", "National University"):
        r = resolve(cat, destination_name=name, max_candidates=MAX)
        assert (r.status, r.entity_id, r.reason, r.candidates) == ("unresolved", None, "no_exact_match", ()), name
    # The TS NUS/NTU name map still applies (TS parity), exact match only.
    for name in ("NUS", "nus", "新加坡国立大学", "新国立大学"):
        assert resolve(cat, destination_name=name, max_candidates=MAX).entity_id == "OSM:way:54519165", name
    assert resolve(cat, destination_name="NTU", max_candidates=MAX).entity_id == "OSM:way:115105262"
    assert resolve(cat, destination_name="Station", max_candidates=MAX).reason == "empty_name"


def test_generic_mrt_name_is_the_mrt_station_category(cat):
    """TS geo-intake parity: a generic MRT mention (isGenericMrt) is category MRTStation."""
    for name in ("MRT", "地铁站", "nearest MRT station", "最近的地铁站", "any MRT 站"):
        r = resolve(cat, destination_name=name, max_candidates=MAX)
        assert (r.status, r.entity_id, r.entity_kind, r.method) == ("resolved", "MRTStation", "category",
                                                                    "name->category"), name
    r = resolve(cat, destination_id="LTA:MRT:CLEMENTI", destination_name="MRT", max_candidates=MAX)
    assert (r.status, r.entity_id, r.reason) == ("resolved", "LTA:MRT:CLEMENTI", "verified_id; generic MRT name")
    r = resolve(cat, destination_id="GOOGLE:1", destination_name="地铁站", max_candidates=MAX)
    assert (r.status, r.entity_id, r.method) == ("resolved", "MRTStation", "id_then_name")


def test_destination_without_coordinates_is_unresolved(cat):
    """P4 review: an OSM place without valid coordinates is not a usable destination, by ID or by
    name (criterion-level problem, like a category without located places)."""
    for target in ({"destination_id": "OSM:node:4"}, {"destination_name": "Nowhere Clinic"},
                   {"destination_id": "OSM:node:4", "destination_name": "Nowhere Clinic"}):
        r = resolve(cat, max_candidates=MAX, **target)
        assert (r.status, r.entity_id) == ("unresolved", None), target
        assert r.reason.endswith("destination_without_coordinates (OSM:node:4)"), target


def test_id_namespaces_invalid_ids_and_conflicts(cat):
    r = resolve(cat, destination_id="GOOGLE:place:1", max_candidates=MAX)
    assert (r.status, r.reason) == ("unresolved", "unsupported_namespace")
    r = resolve(cat, destination_id="GOOGLE:place:1", destination_name="金文泰", max_candidates=MAX)
    assert (r.status, r.entity_id, r.method) == ("resolved", "URA:AREA:CLEMENTI", "id_then_name")
    assert r.reason == "unsupported_namespace; unique_exact_area_match"
    r = resolve(cat, destination_id="URA:AREA:ATLANTIS", max_candidates=MAX)
    assert (r.status, r.reason) == ("unresolved", "id_not_found")
    r = resolve(cat, destination_id="URA:AREA:ATLANTIS", destination_name="Clementi MRT", max_candidates=MAX)
    assert (r.status, r.entity_id, r.reason) == ("resolved", "LTA:MRT:CLEMENTI", "id_not_found; unique_exact_station_match")
    r = resolve(cat, destination_id="LTA:EXIT:24671", max_candidates=MAX)  # an exit is never the entity (R1c)
    assert (r.status, r.reason) == ("unresolved", "exit_not_destination")
    conflict = resolve(cat, destination_id="URA:AREA:CLEMENTI", destination_name="Bedok", max_candidates=MAX)
    assert (conflict.status, conflict.reason, conflict.entity_id) == ("ambiguous", "id_name_conflict", None)
    assert {c[0] for c in conflict.candidates} == {"URA:AREA:CLEMENTI", "URA:AREA:BEDOK"}
    agree = resolve(cat, destination_id="URA:AREA:CLEMENTI", destination_name="金文泰", max_candidates=MAX)
    assert (agree.status, agree.entity_id, agree.method) == ("resolved", "URA:AREA:CLEMENTI", "id+name")
    ambiguous_name = resolve(cat, destination_id="OSM:node:2", destination_name="Cold Storage", max_candidates=MAX)
    assert ambiguous_name.entity_id == "OSM:node:2"  # the ID is among the name's candidates: consistent
    other = resolve(cat, destination_id="OSM:node:3", destination_name="Cold Storage", max_candidates=MAX)
    assert other.status == "ambiguous" and {c[0] for c in other.candidates} == {"OSM:node:1", "OSM:node:2", "OSM:node:3"}
    # Consistency uses ALL the name's matches, not only the listed candidates.
    listed_one = resolve(cat, destination_id="OSM:node:2", destination_name="Cold Storage", max_candidates=1)
    assert (listed_one.status, listed_one.entity_id) == ("resolved", "OSM:node:2")


def test_categories(cat):
    for category in ("MRTStation", "LRTStation", "School", "Supermarket"):
        r = resolve(cat, category=category, max_candidates=MAX)
        assert (r.status, r.entity_id, r.entity_kind) == ("resolved", category, "category"), category
    assert resolve(cat, category="Park", max_candidates=MAX).reason == "category_has_no_entities"
    assert resolve(cat, category="Clinic", max_candidates=MAX).reason == "category_has_no_entities"  # no coordinates
    assert resolve(cat, category="Gym", max_candidates=MAX).reason == "unsupported_category"
    assert resolve(cat, category="BusStop", max_candidates=MAX).status == "unresolved"


# --- distances -----------------------------------------------------------------------------
def test_generic_mrt_category_uses_nearest_exit_and_excludes_lrt(cat):
    s = cat.static
    bakau = s.by_id["LTA:LRT:BAKAU"]  # LRT-only station: a point at its exit is nearest to an MRT exit
    points = [(1.3005, 103.8), (1.35, 103.94), (float(bakau.exit_lat[0]), float(bakau.exit_lon[0]))]
    rows = [listing(f"l{i}", *p) for i, p in enumerate(points)]
    res = resolve(cat, category="MRTStation", max_candidates=MAX)
    facts, record = measure(cat, distance_criterion(category="MRTStation"), res, rows, "nearest_exit")
    mrt = [x for x in s.stations if x.mode == "MRT"]
    for (lat, lon), row, fact in zip(points, rows, facts):
        best = min((distance.scalar(lat, lon, a, b), st.place_id, e) for st in mrt
                   for a, b, e in zip(st.exit_lat, st.exit_lon, st.exit_ids))
        assert (fact["value"], fact["destination_id"], fact["method"]) == (best[0], best[1], EXIT_METHOD)
        assert record["nearest_exits"][row.listing_id] == best[2]  # the exit is provenance only
        assert fact["destination_id"].startswith("LTA:MRT:")
    lrt = measure(cat, distance_criterion(category="LRTStation"), resolve(cat, category="LRTStation", max_candidates=MAX),
                  rows[2:], "nearest_exit")[0][0]
    assert (lrt["destination_id"], lrt["value"]) == ("LTA:LRT:BAKAU", 0.0)


def test_station_nearest_exit_and_centroid_alternative(cat):
    station = cat.static.by_id["LTA:MRT:CLEMENTI"]
    res = resolve(cat, destination_id=station.place_id, max_candidates=MAX)
    rows = [listing("a", 1.31, 103.76), listing("b", 1.3152, 103.7650)]
    facts, record = measure(cat, distance_criterion(destination_id=station.place_id), res, rows, "nearest_exit")
    for row, fact in zip(rows, facts):
        expected = min(distance.scalar(row.latitude, row.longitude, a, b) for a, b in zip(station.exit_lat, station.exit_lon))
        assert fact["value"] == expected and fact["destination_id"] == station.place_id and fact["method"] == EXIT_METHOD
        assert record["nearest_exits"][row.listing_id] in station.exit_ids
    centroid = measure(cat, distance_criterion(destination_id=station.place_id), res, rows, "exit_centroid")[0]
    lat0, lon0 = station.centroid
    assert centroid[0]["value"] == distance.scalar(1.31, 103.76, lat0, lon0) and centroid[0]["method"] == CENTROID_METHOD
    with pytest.raises(ValueError):
        measure(cat, distance_criterion(destination_id=station.place_id), res, rows, "walking")


def test_area_and_region_distances(cat):
    s = cat.static
    clementi, west = s.by_id["URA:AREA:CLEMENTI"], s.by_id["URA:REGION:WEST"]
    exit_lat, exit_lon = s.by_id["LTA:MRT:CLEMENTI"].exit_lat[0], s.by_id["LTA:MRT:CLEMENTI"].exit_lon[0]
    d = distance.area_distance([exit_lat, 1.4, np.nan], [exit_lon, 103.95, 103.8], clementi.areas)
    assert d[0] == 0 and d[1] > 1000 and np.isnan(d[2])
    rng = np.random.default_rng(3)
    lat, lon = rng.uniform(1.22, 1.47, 300), rng.uniform(103.6, 104.05, 300)
    region = distance.area_distance(lat, lon, west.areas)
    per_area = np.min([distance.area_distance(lat, lon, a.areas) for a in west.areas], axis=0)
    assert np.allclose(region, per_area, rtol=0, atol=1e-6)  # region = min over its areas
    facts, _ = measure(cat, distance_criterion(destination_id=west.place_id),
                       resolve(cat, destination_id=west.place_id, max_candidates=MAX),
                       [listing("in", float(exit_lat), float(exit_lon)), listing("none", None, None)], "nearest_exit")
    assert (facts[0]["value"], facts[0]["method"], facts[0]["destination_id"]) == (0.0, AREA_METHOD, west.place_id)
    assert (facts[1]["value"], facts[1]["unknown_reason"]) == (None, MISSING_COORDINATES)


def test_distances_match_ts_fixture():
    """Cross-layer: the TS turf/haversine distances of tests/fixtures/geography-distance-parity.json
    (also re-checked by tests/native-geography-parity.test.ts). Areas: same inside/outside verdict
    and <= 1e-4 relative (<= 2 m) outside; stations: equal to 1e-9 relative."""
    fixture = json.loads((ROOT / "tests/fixtures/geography-distance-parity.json").read_text())
    pts = np.array(fixture["points"])
    s = static_catalog()
    for entity_id, expected in fixture["distances"].items():
        expected = np.array(expected, float)
        entity = s.by_id[entity_id]
        if entity_id.startswith("URA:"):
            got = distance.area_distance(pts[:, 0], pts[:, 1], entity.areas)
            assert ((got == 0) == (expected == 0)).all(), entity_id
            outside = expected > 0
            assert np.all(np.abs(got[outside] - expected[outside]) <= np.maximum(1e-4 * expected[outside], 0) + 1e-9)
            assert np.all(np.abs(got[outside] - expected[outside]) <= 2.0)
        else:
            got = np.array([min(distance.scalar(a, b, la, lo) for la, lo in zip(entity.exit_lat, entity.exit_lon))
                            for a, b in pts])
            assert np.allclose(got, expected, rtol=1e-9, atol=0), entity_id


def _square(lon0, lat0, size):
    return [[lon0, lat0], [lon0 + size, lat0], [lon0 + size, lat0 + size], [lon0, lat0 + size], [lon0, lat0]]


def _area(polygons):
    polys = tuple(tuple(np.array(r, float) for r in p) for p in polygons)
    segments, blocks = area_segments(polys)
    allpts = np.vstack([r for p in polys for r in p])
    return geo_catalog.Area("URA:AREA:SYN", "SYN", "TEST REGION", "SY", polys, frozenset(),
                            (allpts[:, 0].min(), allpts[:, 1].min(), allpts[:, 0].max(), allpts[:, 1].max()),
                            segments, blocks)


def test_polygon_holes_and_multipolygons_on_synthetic_shapes():
    # Polygon with a hole: outer 103.80..103.90 x 1.30..1.40, hole 103.84..103.86 x 1.34..1.36.
    holed = _area([[_square(103.80, 1.30, 0.10), _square(103.84, 1.34, 0.02)[::-1]]])
    lat = np.array([1.32, 1.35, 1.35, 1.45, 1.35])
    lon = np.array([103.82, 103.85, 103.845, 103.85, 103.95])
    d = distance.area_distance(lat, lon, holed.areas)
    assert d[0] == 0  # in the ring between outer boundary and hole
    assert d[1] > 0 and d[2] > 0  # inside the hole = outside the area
    # Centre of the hole: 0.01 degree to the nearest hole edge (east/west edge, local projection).
    assert d[1] == pytest.approx(0.01 * distance.K * math.cos(math.radians(1.35)), rel=1e-9)
    assert d[2] == pytest.approx(0.005 * distance.K * math.cos(math.radians(1.35)), rel=1e-9)
    # Due north of the top edge: equals the haversine to the foot point (meridian) within 1e-6.
    assert d[3] == pytest.approx(distance.scalar(1.45, 103.85, 1.40, 103.85), rel=1e-6)
    assert d[4] == pytest.approx(distance.scalar(1.35, 103.95, 1.35, 103.90), rel=2e-4)
    # MultiPolygon: two disjoint squares; a point between them is outside, nearest to either.
    multi = _area([[_square(103.80, 1.30, 0.02)], [_square(103.90, 1.30, 0.02)]])
    lat = np.array([1.31, 1.31, 1.31])
    lon = np.array([103.81, 103.91, 103.86])
    d = distance.area_distance(lat, lon, multi.areas)
    assert d[0] == 0 and d[1] == 0
    assert d[2] == pytest.approx(0.04 * distance.K * math.cos(math.radians(1.31)), rel=1e-9)
    # Pruned result equals brute force.
    rng = np.random.default_rng(5)
    lat, lon = rng.uniform(1.25, 1.45, 500), rng.uniform(103.75, 103.95, 500)
    for area in (holed, multi):
        assert np.array_equal(distance.area_distance(lat, lon, area.areas),
                              distance.area_distance(lat, lon, area.areas, prune=False))


def test_region_distance_performance_8631_points():
    """R10: region distance over 8,631 points (the live count of listings with coordinates);
    synthetic points across Singapore. Timing is printed and recorded in VALIDATION.md."""
    s = static_catalog()
    rng = np.random.default_rng(8631)
    lat, lon = rng.uniform(1.22, 1.47, 8631), rng.uniform(103.6, 104.05, 8631)
    timings = {}
    for region in s.regions:
        started = time.perf_counter()
        d = distance.area_distance(lat, lon, region.areas)
        timings[region.place_id] = time.perf_counter() - started
        assert np.isfinite(d).all() and (d >= 0).all() and (d == 0).any()
        sample = slice(0, 300)
        assert np.allclose(d[sample], distance.area_distance(lat[sample], lon[sample], region.areas, prune=False),
                           rtol=0, atol=1e-6)
    print("region distance seconds for 8,631 points:", {k: round(v, 3) for k, v in timings.items()})
    assert max(timings.values()) < 5.0


# --- service integration (fixture mode) ----------------------------------------------------
def make(tmp_path, name="state.sqlite", config=None, places=None):
    listing_repo, geo = repositories("fixture")
    if places is not None:
        geo.places = places
    return RecommendationService(listing_repo, geo, StateStore(tmp_path / name), ScriptedController(), config=config)


def body(criteria, request_id="g-1", conversation="geo", version=1, previous=None):
    return RecommendationRequest.model_validate({
        "request_id": request_id, "conversation_id": conversation, "requirements_version": version,
        "previous_state_id": previous, "requirements": {"listing_type": "RENT", "criteria": criteria}})


def geo(criterion_id="geo", strength="hard", value=5000, **target):
    return {"criterion_id": criterion_id, "field": "distance", "operator": "lte", "value": value, "unit": "m",
            "strength": strength, "distance_type": "straight_line", **target,
            **({"weight": 1, "utility": "proximity"} if strength == "soft" else {})}


def test_service_resolves_lta_and_ura_targets_and_uses_resolved_entity_in_pool_key(tmp_path):
    service = make(tmp_path)
    try:
        area = service.recommend(body([geo(value=0, destination_id="URA:AREA:BUKIT_TIMAH")]))
        assert area.status == "success", area.error
        [r] = area.resolution
        assert (r.status, r.entity_id, r.entity_kind, r.method) == ("resolved", "URA:AREA:BUKIT_TIMAH", "ura_area", "id")
        # fixture-05..11 lie inside Bukit Timah (0 m); 01-04 in Queenstown; 12 has no coordinates.
        assert {i.listing.listing_id for i in area.recommendations} == {f"fixture-{n:02d}" for n in range(5, 12)}
        assert all(e.method == AREA_METHOD and e.value == 0 for e in area.evidence)
        assert area.pool.excluded_hard_unknown == 1 and area.pool.excluded_hard_fail == 4
        # Same entity by Chinese alias -> same pool_key -> reused; the resolution records the name path.
        alias = service.recommend(body([geo(value=0, destination_name="武吉知马")], "g-2", version=2,
                                       previous=area.state.state_id))
        assert alias.pool.cache == "reused" and alias.pool.pool_key == area.pool.pool_key
        assert (alias.resolution[0].entity_id, alias.resolution[0].method) == ("URA:AREA:BUKIT_TIMAH", "name")
        station = service.recommend(body([geo(value=1500, destination_name="Holland Village MRT")], "g-3",
                                         version=3, previous=alias.state.state_id))
        assert station.status == "success", station.error
        assert station.resolution[0].entity_id == "LTA:MRT:HOLLAND_VILLAGE"
        assert all(e.method == EXIT_METHOD and e.destination_id == "LTA:MRT:HOLLAND_VILLAGE" and e.value <= 1500
                   for e in station.evidence)
        mrt = service.recommend(body([geo(value=600, category="MRTStation")], "g-4", version=4,
                                     previous=station.state.state_id))
        assert mrt.status == "success" and mrt.resolution[0].entity_kind == "category"
        assert all(e.destination_id.startswith("LTA:MRT:") and e.method == EXIT_METHOD for e in mrt.evidence)
    finally:
        service.close()


def test_service_unresolved_hard_target_needs_clarification_and_soft_is_unknown(tmp_path):
    service = make(tmp_path)
    try:
        hard = service.recommend(body([geo(destination_name="NUS ISS")]))
        assert hard.status == "needs_clarification" and not hard.recommendations
        assert hard.pool.size == 0 and hard.pool.excluded_hard_unknown == 12
        [r] = hard.resolution
        assert (r.status, r.reason, r.entity_id) == ("unresolved", "no_exact_match", None)
        assert any("'geo'" in c and "unresolved" in c and "no catalog entity matches" in c for c in hard.clarifications)
        namespace = service.recommend(body([geo(destination_id="GOOGLE:1")], "g-2", "geo-2"))
        assert namespace.status == "needs_clarification" and namespace.resolution[0].reason == "unsupported_namespace"
        ambiguous = service.recommend(body([geo(destination_id="URA:AREA:CLEMENTI", destination_name="Bedok")],
                                           "g-3", "geo-3"))
        assert ambiguous.status == "needs_clarification"
        assert (ambiguous.resolution[0].status, ambiguous.resolution[0].reason) == ("ambiguous", "id_name_conflict")
        assert [c.entity_id for c in ambiguous.resolution[0].candidates] == ["URA:AREA:BEDOK", "URA:AREA:CLEMENTI"]
        assert any("URA:AREA:BEDOK" in c and "different entities" in c for c in ambiguous.clarifications)
        budget = {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 3000,
                  "unit": "SGD/month", "strength": "hard"}
        soft = service.recommend(body([budget, geo("near", "soft", destination_name="NUS ISS")], "g-4", "geo-4"))
        assert soft.status == "success" and soft.resolution[0].status == "unresolved"
        for item in soft.recommendations:
            [result] = [x for x in item.criterion_results if x.criterion_id == "near"]
            assert result.outcome == "unknown" and "near" in item.unknowns
    finally:
        service.close()


def test_id_with_name_end_to_end_invalid_id_fallback_and_conflict(tmp_path):
    """Plan Step 5 through a contract 2.0 request (the TS adapter sends an app target's place_id
    together with its name): invalid or unsupported IDs fall back to the name with the reason
    recorded; an ID and a name naming different entities are ambiguous (needs_clarification); a
    category still stands alone."""
    with pytest.raises(ValueError, match="category alone"):
        Criterion.model_validate({**geo(category="School"), "destination_name": "Fixture School"})
    with pytest.raises(ValueError, match="category alone"):
        Criterion.model_validate(geo())
    service = make(tmp_path)
    try:
        fallback = service.recommend(body([geo(value=1500, destination_id="URA:AREA:ATLANTIS",
                                               destination_name="Holland Village MRT")]))
        assert fallback.status == "success", fallback.error
        [r] = fallback.resolution
        assert (r.status, r.entity_id, r.method, r.reason) == (
            "resolved", "LTA:MRT:HOLLAND_VILLAGE", "id_then_name", "id_not_found; unique_exact_station_match")
        assert (r.original.destination_id, r.original.destination_name) == ("URA:AREA:ATLANTIS", "Holland Village MRT")
        assert all(e.destination_id == "LTA:MRT:HOLLAND_VILLAGE" for e in fallback.evidence)
        namespace = service.recommend(body([geo(value=0, destination_id="GOOGLE:place:1", destination_name="武吉知马")],
                                           "g-2", version=2, previous=fallback.state.state_id))
        assert namespace.status == "success" and namespace.resolution[0].entity_id == "URA:AREA:BUKIT_TIMAH"
        assert namespace.resolution[0].reason == "unsupported_namespace; unique_exact_area_match"
        agree = service.recommend(body([geo(value=0, destination_id="URA:AREA:BUKIT_TIMAH", destination_name="Bukit Timah")],
                                       "g-3", version=3, previous=namespace.state.state_id))
        assert agree.status == "success" and (agree.resolution[0].method, agree.resolution[0].entity_id) == (
            "id+name", "URA:AREA:BUKIT_TIMAH")
        assert agree.pool.pool_key == namespace.pool.pool_key and agree.pool.cache == "reused"
        conflict = service.recommend(body([geo(value=0, destination_id="URA:AREA:BUKIT_TIMAH", destination_name="Bedok")],
                                          "g-4", version=4, previous=agree.state.state_id))
        assert conflict.status == "needs_clarification" and not conflict.recommendations
        assert (conflict.resolution[0].status, conflict.resolution[0].reason) == ("ambiguous", "id_name_conflict")
        assert any("'geo'" in c and "URA:AREA:BEDOK" in c for c in conflict.clarifications)
    finally:
        service.close()


def test_service_destination_without_coordinates_needs_clarification(tmp_path):
    """P4 review: a destination_id naming a place without coordinates is a criterion-level
    problem (R8: needs_clarification), not a no_match with every listing unknown."""
    rows = [{"place_id": "OSM:node:4", "name": "Nowhere Clinic", "category": "Clinic", "latitude": None,
             "longitude": None}]
    service = make(tmp_path, places=rows)
    try:
        response = service.recommend(body([geo(destination_id="OSM:node:4")]))
        assert response.status == "needs_clarification" and response.pool.size == 0
        [r] = response.resolution
        assert (r.status, r.entity_id) == ("unresolved", None)
        assert r.reason == "verified_id; destination_without_coordinates (OSM:node:4)"
        assert any("'geo'" in c and "no valid coordinates" in c for c in response.clarifications)
    finally:
        service.close()


def test_service_missing_coordinates_are_unknown_not_fail(tmp_path):
    """R8: a listing without coordinates is excluded as hard-unknown (never failed, never 0 m);
    with no criterion-level problem the status is not needs_clarification."""
    service = make(tmp_path)
    try:
        response = service.recommend(body([geo(value=100000, category="MRTStation")]))
        assert response.status == "success" and response.pool.excluded_hard_unknown == 1
        assert response.pool.excluded_hard_fail == 0
        assert response.pool.unknown_reasons == {f"distance: {MISSING_COORDINATES}": 1}
        assert "fixture-12" not in {i.listing.listing_id for i in response.recommendations}
    finally:
        service.close()


def test_geography_and_resolution_settings_are_in_effect(tmp_path):
    """geography.station_distance_method and entity_resolution.max_candidates come from the
    settings file (no code defaults): exit_centroid changes the method and the pool_key
    (geography digest); max_candidates bounds the candidates of an ambiguous target."""
    def run(name, request, config=None):
        service = make(tmp_path, name, config)
        try:
            return service.recommend(request)
        finally:
            service.close()

    target = [geo(value=1500, destination_id="LTA:MRT:HOLLAND_VILLAGE")]
    exits = run("exit.sqlite", body(target))
    centroid_config = RecommendationConfig.default().with_overrides({"geography.station_distance_method": "exit_centroid"})
    centroid = run("centroid.sqlite", body(target), centroid_config)
    assert {e.method for e in exits.evidence} == {EXIT_METHOD}
    assert {e.method for e in centroid.evidence} == {CENTROID_METHOD}
    assert exits.pool.pool_key != centroid.pool.pool_key
    one = RecommendationConfig.default().with_overrides({"entity_resolution.max_candidates": 1})
    ambiguous = run("one.sqlite", body([geo(destination_id="URA:AREA:CLEMENTI", destination_name="Bedok")]), one)
    assert ambiguous.status == "needs_clarification" and len(ambiguous.resolution[0].candidates) == 1
