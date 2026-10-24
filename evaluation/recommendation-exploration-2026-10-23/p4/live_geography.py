"""P4 part 1 live check of the native geography, READ-ONLY (PostgreSQL READ ONLY REPEATABLE READ,
Neo4j READ sessions). Database data mode, ScriptedController (the live LLM is never used), a
TEMPORARY state file (never data/.runtime).

1. Place catalog from Neo4j: load time, counts, process cache.
2. Resolution of representative mentions against the live catalog.
3. Distances over the REAL listing coordinates (SQL): region / area / MRTStation / Hospital
   timings, and URA membership compared with the graph's IN_PLANNING_AREA edges.
4. End-to-end requests with hard geography (pool build timing from the trace pool_stats).

Usage (repository root):
  PYTHONPATH=. .venv/bin/python evaluation/recommendation-exploration-2026-10-23/p4/live_geography.py [out.json]
"""
import json, sys, tempfile, time
from pathlib import Path

import numpy as np

from recommendation.agent.controller import ScriptedController
from recommendation.config import RecommendationConfig
from recommendation.contracts.request import RecommendationRequest
from recommendation.geography import catalog as geo_catalog
from recommendation.geography import distance
from recommendation.geography.resolver import resolve
from recommendation.repositories.connections import load_environment, repositories
from recommendation.service import RecommendationService
from recommendation.state.store import StateStore

ROOT = Path(__file__).resolve().parents[3]
MENTIONS = ["NUS", "NTU", "新加坡国立大学", "NUS ISS", "NUS-ISS", "Institute of Systems Science", "Clementi", "金文泰",
            "Clementi MRT", "金文泰地铁站", "Bukit Panjang", "Bukit Panjang Station", "Bukit Panjang LRT", "西区",
            "one-north", "one-north MRT", "Cold Storage", "VivoCity", "Atlantis"]


def timed(fn, *args, **kwargs):
    started = time.perf_counter()
    value = fn(*args, **kwargs)
    return value, round((time.perf_counter() - started) * 1000, 1)


def main(out):
    load_environment()
    settings = RecommendationConfig.default()
    report = {}
    listing, geographic = repositories("database")
    try:
        geo_catalog._PLACES.clear()
        places, cold_ms = timed(geographic.place_catalog)
        _, warm_ms = timed(geographic.place_catalog)
        catalog = geographic.catalog()
        report["place_catalog"] = {
            "places": len(places.places), "categories": places.category_counts, "cold_load_ms": cold_ms,
            "cached_ms": warm_ms, "catalog_version": geographic.catalog_version(), "source": places.source,
        }
        report["resolution"] = {
            m: resolve(catalog, destination_name=m, max_candidates=settings.entity_resolution.max_candidates).summary()
            for m in MENTIONS
        }
        report["resolution"]["id:OSM:way:54519165"] = resolve(
            catalog, destination_id="OSM:way:54519165", max_candidates=5).summary()
        for category in ("MRTStation", "LRTStation", "Hospital", "Gym"):
            report["resolution"][f"category:{category}"] = resolve(catalog, category=category, max_candidates=5).summary()

        rows, retrieve_ms = timed(listing.retrieve)
        lat, lon, valid = distance.coordinates(rows)
        ids = [r.listing_id for r in rows]
        vlat, vlon = lat[valid], lon[valid]
        report["listings"] = {"rows": len(rows), "with_valid_coordinates": int(valid.sum()), "retrieve_ms": retrieve_ms}
        timings = {}
        for region in catalog.static.regions:
            d, ms = timed(distance.area_distance, vlat, vlon, region.areas)
            timings[region.place_id] = {"ms": ms, "inside": int((d == 0).sum()), "max_m": round(float(d.max()), 1)}
        report["region_distance_8631"] = timings
        d, ms = timed(distance.area_distance, vlat, vlon, catalog.static.by_id["URA:AREA:CLEMENTI"].areas)
        report["area_distance_clementi"] = {"ms": ms, "inside": int((d == 0).sum())}
        stations, owner, elat, elon = catalog.static.mode_exits["MRT"]
        (index, dist), ms = timed(distance.nearest, vlat, vlon, elat, elon)
        report["nearest_mrt_exit"] = {"ms": ms, "exits": len(elat), "median_m": round(float(np.median(dist)), 1),
                                      "p95_m": round(float(np.quantile(dist, 0.95)), 1)}
        located, plat, plon = catalog.places.categories["Hospital"]
        (_, dist), ms = timed(distance.nearest, vlat, vlon, plat, plon)
        report["nearest_hospital"] = {"ms": ms, "places": len(located), "within_1500": int((dist <= 1500).sum())}

        # URA membership: Python point-in-polygon vs the graph's IN_PLANNING_AREA edges.
        membership, ms = timed(
            geographic.query,
            "MATCH (l:Listing)-[:IN_PLANNING_AREA]->(a:GeoArea) RETURN l.listing_id AS listing_id, a.location_id AS area_id",
        )
        graph = {str(r["listing_id"]): r["area_id"] for r in membership}
        python = {}
        valid_ids = [i for i, ok in zip(ids, valid) if ok]
        for area in catalog.static.areas:
            inside = distance.points_in_area(vlat, vlon, area)
            for k in np.flatnonzero(inside):
                python.setdefault(valid_ids[k], []).append(area.place_id)
        multi = sum(len(v) > 1 for v in python.values())
        agree = sum(graph.get(i) == (python.get(i) or [None])[0] for i in valid_ids)
        disagree = [{"listing_id": i, "graph": graph.get(i), "python": python.get(i)} for i in valid_ids
                    if graph.get(i) != (python.get(i) or [None])[0]]
        report["ura_membership_vs_graph"] = {
            "graph_edges": len(graph), "python_inside_any": len(python), "python_in_several_areas": multi,
            "agree": agree, "compared": len(valid_ids), "disagreements": disagree[:20], "graph_query_ms": ms,
        }
    finally:
        listing.close()
        geographic.close()

    scenarios = {
        "hard-hospital-1500": [
            {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 4000, "unit": "SGD/month", "strength": "hard"},
            {"criterion_id": "hospital", "field": "distance", "operator": "lte", "value": 1500, "unit": "m",
             "strength": "hard", "category": "Hospital", "distance_type": "straight_line"}],
        "hard-mrt-600-soft-clementi": [
            {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 4000, "unit": "SGD/month", "strength": "hard"},
            {"criterion_id": "mrt", "field": "distance", "operator": "lte", "value": 600, "unit": "m",
             "strength": "hard", "category": "MRTStation", "distance_type": "straight_line"},
            {"criterion_id": "clementi", "field": "distance", "operator": "lte", "value": 2000, "unit": "m",
             "strength": "soft", "weight": 1, "utility": "proximity", "destination_name": "金文泰", "distance_type": "straight_line"}],
        "hard-west-region": [
            {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 4000, "unit": "SGD/month", "strength": "hard"},
            {"criterion_id": "west", "field": "distance", "operator": "lte", "value": 0, "unit": "m",
             "strength": "hard", "destination_id": "URA:REGION:WEST", "distance_type": "straight_line"}],
        "hard-nus-iss-unresolved": [
            {"criterion_id": "iss", "field": "distance", "operator": "lte", "value": 2000, "unit": "m",
             "strength": "hard", "destination_name": "NUS ISS", "distance_type": "straight_line"}],
    }
    report["end_to_end"] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, criteria in scenarios.items():
            listing, geographic = repositories("database")
            store = StateStore(Path(tmp) / "state.sqlite")
            service = RecommendationService(listing, geographic, store, ScriptedController(),
                                            config=settings.with_overrides({"cache.enabled": False}))
            try:
                started = time.perf_counter()
                response = service.recommend(RecommendationRequest.model_validate({
                    "request_id": f"p4-{name}", "conversation_id": f"p4-{name}", "requirements_version": 1,
                    "requirements": {"listing_type": "RENT", "criteria": criteria}}))
                wall = round((time.perf_counter() - started) * 1000, 1)
                trace = json.loads(store.db.execute(
                    "SELECT content FROM traces WHERE trace_id = ?", (response.execution.trace_id,)).fetchone()[0])
            finally:
                service.close()
            report["end_to_end"][name] = {
                "status": response.status, "wall_ms": wall, "pool": response.pool.model_dump() if response.pool else None,
                "resolution": [r.model_dump() for r in response.resolution],
                "returned": len(response.recommendations), "clarifications": response.clarifications,
                "evidence_methods": sorted({e.method for e in response.evidence}),
                "pool_stats": {k: round(v, 1) if isinstance(v, float) else v
                               for k, v in (trace.get("pool_stats") or {}).items()},
            }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: report[k] for k in ("place_catalog", "listings", "region_distance_8631", "nearest_mrt_exit",
                                              "nearest_hospital")}, indent=1, ensure_ascii=False))
    print(json.dumps(report["ura_membership_vs_graph"], indent=1))
    for name, item in report["end_to_end"].items():
        print(name, item["status"], item["wall_ms"], item["pool"] and item["pool"]["size"], item["pool_stats"].get("hard_geo_ms"))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("live-geography.json"))
