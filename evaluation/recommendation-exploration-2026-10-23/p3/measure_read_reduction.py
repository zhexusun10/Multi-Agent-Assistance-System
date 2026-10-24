"""P3 read-reduction measurement on the LIVE databases, read-only (spec R5 metric).

Database data mode (PostgreSQL read-only REPEATABLE READ transactions, Neo4j READ sessions)
with the ScriptedController. The live LLM is never used. The state file is a TEMPORARY file
(never data/.runtime). Repositories are created per request as in recommendation/app.py,
while the pool-cache LRU lives at module level as in the long-running service.

For each scenario:
  uncached: cache.enabled false (full retrieve + hard-geo evaluation each run, gate by id)
  built:    cache enabled, first request of a conversation (miss -> build + store)
  reused:   cache enabled, later requests with a soft-only change (hit), LRU warm
  reused-sqlite: same, LRU cleared first (content parsed from SQLite)
Metric: listing rows transferred (pool build + final gate) and wall time per request.

Usage: .venv/bin/python -m evaluation... (run from the repository root):
  PYTHONPATH=. .venv/bin/python evaluation/recommendation-exploration-2026-10-23/p3/measure_read_reduction.py [out.json]
"""
import json, statistics, sys, tempfile, time
from pathlib import Path

from recommendation.agent.controller import ScriptedController
from recommendation.config import RecommendationConfig
from recommendation.contracts.request import RecommendationRequest
from recommendation.repositories.connections import load_environment, repositories
from recommendation.service import RecommendationService
from recommendation.state import pool_cache
from recommendation.state.store import StateStore

ROOT = Path(__file__).resolve().parents[3]
RUNS = 5
SCENARIOS = {
    "listing-attributes": {
        "hard": [
            {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 3000, "unit": "SGD/month", "strength": "hard"},
            {"criterion_id": "bedrooms", "field": "bedrooms", "operator": "gte", "value": 2, "strength": "hard"},
        ],
        "soft": [[{"criterion_id": "district", "field": "district_code", "operator": "in", "value": [d], "strength": "soft"}]
                 for d in ("D05", "D10", "D15", "D19", "D20", "D21")],
    },
    "hard-geography": {
        "hard": [
            {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 4000, "unit": "SGD/month", "strength": "hard"},
            {"criterion_id": "hospital", "field": "distance", "operator": "lte", "value": 1500, "unit": "m", "strength": "hard",
             "category": "Hospital", "distance_type": "straight_line"},
        ],
        "soft": [[{"criterion_id": "bedrooms", "field": "bedrooms", "operator": "gte", "value": b, "strength": "soft"}]
                 for b in (1, 2, 3, 4, 5, 6)],
    },
}


def run(state, config, conversation, request_id, version, criteria, previous):
    listing, geographic = repositories("database")
    service = RecommendationService(listing, geographic, StateStore(state), ScriptedController(), config=config)
    try:
        started = time.monotonic()
        response = service.recommend(RecommendationRequest.model_validate({
            "request_id": request_id, "conversation_id": conversation, "requirements_version": version,
            "previous_state_id": previous, "requirements": {"listing_type": "RENT", "criteria": criteria},
        }))
        wall = (time.monotonic() - started) * 1000
        assert response.status != "error", response.error
        trace = service.store.trace(conversation, request_id)
        gate = [s for s in trace["steps"] if s["kind"] == "final_gate"][-1]
        stats = trace["pool_stats"]
        return response, {
            "cache": response.pool.cache, "outcome": trace["pool_ref"]["outcome"], "wall_ms": round(wall, 1),
            "duration_ms": round(response.execution.duration_ms, 1),
            "pool_rows_transferred": stats.get("rows_transferred"), "gate_rows_read": gate.get("rows_read"),
            "rows_transferred": (stats.get("rows_transferred") or 0) + (gate.get("rows_read") or 0),
            "retrieve_ms": round(stats.get("retrieve_ms", 0), 1), "fingerprint_ms": round(stats.get("fingerprint_ms", 0), 1),
            "hard_geo_ms": round(stats.get("hard_geo_ms", 0), 1), "cache_read_ms": round(stats.get("cache_read_ms", 0), 1),
            "cache_source": stats.get("cache_source"), "pool_size": response.pool.size,
            "source_count": response.pool.source_count, "returned": len(response.recommendations),
            "data_version": response.pool.data_version[:12], "pool_version": response.pool.pool_version[:12],
        }
    finally:
        service.close()


def summary(rows):
    return {
        "n": len(rows),
        "rows_transferred_median": statistics.median(r["rows_transferred"] for r in rows),
        "wall_ms_median": round(statistics.median(r["wall_ms"] for r in rows), 1),
        "wall_ms_min": min(r["wall_ms"] for r in rows), "wall_ms_max": max(r["wall_ms"] for r in rows),
        "fingerprint_ms_median": round(statistics.median(r["fingerprint_ms"] for r in rows), 1),
    }


def main(out):
    load_environment()
    cached = RecommendationConfig.default()
    uncached = cached.with_overrides({"cache.enabled": False})
    results = {"runs_per_mode": RUNS, "scenarios": {}}
    with tempfile.TemporaryDirectory(prefix="p3-measure-") as tmp:
        state = Path(tmp) / "state.sqlite"
        for name, scenario in SCENARIOS.items():
            modes = {"uncached": [], "built": [], "reused": [], "reused-sqlite": []}
            for i in range(RUNS):
                modes["uncached"].append(run(state, uncached, f"{name}-nc-{i}", "r1", 1, scenario["hard"] + scenario["soft"][0], None)[1])
            for i in range(RUNS):
                modes["built"].append(run(state, cached, f"{name}-c-{i}", "r1", 1, scenario["hard"] + scenario["soft"][0], None)[1])
            conversation = f"{name}-c-0"
            previous = StateStore(state).current(conversation)["state_id"]
            for i in range(1, RUNS + 1):
                response, row = run(state, cached, conversation, f"r{i + 1}", i + 1, scenario["hard"] + scenario["soft"][i], previous)
                modes["reused"].append(row)
                previous = response.state.state_id
            for i in range(RUNS):
                pool_cache.MEMORY.clear()
                conversation = f"{name}-c-{i}"
                head = StateStore(state).current(conversation)
                response, row = run(state, cached, conversation, f"s{i}", head["request"]["requirements_version"] + 1,
                                    scenario["hard"] + scenario["soft"][(i + 2) % 6], head["state_id"])
                modes["reused-sqlite"].append(row)
            results["scenarios"][name] = {"summary": {m: summary(r) for m, r in modes.items()}, "runs": modes}
    Path(out).write_text(json.dumps(results, indent=2))
    for name, data in results["scenarios"].items():
        print(name)
        for mode, s in data["summary"].items():
            print(f"  {mode:14s} rows={s['rows_transferred_median']:>7} wall_ms median={s['wall_ms_median']:>8} "
                  f"[{s['wall_ms_min']}, {s['wall_ms_max']}] fingerprint_ms={s['fingerprint_ms_median']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("read-reduction.json"))
