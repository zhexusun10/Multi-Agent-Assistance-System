"""P7 live smoke: the native service from THIS tree on a separate port, live PostgreSQL + Neo4j, temporary state.

Never touches the running services on 8010 / 8091 / 8092 or data/.runtime. Each part starts
p7/serve_smoke.py as a subprocess (own port, own random token, temporary RECOMMENDATION_STATE_PATH), talks to it
over HTTP (/health, /app/recommend, /recommend) and stops it afterwards.

  scripted  ScriptedController, database mode, one conversation, 3 rounds + replay:
            r1 initial (hard budget + bedrooms, soft district), r2 feedback re-rank with a complete snapshot
            (browsing 90 s / 0 s / missing + one counted appointment) -> pool reused, r3 soft-preference change
            (requirements_version + 1, hard criteria unchanged) -> pool cache hit; then r2 resent unchanged ->
            immutable replay (identical response).
  live      LiveController with the .env model, at most 3 runs: L1 initial with a soft named-area target, L2 feedback
            re-rank of L1, L3 a separate conversation whose soft target name is ambiguous but narrowable
            ("Lee Kong Chian School of Business"), so the model may use search_places + bind_entity.

Per request it records the request summary, response summary (status, pool, selection, cold start, IDs, reasons,
score ranks), client wall time, execution duration / model calls / tool calls / tokens, trace stage durations and
the tool sequence (read from the temporary state after the service stopped), plus offline correctness checks
(no duplicates, all returned IDs in the stored pool content, display order = leaders then drawn items in score
order, hard criteria pass in criterion_results, replay identical).

Usage (repository root): .venv/bin/python evaluation/recommendation-exploration-2026-10-23/p7/live_smoke.py \
    [--parts scripted,live] [--port 8192] [--out evaluation/recommendation-exploration-2026-10-23/p7/live-smoke.json]
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
LIVE_STATE = (ROOT / "data/.runtime/recommendation/state.sqlite").resolve()
PROTECTED_PORTS = {8010, 8091, 8092}

HARD = [
    {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 3500, "unit": "SGD/month", "strength": "hard"},
    {"criterion_id": "bedrooms", "field": "bedrooms", "operator": "gte", "value": 2, "strength": "hard"},
]
SOFT_D15 = {"criterion_id": "district", "field": "district_code", "operator": "in", "value": ["D15"], "strength": "soft", "weight": 1}
SOFT_D19 = {"criterion_id": "district", "field": "district_code", "operator": "in", "value": ["D19"], "strength": "soft", "weight": 1}
SOFT_PRICE = {"criterion_id": "cheaper", "field": "price", "operator": "lte", "value": 3500, "unit": "SGD/month", "strength": "soft", "weight": 1, "utility": "minimize"}


def body(conversation, request_id, version, criteria, previous_state_id=None, previous_round=None, appointments=()):
    return {"contract_version": "2.0", "request_id": request_id, "conversation_id": conversation, "requirements_version": version,
            "requirements": {"listing_type": "RENT", "criteria": criteria}, "previous_state_id": previous_state_id,
            "result_limit": 10, "feedback": {"previous_round": previous_round, "appointments": list(appointments)},
            "cold_start": {"exited": False, "exited_pool_key": None}}


def snapshot_round(response, recorded):
    """Complete previous-round snapshot: one record per shown ID; ``recorded`` maps index -> seconds."""
    shown = [i["listing"]["listing_id"] for i in response["recommendations"]]
    browsing = [{"listing_id": i, "status": "recorded", "duration_seconds": recorded[k]} if k in recorded
                else {"listing_id": i, "status": "missing", "duration_seconds": None} for k, i in enumerate(shown)]
    return {"request_id": response["request_id"], "shown_listing_ids": shown, "browsing": browsing}


class Service:
    def __init__(self, controller, port, state):
        if port in PROTECTED_PORTS:
            raise SystemExit(f"port {port} belongs to a running service")
        self.port, self.token, self.state = port, secrets.token_hex(24), state
        env = {**os.environ, "P7_SMOKE_PORT": str(port), "P7_SMOKE_TOKEN": self.token, "RECOMMENDATION_STATE_PATH": str(state),
               "RECOMMENDATION_CONTROLLER": controller, "RECOMMENDATION_DATA_MODE": "database", "PYTHONUNBUFFERED": "1"}
        for key in ("RECOMMENDATION_CONFIG", "RECOMMENDATION_TRACE_MODE"):
            env.pop(key, None)
        self.log = open(state.with_suffix(f".{controller}.log"), "w")
        self.process = subprocess.Popen([sys.executable, str(HERE / "serve_smoke.py")], cwd=ROOT, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 60
        while True:
            try:
                health = httpx.get(self.url + "/health", timeout=2).json()
                break
            except httpx.HTTPError:
                if self.process.poll() is not None or time.monotonic() > deadline:
                    raise SystemExit(f"smoke service did not start (see {self.log.name})")
                time.sleep(0.3)
        assert health["contract"] == "2.0" and health["controller"] == controller, health
        self.health = health

    def post(self, path, payload):
        started = time.monotonic()
        r = httpx.post(self.url + path, json=payload, headers={"x-recommendation-token": self.token}, timeout=240)
        wall = (time.monotonic() - started) * 1000
        r.raise_for_status()
        return r.json(), round(wall, 1)

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.log.close()


def summarise(request, envelope, wall):
    response = envelope.get("response", envelope)
    sel, pool, ex = response.get("selection"), response.get("pool"), response["execution"]
    fb = request["feedback"]
    return {
        "request": {"request_id": request["request_id"], "requirements_version": request["requirements_version"],
                    "criteria": [f"{c['criterion_id']}:{c['field']} {c['operator']} {c['value']} ({c['strength']})" for c in request["requirements"]["criteria"]],
                    "previous_round": None if not fb["previous_round"] else {
                        "request_id": fb["previous_round"]["request_id"], "shown": len(fb["previous_round"]["shown_listing_ids"]),
                        "recorded": {b["listing_id"]: b["duration_seconds"] for b in fb["previous_round"]["browsing"] if b["status"] == "recorded"}},
                    "appointments": fb["appointments"]},
        "status": response["status"], "warnings": response["warnings"], "clarifications": response["clarifications"],
        "resolution": [{k: r[k] for k in ("criterion_id", "status", "entity_id", "entity_kind", "method", "reason")} | {"candidates": len(r["candidates"])} for r in response["resolution"]],
        "pool": pool and {k: pool[k] for k in ("pool_key", "pool_version", "data_version", "cache", "size", "source_count", "excluded_hard_fail", "excluded_hard_unknown")},
        "selection": sel and {"mode": sel["mode"], "algorithm_version": sel["algorithm_version"], "leaders": sel["leaders"], "tie_group": sel["tie_group"],
                              "exploration": sel["exploration"] and {k: sel["exploration"][k] for k in ("candidate_count", "sample_size")},
                              "cold_start": {k: sel["cold_start"][k] for k in ("evaluated", "active", "reason", "coverage_c", "boundary_group_b", "stability_s", "exit_conditions_met")}},
        "recommendations": [{"listing_id": i["listing"]["listing_id"], "rank": i["rank"], "score_rank": i["score_rank"], "reason": i["selection_reason"],
                             "combined": round(i["scores"]["combined_score"], 6), "soft": i["scores"]["soft_score"], "behavior": i["scores"]["behavior_score"],
                             "appointment": i["scores"]["appointment_score"], "browsing": i["scores"]["browsing_score"]} for i in response["recommendations"]],
        "presentation_counts": envelope.get("presentation", {}).get("counts"),
        "execution": {k: ex[k] for k in ("controller_mode", "data_mode", "scoring_version", "selection_algorithm", "config_hash", "model_identifier", "model_calls", "tool_calls", "duration_ms", "tokens")},
        "timing_ms": {"client_wall": wall, "service": round(ex["duration_ms"], 1)},
    }


def checks(state, conversation, request, response):
    """Offline correctness checks against the temporary state (pool content, trace) after the service stopped."""
    from recommendation.state.store import StateStore
    store = StateStore(state)
    try:
        # The run's OWN trace (a later replay of the same request_id stores a replay trace under that request_id).
        trace = store.resolve_reference(conversation, response["execution"]["trace_id"])
        out = {"stage_ms": {s["stage"]: round(s["duration_ms"], 1) for s in trace.get("stages", []) if s.get("duration_ms") is not None},
               "tools": [s["result"]["tool"] if s.get("result") else s.get("kind") for s in trace["steps"] if s.get("kind") in ("tool", "final_gate")],
               "model_steps": sum(1 for s in trace["steps"] if s.get("kind") == "model"),
               "trace_bytes": len(json.dumps(trace, default=str))}
        ids = [i["listing"]["listing_id"] for i in response["recommendations"]]
        result = {"no_duplicates": len(ids) == len(set(ids)),
                  "display_order_leaders_then_drawn_in_score_order": all(a["score_rank"] < b["score_rank"] for a, b in zip(response["recommendations"], response["recommendations"][1:])),
                  "hard_criteria_pass": all(r["outcome"] == "pass" for i in response["recommendations"] for r in i["criterion_results"]
                                            if any(c["criterion_id"] == r["criterion_id"] and c["strength"] == "hard" for c in request["requirements"]["criteria"]))}
        try:
            pool = store.resolve_reference(conversation, f"{trace['trace_id']}/pool")
            rows = pool.get("rows") if isinstance(pool, dict) else None
            pool_ids = {r["listing_id"] for r in rows} if rows else None
            result["all_in_pool_content"] = pool_ids is not None and set(ids) <= pool_ids
            result["pool_content_rows"] = len(pool_ids) if pool_ids is not None else None
        except Exception as error:  # evicted / bypassed pools are reported, not hidden
            result["all_in_pool_content"] = f"unavailable: {error}"
        out["correctness"] = result
        return out
    finally:
        store.close()


def scripted(port, tmp):
    state = Path(tmp) / "scripted.sqlite"
    service = Service("scripted", port, state)
    runs, conv = [], "p7-smoke-" + uuid.uuid4().hex[:8]
    try:
        r1_body = body(conv, "s1-" + uuid.uuid4().hex[:6], 1, HARD + [SOFT_D15])
        r1, wall = service.post("/app/recommend", r1_body)
        runs.append(("r1_initial", r1_body, r1, wall))
        first = r1["response"]
        appointment = first["recommendations"][2]["listing"]["listing_id"]
        r2_body = body(conv, "s2-" + uuid.uuid4().hex[:6], 1, HARD + [SOFT_D15], first["state"]["state_id"],
                       snapshot_round(first, {0: 90.0, 1: 0.0}),
                       [{"listing_id": appointment, "statuses": ["MANUAL_SENT"], "action_ids": ["p7-action-1"]}])
        r2, wall = service.post("/app/recommend", r2_body)
        runs.append(("r2_feedback_rerank", r2_body, r2, wall))
        second = r2["response"]
        r3_body = body(conv, "s3-" + uuid.uuid4().hex[:6], 2, HARD + [SOFT_D19, SOFT_PRICE], second["state"]["state_id"],
                       snapshot_round(second, {}), r2_body["feedback"]["appointments"])
        r3, wall = service.post("/app/recommend", r3_body)
        runs.append(("r3_soft_change_cache_hit", r3_body, r3, wall))
        replay, replay_wall = service.post("/app/recommend", r2_body)
        direct, direct_wall = service.post("/recommend", r2_body)
        health = service.health
    finally:
        service.stop()
    out = {"port": port, "state": "temporary", "health": health, "conversation": conv, "runs": []}
    for name, req, env, wall in runs:
        out["runs"].append({"name": name, **summarise(req, env, wall), **checks(state, conv, req, env["response"])})
    out["replay"] = {"request_id": r2_body["request_id"], "identical_response": replay["response"] == second, "direct_recommend_identical": direct == second,
                     "client_wall_ms": replay_wall, "direct_client_wall_ms": direct_wall}
    s1, s2, s3 = (r["pool"] for r in out["runs"])
    out["expectations"] = {
        "r1_built": s1["cache"] == "built",
        "r2_feedback_reuses_pool": s2["cache"] == "reused" and s2["pool_version"] == s1["pool_version"],
        "r3_soft_change_reuses_pool": s3["cache"] == "reused" and s3["pool_key"] == s1["pool_key"],
        "r2_feedback_changes_behaviour_scores": any(i["behavior"] is not None for i in out["runs"][1]["recommendations"]),
        "replay_identical": out["replay"]["identical_response"] and out["replay"]["direct_recommend_identical"],
        "all_success": all(r["status"] == "success" for r in out["runs"]),
    }
    return out


def live(port, tmp, limit):
    state = Path(tmp) / "live.sqlite"
    service = Service("live", port, state)
    runs, conv, conv2 = [], "p7-live-" + uuid.uuid4().hex[:8], "p7-live-amb-" + uuid.uuid4().hex[:8]
    clementi = {"criterion_id": "near-clementi", "field": "distance", "operator": "lte", "value": 1500, "unit": "m", "strength": "soft",
                "weight": 1, "utility": "proximity", "destination_name": "Clementi", "distance_type": "straight_line"}
    lkc = {"criterion_id": "near-lkc", "field": "distance", "operator": "lte", "value": 2000, "unit": "m", "strength": "soft",
           "weight": 1, "utility": "proximity", "destination_name": "Lee Kong Chian School of Business", "distance_type": "straight_line"}
    try:
        l1_body = body(conv, "l1-" + uuid.uuid4().hex[:6], 1, HARD + [clementi])
        l1, wall = service.post("/app/recommend", l1_body)
        runs.append(("L1_initial_named_area", conv, l1_body, l1, wall))
        if limit >= 2 and l1["response"]["status"] == "success":
            first = l1["response"]
            l2_body = body(conv, "l2-" + uuid.uuid4().hex[:6], 1, HARD + [clementi], first["state"]["state_id"], snapshot_round(first, {0: 120.0}))
            l2, wall = service.post("/app/recommend", l2_body)
            runs.append(("L2_feedback_rerank", conv, l2_body, l2, wall))
        if limit >= 3:
            l3_body = body(conv2, "l3-" + uuid.uuid4().hex[:6], 1, HARD + [lkc])
            l3, wall = service.post("/app/recommend", l3_body)
            runs.append(("L3_ambiguous_narrowable_target", conv2, l3_body, l3, wall))
        health = service.health
    finally:
        service.stop()
    out = {"port": port, "state": "temporary", "health": health, "llm_runs": len(runs), "runs": []}
    for name, c, req, env, wall in runs:
        out["runs"].append({"name": name, **summarise(req, env, wall), **checks(state, c, req, env["response"])})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parts", default="scripted,live")
    parser.add_argument("--port", type=int, default=8192)
    parser.add_argument("--live-runs", type=int, default=3)
    parser.add_argument("--out", default=str(HERE / "live-smoke.json"))
    args = parser.parse_args()
    if args.live_runs > 3:
        parser.error("at most 3 live-LLM runs")
    before = LIVE_STATE.stat().st_mtime_ns if LIVE_STATE.exists() else None
    report = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tree": str(ROOT), "python": sys.version.split()[0]}
    with tempfile.TemporaryDirectory(prefix="p7-smoke-") as tmp:
        if "scripted" in args.parts:
            report["scripted"] = scripted(args.port, tmp)
        if "live" in args.parts:
            report["live"] = live(args.port, tmp, args.live_runs)
    report["live_state_untouched"] = (LIVE_STATE.stat().st_mtime_ns if LIVE_STATE.exists() else None) == before
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    Path(args.out).write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v.get("expectations", {"runs": [(r["name"], r["status"], r["execution"]["tool_calls"], r["execution"]["tokens"]) for r in v["runs"]]})
                      if isinstance(v, dict) else v for k, v in report.items()}, indent=1, default=str))


if __name__ == "__main__":
    main()
