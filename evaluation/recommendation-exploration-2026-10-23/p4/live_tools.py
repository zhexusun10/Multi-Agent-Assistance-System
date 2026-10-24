"""P4 part 2 live smoke of the pipeline/query tools, READ-ONLY (PostgreSQL READ ONLY transactions,
Neo4j READ sessions). Database data mode, ScriptedController only (the live LLM is never used),
a TEMPORARY state file (never data/.runtime); the pool cache is disabled for the direct tool
calls (nothing is written anywhere).

1. End-to-end scripted requests: the tool order in the trace is the fixed pipeline.
2. Direct tool calls on one Dispatcher over the live repositories: resolve_entities, build_pool,
   get_geographic_evidence, rank_candidates; search_places; nearest_mrt (incl. a listing without
   coordinates: platform text, labelled); distance_to; nearby_amenities; district_membership;
   bind_entity for an ambiguous name (multi-candidate rejected, single candidate accepted,
   pool rebuilt with another pool_key).

Usage (repository root):
  PYTHONPATH=. .venv/bin/python evaluation/recommendation-exploration-2026-10-23/p4/live_tools.py [out.json]
"""
import hashlib, json, sys, tempfile, time
from pathlib import Path

from recommendation.agent.controller import ScriptedController
from recommendation.config import RecommendationConfig
from recommendation.contracts.request import RecommendationRequest
from recommendation.repositories.connections import load_environment, repositories
from recommendation.service import RecommendationService
from recommendation.state.store import StateStore
from recommendation.tools.dispatcher import Dispatcher
from recommendation.tools.registry import PIPELINE

ROOT = Path(__file__).resolve().parents[3]
LIVE_STATE = ROOT / "data/.runtime/recommendation/state.sqlite"
BUDGET = {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 4000, "unit": "SGD/month",
          "strength": "hard"}


def geo(criterion_id, value, strength="hard", **target):
    return {"criterion_id": criterion_id, "field": "distance", "operator": "lte", "value": value, "unit": "m",
            "strength": strength, "distance_type": "straight_line", **target,
            **({"weight": 1, "utility": "proximity"} if strength == "soft" else {})}


def md5(path):
    if not path.exists():
        return None
    h = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def request(name, criteria):
    return RecommendationRequest.model_validate({
        "request_id": f"p4b-{name}", "conversation_id": f"p4b-{name}", "requirements_version": 1,
        "requirements": {"listing_type": "RENT", "criteria": criteria}})


def compact(result):
    data = result.data or {}
    return {"status": result.status, "error": result.error.model_dump() if result.error else None,
            "stages": result.completed_stages, "data": data}


def main(out):
    load_environment()
    report = {"live_state_md5_before": md5(LIVE_STATE)}
    settings = RecommendationConfig.default()
    scenarios = {
        "clementi-mrt-1500": [BUDGET, geo("mrt", 1500, destination_name="Clementi MRT")],
        "nus-iss-unresolved": [BUDGET, geo("iss", 2000, destination_name="NUS ISS")],
        "bukit-panjang-station-ambiguous": [BUDGET, geo("bp", 1500, destination_name="Bukit Panjang Station")],
        "soft-jinwentai": [BUDGET, geo("cl", 2000, "soft", destination_name="金文泰")],
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
                response = service.recommend(request(name, criteria))
                wall = round((time.perf_counter() - started) * 1000, 1)
                trace = json.loads(store.db.execute(
                    "SELECT content FROM traces WHERE trace_id = ?", (response.execution.trace_id,)).fetchone()[0])
            finally:
                service.close()
            tools = [s["result"]["tool"] for s in trace["steps"] if s["kind"] == "tool"]
            report["end_to_end"][name] = {
                "status": response.status, "wall_ms": wall, "tool_order": tools,
                "tool_order_is_pipeline": tools == list(PIPELINE),
                "pool_size": response.pool.size if response.pool else None,
                "returned": len(response.recommendations),
                "resolution": [{k: r.model_dump()[k] for k in ("criterion_id", "status", "entity_id", "method", "reason")}
                               | {"candidates": [c.entity_id for c in r.candidates]} for r in response.resolution],
                "clarifications": response.clarifications,
            }

    # Direct tool calls (no store: the pool is built without caching; nothing is written).
    listing, geographic = repositories("database")
    config = settings.with_overrides({"controller.max_tool_calls": 40, "controller.max_resolution_tool_calls": 30,
                                      "controller.timeout_seconds": 600})
    req = request("tools", [BUDGET, geo("bp", 1500, destination_name="Bukit Panjang Station")]).model_copy(
        update={"result_limit": 10})
    d = Dispatcher(req, {"viewed": []}, listing, geographic, {"trace_id": "live-tools", "steps": []}, config=config)
    calls = {}

    def call(label, name, **arguments):
        started = time.perf_counter()
        result = d.call(name, {"context_ref": d.ref, **arguments})
        calls[label] = {"ms": round((time.perf_counter() - started) * 1000, 1), **compact(result)}
        return result

    try:
        for name in PIPELINE:
            call(name, name)
        report["unbound"] = {"pool_key": d.pool_result.key, "partition": d.pool_result.partition,
                             "resolution": d.resolutions["bp"].summary()}
        call("search_cold_storage", "search_places", query="Cold Storage")
        call("search_bp_multi", "search_places", query="Bukit Panjang Station", criterion_id="bp")
        call("bind_multi_rejected", "bind_entity", criterion_id="bp", entity_id="LTA:MRT:BUKIT_PANJANG")
        call("search_bp_mrt", "search_places", query="Bukit Panjang MRT", criterion_id="bp")
        call("bind_single", "bind_entity", criterion_id="bp", entity_id="LTA:MRT:BUKIT_PANJANG")
        call("rank_after_bind_stale", "rank_candidates")
        for name in PIPELINE[1:]:
            call(f"{name}_after_bind", name)
        report["bound"] = {"pool_key": d.pool_result.key, "partition": d.pool_result.partition,
                           "resolution": d.resolutions["bp"].summary(), "selected": d.selected}
        selected = list(d.selected)
        call("nearest_mrt_selected", "nearest_mrt", listing_ids=selected[:5])
        call("district_membership", "district_membership", listing_ids=selected[:5])
        call("nearby_supermarkets", "nearby_amenities", listing_ids=selected[:3], categories=["Supermarket"],
             radius_m=800, limit=3)
        call("distance_to_station", "distance_to", listing_ids=selected[:3], destination_id="LTA:MRT:BUKIT_PANJANG")
        call("distance_to_invented", "distance_to", listing_ids=selected[:1], destination_id="URA:AREA:ORCHARD")
        call("listing_not_in_pool", "nearest_mrt", listing_ids=["999999999"])
    finally:
        listing.close()
        geographic.close()

    # A listing without coordinates (pool of the budget criterion only) for the platform text.
    listing, geographic = repositories("database")
    try:
        req2 = request("nocoord", [BUDGET]).model_copy(update={"result_limit": 10})
        d = Dispatcher(req2, {"viewed": []}, listing, geographic, {"trace_id": "live-nocoord", "steps": []},
                       config=config)
        call("resolve_budget_only", "resolve_entities")
        call("build_pool_budget_only", "build_pool")
        missing = [r.listing_id for r in d.pool if r.latitude is None or r.longitude is None]
        report["budget_only_pool"] = {"size": len(d.pool), "without_coordinates": len(missing)}
        if missing:
            call("nearest_mrt_without_coordinates", "nearest_mrt", listing_ids=missing[:3])
    finally:
        listing.close()
        geographic.close()
    report["calls"] = calls
    report["live_state_md5_after"] = md5(LIVE_STATE)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str) + "\n")
    for name, item in report["end_to_end"].items():
        print(name, item["status"], item["wall_ms"], item["tool_order_is_pipeline"], item["pool_size"],
              [(r["status"], r["entity_id"]) for r in item["resolution"]])
    for label, item in calls.items():
        print(label, item["status"], item["ms"], item["error"] and item["error"]["code"])
    print("unbound", report["unbound"]["pool_key"][:12], report["unbound"]["partition"]["size"],
          "bound", report["bound"]["pool_key"][:12], report["bound"]["partition"]["size"])
    print("state md5", report["live_state_md5_before"], report["live_state_md5_after"])


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("live-tools.json"))
