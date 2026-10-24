"""P5 part 2 live smoke: cold-start diagnostics + select-v1 on the LIVE databases, read-only.

Database data mode (PostgreSQL read-only transactions, Neo4j READ sessions) with the
ScriptedController; the live LLM is never used. The state file is a TEMPORARY file (never
data/.runtime). Repositories are created per request as in recommendation/app.py.

Scenarios (committed YAML settings, result_limit 10):
  attributes-soft-district: hard budget <= 3000 + bedrooms >= 2, soft district D05.
      round 1: initial request; round 2: same requirements + round-1 browsing feedback, with a
      SIMULATED orchestrator exit: cold_start {exited: true, exited_pool_key: round 1's pool_key}
      is injected although round 1 reported exit_conditions_met false. Under R7 a real
      orchestrator records the exit only after adopting a response whose diagnostics met the exit
      conditions, so this round is not a flow R7 produces; it only exercises near-tie sampling
      (cold start off for this pool) on live data.
  hard-geography-no-soft: hard budget <= 4000 + within 1500 m of a Hospital, no soft criteria
      (C = 0 by definition, so cold start cannot be exited, R9).
Reported per run: selection mode, leaders, tie group, exploration, C/B/S, reason, and timing
(service total, rank_candidates step, scoring, selection incl. diagnostics).

Usage (repository root):
  PYTHONPATH=. .venv/bin/python evaluation/recommendation-exploration-2026-10-23/p5/live_selection.py [out.json]
"""
import json, sys, tempfile, time
from pathlib import Path

from recommendation.agent.controller import ScriptedController
from recommendation.contracts.request import RecommendationRequest
from recommendation.repositories.connections import load_environment, repositories
from recommendation.service import RecommendationService
from recommendation.state.store import StateStore

ATTRIBUTES = [
    {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 3000, "unit": "SGD/month", "strength": "hard"},
    {"criterion_id": "bedrooms", "field": "bedrooms", "operator": "gte", "value": 2, "strength": "hard"},
    {"criterion_id": "district", "field": "district_code", "operator": "in", "value": ["D05"], "strength": "soft"},
]
GEOGRAPHY = [
    {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 4000, "unit": "SGD/month", "strength": "hard"},
    {"criterion_id": "hospital", "field": "distance", "operator": "lte", "value": 1500, "unit": "m", "strength": "hard",
     "category": "Hospital", "distance_type": "straight_line"},
]


def run(state, body):
    listing, geographic = repositories("database")
    service = RecommendationService(listing, geographic, StateStore(state), ScriptedController())
    try:
        started = time.monotonic()
        response = service.recommend(RecommendationRequest.model_validate(body))
        wall = (time.monotonic() - started) * 1000
        assert response.status != "error", response.error
        trace = service.store.trace(body["conversation_id"], body["request_id"])
        rank = [s for s in trace["steps"] if s.get("result", {}).get("tool") == "rank_candidates"][-1]
        data = rank["result"]["data"]
        sel = response.selection
        cold = sel.cold_start
        return response, {
            "status": response.status, "pool_size": response.pool.size, "cache": response.pool.cache,
            "mode": sel.mode, "leaders": sel.leaders, "returned": len(response.recommendations),
            "tie_group": sel.tie_group.model_dump() if sel.tie_group else None,
            "exploration": sel.exploration.model_dump() if sel.exploration else None,
            "cold_start": {"evaluated": cold.evaluated, "active": cold.active, "reason": cold.reason,
                           "C": cold.coverage_c, "B": cold.boundary_group_b, "S": cold.stability_s,
                           "exit_conditions_met": cold.exit_conditions_met},
            "reasons": [i.selection_reason for i in response.recommendations],
            "score_ranks": [i.score_rank for i in response.recommendations],
            "timing_ms": {"wall": round(wall, 1), "service_total": round(response.execution.duration_ms, 1),
                          "rank_candidates_step": round(rank["duration_ms"], 1),
                          "scoring": round(data["scoring"]["duration_ms"], 1),
                          "selection_incl_diagnostics": round(data["selection"]["duration_ms"], 1),
                          "diagnostics": round(data["selection"]["cold_start"].get("duration_ms", 0.0), 1)},
        }
    finally:
        service.close()


def main():
    load_environment()
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "live-selection.sqlite"
        base = {"requirements_version": 1, "result_limit": 10}
        first, results["attributes-soft-district/round-1"] = run(state, {
            **base, "request_id": "p5-live-a1", "conversation_id": "p5-live-a",
            "requirements": {"listing_type": "RENT", "criteria": ATTRIBUTES}})
        shown = [i.listing.listing_id for i in first.recommendations]
        _, results["attributes-soft-district/round-2-simulated-exit"] = run(state, {
            **base, "request_id": "p5-live-a2", "conversation_id": "p5-live-a",
            "previous_state_id": first.state.state_id,
            "requirements": {"listing_type": "RENT", "criteria": ATTRIBUTES},
            "feedback": {"previous_round": {"request_id": "p5-live-a1", "shown_listing_ids": shown,
                                            "browsing": [{"listing_id": i, "status": "recorded", "duration_seconds": 40.0 * (k + 1)}
                                                         for k, i in enumerate(shown)]},
                         "appointments": []},
            "cold_start": {"exited": True, "exited_pool_key": first.pool.pool_key}})
        results["attributes-soft-district/round-2-simulated-exit"]["scenario_note"] = (
            "Simulated orchestrator exit injected at round 1's pool_key although round 1 reported "
            f"exit_conditions_met={first.selection.cold_start.exit_conditions_met}; R7 would not record "
            "this exit. Used only to exercise near-tie sampling.")
        _, results["hard-geography-no-soft/round-1"] = run(state, {
            **base, "request_id": "p5-live-g1", "conversation_id": "p5-live-g",
            "requirements": {"listing_type": "RENT", "criteria": GEOGRAPHY}})
    text = json.dumps(results, indent=1)
    print(text)
    if out:
        out.write_text(text + "\n")


if __name__ == "__main__":
    main()
