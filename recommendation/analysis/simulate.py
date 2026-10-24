"""P7 evaluation: rule-based simulated users over multi-round sessions, three service modes.

Usage (repository root):
  .venv/bin/python -m recommendation.analysis.simulate [--data database|fixture] [--sessions N]
      [--rounds N] [--out FILE]

Modes (the committed settings with in-memory overrides only):
  no_cache          cache.enabled false, cold_start.enabled false
  pool_cache        cache.enabled true,  cold_start.enabled false
  cache_cold_start  cache.enabled true,  cold_start.enabled true (price-stratified exploration)

Every request runs through RecommendationService with the ScriptedController, the shared
read-only repositories and one TEMPORARY state file per mode. Modes are interleaved per round so
database warm-up affects them alike; the same conversation / request IDs are used in every mode,
so no_cache and pool_cache must return identical selections (checked).

SIMULATED behaviour, never real users: each session has a rule-based user with a hidden
preference (target price, property type, districts) drawn from the pool with a fixed seed. For
every shown listing the user "browses" for a duration that grows with the listing's match to the
hidden preference (log-normal noise; about 10 % of records missing, as an orchestrator gap) and
saves a timed viewing draft (later re-saved: same listing, no extra signal) when the match is high.
The orchestrator side is emulated per the main contract: behavior_context.listing_view_durations =
session-cumulative seconds per listing (interest_eligible by the emulated main gate),
book_viewing_listings = every listing with a saved draft (one entry per listing), and
cold_start is always sent as {exited: false, exited_pool_key: null}: since the mpd-v1 detector
(2026-10-24) the orchestrator records no exits and native decides every round from D.
Durations depend only on (session, round, listing), so identical selections get identical
feedback in every mode. Reported exposure / coverage / match figures describe this simulator,
not user satisfaction.

Metrics (computed here, outside the subagent; the service keeps no exposure history):
per-stage and end-to-end P50/P95 (trace stage durations, execution.duration_ms, wall time),
pool coverage (share of the pool ever shown), exposure Gini over the WHOLE pool including
never-shown listings, score loss vs the deterministic top K of the same scores (from the run
snapshot's selection_input head), simulated match of the shown listings, and correctness (no
duplicates, every listing in the pool version, hard criteria pass, replay returns the stored
response, select-v1 replay of the snapshot reproduces the IDs, no_cache == pool_cache).
Price sampling does NOT reduce scoring cost: every mode scores the full pool every round.
"""

import argparse
import hashlib
import json
import math
import tempfile
import time
from pathlib import Path

import numpy as np
from recommendation.contracts.behavior import request_uuid, simulated_interest_eligibility

from recommendation.contracts.request import RecommendationRequest
from ..agent.controller import ScriptedController
from ..config import RecommendationConfig
from ..ranking import selection as selection_v1
from ..service import RecommendationService
from ..state.store import StateStore
from ..tracing.slim import STAGES
from . import stats
from .live import Data
from .scenarios import HARD_SCENARIOS, SOFT_SETS

MODES = {
    "no_cache": {"cache.enabled": False, "cold_start.enabled": False},
    "pool_cache": {"cache.enabled": True, "cold_start.enabled": False},
    "cache_cold_start": {"cache.enabled": True, "cold_start.enabled": True},
}
SESSION_PLAN = (("budget_bedrooms", "mixed"), ("mrt_distance", "district"), ("property_type", "no_soft"), ("none", "mixed"))
SEED = 20261023
MISSING_SHARE = 0.10
BOOK_MATCH = 0.75
BOOK_PROBABILITY = 0.5


def _unit_hash(*parts):
    """Deterministic U[0, 1) from the parts (independent of mode and call order)."""
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return int(h[:13], 16) / float(16 ** 13)


def _normal_hash(*parts):
    u1 = max(_unit_hash(*parts, "a"), 1e-12)
    u2 = _unit_hash(*parts, "b")
    return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)


class SimulatedUser:
    """Rule-based simulated user with a hidden preference drawn from the pool rows."""

    def __init__(self, rows, session):
        rng = np.random.default_rng(SEED + session)
        prices = np.array([r.price for r in rows if r.price is not None], float)
        self.session = session
        self.target_price = float(np.percentile(prices, rng.uniform(20, 80))) if len(prices) else None
        types = sorted({r.property_type for r in rows if r.property_type})
        districts = sorted({r.district_code for r in rows if r.district_code})
        self.property_type = types[rng.integers(len(types))] if types else None
        self.districts = set(rng.choice(districts, size=min(3, len(districts)), replace=False)) if districts else set()

    def match(self, row):
        parts = []
        if self.target_price and row.price is not None:
            parts.append(math.exp(-abs(row.price - self.target_price) / (0.25 * self.target_price)))
        if self.property_type is not None:
            parts.append(float(row.property_type == self.property_type))
        if self.districts:
            parts.append(float(row.district_code in self.districts))
        return sum(parts) / len(parts) if parts else 0.0

    def browse(self, round_no, row):
        """Browsing record for a shown listing (about 10 % missing)."""
        if _unit_hash(self.session, round_no, row.listing_id, "missing") < MISSING_SHARE:
            return {"listing_id": row.listing_id, "status": "missing"}
        m = self.match(row)
        seconds = 4 + 90 * m * math.exp(0.6 * _normal_hash(self.session, round_no, row.listing_id))
        if m < 0.2 and _unit_hash(self.session, round_no, row.listing_id, "skip") < 0.5:
            seconds = 0.0  # shown but not opened
        return {"listing_id": row.listing_id, "status": "recorded", "duration_seconds": round(seconds, 1)}

    def book(self, round_no, row):
        return self.match(row) >= BOOK_MATCH and _unit_hash(self.session, round_no, row.listing_id, "book") < BOOK_PROBABILITY



class Session:
    """Orchestrator emulation for one simulated conversation in one mode."""

    def __init__(self, scenario, soft_name, index, user, mode):
        self.scenario, self.soft_name, self.index, self.user, self.mode = scenario, soft_name, index, user, mode
        self.conversation = f"sim-{scenario}-{soft_name}-{index}"
        self.previous = None  # (request_id, response)
        self.views = {}  # listing_id -> {"seconds", "views", "requests", "last"}
        self.drafts = {}  # listing_id -> saved viewing draft summary
        self.cold_start = {"exited": False, "exited_pool_key": None}  # compat field, never updated (mpd-v1)

    def as_of(self, round_no):
        return f"2026-10-23T{8 + round_no:02d}:00:00Z"

    def body(self, round_no):
        if self.previous is not None:
            request_id, response = self.previous
            rows = {i.listing.listing_id: i.listing for i in response.recommendations}
            for listing_id, row in rows.items():
                record = self.user.browse(round_no - 1, row)
                if record["status"] != "recorded" or not record["duration_seconds"] > 0:
                    continue  # no completed segment: nothing reaches the behaviour ledger
                entry = self.views.setdefault(listing_id, {"seconds": 0.0, "views": 0, "requests": set(), "last": None})
                entry["seconds"] += record["duration_seconds"]
                entry["views"] += 1
                entry["requests"].add(request_uuid(request_id))
                entry["last"] = self.as_of(round_no - 1)
        eligible = simulated_interest_eligibility({k: v["seconds"] for k, v in self.views.items()})
        behavior = {
            "version": "1.0", "as_of": self.as_of(round_no),
            "listing_view_durations": [
                {"listing_id": k, "duration_seconds": round(v["seconds"], 3), "view_count": v["views"],
                 "last_viewed_at": v["last"], "source_request_ids": sorted(v["requests"]), "interest_eligible": eligible[k]}
                for k, v in sorted(self.views.items())],
            "book_viewing_listings": [self.drafts[k] for k in sorted(self.drafts)],
        }
        return {"request_id": f"{self.conversation}-r{round_no}", "conversation_id": self.conversation,
                "requirements_version": 1,
                "previous_state_id": self.previous[1].state.state_id if self.previous else None,
                "requirements": {"listing_type": "RENT", "criteria": HARD_SCENARIOS[self.scenario] + SOFT_SETS[self.soft_name]},
                "result_limit": 10, "behavior_context": behavior, "cold_start": dict(self.cold_start)}

    def adopt(self, round_no, request_id, response):
        """Orchestrator after adopting a response: saved viewing drafts (no cold-start exit is recorded)."""
        self.previous = (request_id, response)
        for item in response.recommendations:
            listing_id = item.listing.listing_id
            if listing_id not in self.drafts and self.user.book(round_no, item.listing):
                self.drafts[listing_id] = {
                    "listing_id": listing_id, "action_id": request_uuid(f"{self.conversation}-view-{listing_id}"),
                    "source_request_id": request_uuid(request_id), "requirements_version": 1,
                    "starts_at": "2026-10-30T06:00:00Z", "ends_at": "2026-10-30T07:00:00Z",
                    "time_zone": "Asia/Singapore", "recorded_at": self.as_of(round_no),
                    "signal": "viewing_draft_saved", "appointment_confirmed": False}


def check(response, body, pool_ids, pool_version, store):
    """Output correctness of one response (dict of booleans + details)."""
    ids = [i.listing.listing_id for i in response.recommendations]
    hard = {c["criterion_id"] for c in body["requirements"]["criteria"] if c["strength"] == "hard"}
    cached = store.cached(body["conversation_id"], body["request_id"])
    snapshot = cached[2] if cached else {}
    replayed = selection_v1.replay(snapshot["selection_input"]).ids if snapshot.get("selection_input") else None
    return {
        "status_ok": response.status in ("success", "no_match"),
        "no_duplicates": len(ids) == len(set(ids)),
        "all_in_pool": set(ids) <= pool_ids,
        "pool_version_matches": response.pool is not None and response.pool.pool_version == pool_version,
        "hard_criteria_pass": all(r.outcome == "pass" for i in response.recommendations
                                  for r in i.criterion_results if r.criterion_id in hard),
        "selection_replay_identical": replayed == ids,
    }, snapshot


def score_loss(response, snapshot, k):
    """(relative, absolute per item) loss of the shown set vs the deterministic top K of the
    same full-pool scores (the run snapshot's selection_input head is the O prefix)."""
    head = (snapshot.get("selection_input") or {}).get("head") or []
    top = sum(h[1] for h in head[:k])
    shown = sum(i.scores.combined_score for i in response.recommendations)
    return ((1 - shown / top) if top > 0 else 0.0), ((top - shown) / k if k else 0.0)


def stage_durations(trace):
    out = {}
    for record in trace.get("stages", []):
        if record.get("duration_ms") is not None:
            out[record["stage"]] = out.get(record["stage"], 0.0) + record["duration_ms"]
    return out


def evaluate(data, settings, sessions=6, rounds=5, plan=SESSION_PLAN, log=print):
    configs = {mode: settings.with_overrides(overrides) for mode, overrides in MODES.items()}
    builds = {scenario: data.build(scenario, HARD_SCENARIOS[scenario], settings) for scenario, _ in plan}
    report = {"settings": settings.identity(), "modes": {m: c.identity() for m, c in configs.items()},
              "mode_overrides": MODES, "sessions_per_scenario": sessions, "rounds": rounds,
              "plan": [list(p) for p in plan], "basis": "real listings, SIMULATED users (rule-based); not user satisfaction",
              "scenarios": {}}
    with tempfile.TemporaryDirectory(prefix="p7-evaluate-") as tmp:
        stores = {mode: StateStore(Path(tmp) / f"{mode}.sqlite") for mode in MODES}
        services = {mode: RecommendationService(data.listing, data.geographic, stores[mode], ScriptedController(),
                                                config=configs[mode]) for mode in MODES}
        try:
            for scenario, soft_name in plan:
                build = builds[scenario]
                pool_ids = {r.listing_id for r in build.rows}
                rows = {r.listing_id: r for r in build.rows}
                per_mode = {mode: {"timings": [], "checks": [], "losses": [], "abs_losses": [], "exposure": {}, "matches": [],
                                   "selection_modes": [], "cache": [], "replay_identical": [], "ids": [],
                                   "cold_start_inactive": 0} for mode in MODES}
                for index in range(sessions):
                    user = SimulatedUser(build.rows, index)
                    states = {mode: Session(scenario, soft_name, index, user, mode) for mode in MODES}
                    for round_no in range(1, rounds + 1):
                        for mode in MODES:
                            state, acc = states[mode], per_mode[mode]
                            body = state.body(round_no)
                            request = RecommendationRequest.model_validate(body)
                            started = time.perf_counter()
                            response = services[mode].recommend(request)
                            wall = (time.perf_counter() - started) * 1000
                            if response.status == "error":
                                raise RuntimeError(f"{mode} {body['request_id']}: {response.error}")
                            trace = stores[mode].trace(body["conversation_id"], body["request_id"])
                            acc["timings"].append({"round": round_no, "wall_ms": wall,
                                                   "service_ms": response.execution.duration_ms,
                                                   "stages": stage_durations(trace),
                                                   "rows_transferred": (trace.get("pool_stats") or {}).get("rows_transferred", 0)})
                            checks, snapshot = check(response, body, pool_ids, build.version, stores[mode])
                            replay = services[mode].recommend(RecommendationRequest.model_validate(body))
                            checks["replay_identical"] = replay.model_dump(mode="json") == response.model_dump(mode="json")
                            acc["checks"].append(checks)
                            relative, absolute = score_loss(response, snapshot, min(10, len(pool_ids)))
                            acc["losses"].append(relative)
                            acc["abs_losses"].append(absolute)
                            ids = [i.listing.listing_id for i in response.recommendations]
                            acc["ids"].append(ids)
                            for i in ids:
                                acc["exposure"][i] = acc["exposure"].get(i, 0) + 1
                            acc["matches"].extend(user.match(rows[i]) for i in ids)
                            acc["selection_modes"].append(response.selection.mode if response.selection else None)
                            acc["cache"].append(response.pool.cache if response.pool else None)
                            cold = response.selection.cold_start if response.selection else None
                            if cold is not None and cold.evaluated and not cold.active:
                                acc["cold_start_inactive"] += 1
                            state.adopt(round_no, body["request_id"], response)
                    log(f"{scenario}: session {index + 1}/{sessions} done")
                report["scenarios"][f"{scenario}/{soft_name}"] = summarise(per_mode, build, rows)
        finally:
            for store in stores.values():
                store.close()
    return report


def summarise(per_mode, build, rows):
    pool = [r.listing_id for r in build.rows]
    out = {"pool_size": len(pool), "pool_version": build.version, "modes": {}}
    for mode, acc in per_mode.items():
        timings = acc["timings"]
        stages = {s: stats.p50_p95([t["stages"].get(s) for t in timings if s in t["stages"]])
                  for s in STAGES if any(s in t["stages"] for t in timings)}
        first = [t for t in timings if t["round"] == 1]
        later = [t for t in timings if t["round"] > 1]
        counts = [acc["exposure"].get(i, 0) for i in pool]
        checks = acc["checks"]
        out["modes"][mode] = {
            "requests": len(timings),
            "end_to_end_ms": {"wall": stats.p50_p95([t["wall_ms"] for t in timings]),
                              "service": stats.p50_p95([t["service_ms"] for t in timings]),
                              "service_round1": stats.p50_p95([t["service_ms"] for t in first]),
                              "service_later_rounds": stats.p50_p95([t["service_ms"] for t in later])},
            "stages_ms": stages,
            "rows_transferred": stats.p50_p95([t["rows_transferred"] for t in timings]),
            "pool_cache": {k: acc["cache"].count(k) for k in sorted(set(acc["cache"]) - {None})},
            "selection_modes": {k: acc["selection_modes"].count(k) for k in sorted(set(acc["selection_modes"]) - {None})},
            "cold_start_inactive_responses": acc["cold_start_inactive"],
            "coverage": stats.coverage(acc["exposure"], pool),
            "coverage_of_possible": len(acc["exposure"]) / min(len(pool), sum(counts)) if sum(counts) else 0.0,
            "distinct_shown": len(acc["exposure"]),
            "exposure_gini_incl_zero": stats.gini(counts),
            "exposure_gini_shown_only": stats.gini([c for c in counts if c > 0]),
            "score_loss_vs_deterministic_top_k": stats.summarize(acc["losses"], percentiles=(50, 95, 100)),
            "score_loss_abs_per_item": stats.summarize(acc["abs_losses"], percentiles=(50, 95, 100)),
            "simulated_match_of_shown": stats.summarize(acc["matches"], percentiles=(25, 50, 75)),
            "checks": {name: sum(c[name] for c in checks) for name in checks[0]} if checks else {},
            "checks_total": len(checks),
        }
    a, b = per_mode.get("no_cache"), per_mode.get("pool_cache")
    if a and b:
        out["no_cache_equals_pool_cache"] = a["ids"] == b["ids"]
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", choices=("database", "fixture"), default="database")
    parser.add_argument("--config", default=None)
    parser.add_argument("--sessions", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    settings = RecommendationConfig.from_file(args.config)
    data = Data(args.data)
    plan = SESSION_PLAN if args.data == "database" else (("none", "mixed"), ("none", "no_soft"))
    try:
        report = evaluate(data, settings, args.sessions, args.rounds, plan)
    finally:
        data.close()
    text = json.dumps(report, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n")
    else:
        print(text)
    return report


if __name__ == "__main__":
    main()
