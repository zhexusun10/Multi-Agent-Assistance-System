"""P6 part 2: terminal debugging commands (spec section 11, R14; terminal/debug.py).

Fixture data mode, scripted controller (live-mode tests use a scripted fake model) and a private
state file (conftest isolation). The first test is the automated terminal transcript of the P6
validation item: first recommendation, feedback re-rank, config comparison with a fixed seed,
cache hit/miss, entity ambiguity, single-listing inspection and original-request replay.
"""

import argparse
import hashlib
import json
from pathlib import Path

import pytest

from recommendation.agent.controller import LiveController
from recommendation.config import SETTINGS_PATH, RecommendationConfig
from recommendation.contracts.errors import RecommendationError
from recommendation.state import pool_cache as pool_cache_module
from recommendation.terminal import debug
from recommendation.terminal.__main__ import Terminal
from recommendation.tools.registry import PIPELINE

EXAMPLES = Path(__file__).parents[1] / "examples"
CONV = "p6-terminal"
D05 = {"fixture-01", "fixture-03", "fixture-05", "fixture-07", "fixture-09"}
GEO_CONFLICT = {"criterion_id": "geo", "field": "distance", "operator": "lte", "value": 5000, "unit": "m",
                "strength": "hard", "distance_type": "straight_line", "destination_id": "URA:AREA:CLEMENTI",
                "destination_name": "Bedok"}


@pytest.fixture(autouse=True)
def fresh_memory():
    pool_cache_module.MEMORY.clear()
    yield
    pool_cache_module.MEMORY.clear()


PRE_P7_SETTINGS = Path(__file__).with_name("fixtures") / "settings-pre-p7-provisional.json"


def terminal(tmp_path, controller="scripted", conversation=CONV, **extra):
    return Terminal(argparse.Namespace(state_path=str(tmp_path / "state.sqlite"), conversation_id=conversation,
                                       data_mode="fixture", controller=controller, brief=True, **extra))


class Session:
    """Runs terminal commands and records an echoed transcript (like --script)."""

    def __init__(self, t, capsys):
        self.t, self.capsys, self.transcript = t, capsys, []

    def __call__(self, line):
        self.capsys.readouterr()
        try:
            self.t.command(line)
        finally:
            out = self.capsys.readouterr().out
            self.transcript.append(f"recommendation> {line}\n{out}")
        return out

    def close(self):
        if self.t.service:
            self.t.service.close()
        else:
            self.t.store.close()


def payload(out):
    """The JSON document printed after the 'source:' / note lines of an inspection command."""
    start = 0 if out.startswith(("{", "[")) else min(
        i for i in (out.find("\n{"), out.find("\n[")) if i >= 0) + 1
    return json.JSONDecoder().raw_decode(out[start:])[0]


def requirements(tmp_path, criteria, name="requirements.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"listing_type": "RENT", "criteria": criteria}))
    return path


def base_criteria(budget=3000, district="D05"):
    return [{"criterion_id": "budget", "field": "price", "operator": "lte", "value": budget, "unit": "SGD/month",
             "strength": "hard"},
            {"criterion_id": "bedrooms", "field": "bedrooms", "operator": "gte", "value": 2, "strength": "hard"},
            {"criterion_id": "district", "field": "district_code", "operator": "in", "value": [district],
             "strength": "soft", "weight": 1}]


def ids(response):
    return [i.listing.listing_id for i in response.recommendations]


# --- the automated terminal transcript (P6 validation item) ------------------------------------
def test_terminal_transcript_covers_the_p6_flows(tmp_path, capsys):
    yaml_digest = hashlib.sha256(SETTINGS_PATH.read_bytes()).hexdigest()
    # P7: the transcript's tie / draw assertions were written for the pre-calibration values
    # (epsilon 0.02, iqr); they are pinned by a fixture settings file instead of the YAML.
    run = Session(terminal(tmp_path, config=str(PRE_P7_SETTINGS)), capsys)
    t = run.t
    try:
        # 1. First recommendation (fixed seed, session config: result_limit 3 so the pool exceeds it).
        run(f"requirements @{requirements(tmp_path, base_criteria())}")
        assert "config_hash" in run("set-config recommendation.default_result_limit 3")
        run("set-seed fixed-seed")
        out = run("recommend")
        first = t.response
        assert first.status == "success" and len(first.recommendations) == 3
        assert "cache=built (MISS)" in out and first.pool.cache == "built"
        assert first.execution.config_hash == t.config.config_hash
        assert all(f"tool: {name} start" in out and f"tool: {name} end ok" in out for name in PIPELINE)
        assert "gate: final_gate ok (selected=3, rows_read=3)" in out
        stored = t.store.trace(CONV, first.request_id)
        assert stored["seed_override"]["value"] == "fixed-seed"
        assert first.selection.seed == debug_seed("fixed-seed")
        first_ranked = list(t.run_dispatcher.scored.ranked)

        # 2. Single-listing inspection from this run's retained data, and a recomputed one.
        chosen = first.recommendations[0]
        out = run(f"inspect-listing {chosen.listing.listing_id}")
        assert "data retained in memory from this run" in out
        info = payload(out)
        assert info["recomputed"] is False and info["selected"] is True and info["rank"] == 1
        assert info["score_rank"] == chosen.score_rank
        assert info["scores"] == chosen.scores.model_dump(mode="json")
        assert info["selection_reason"] == chosen.selection_reason
        out = run("inspect-listing fixture-11")
        assert "recomputed" in out
        info = payload(out)
        assert info["recomputed"] is True and info["hard_failures"] == ["budget"] and not info["hard_criteria_pass"]
        pool = payload(run("show-pool --page 1"))
        assert pool["pool"]["size"] == 9 and [r["listing_id"] for r in pool["rows"]] == first_ranked
        scores = payload(run("show-scores --top 4"))
        assert [r["listing_id"] for r in scores["top"]] == first_ranked[:4]
        cold = payload(run("show-cold-start"))
        assert cold["cold_start"] == first.selection.cold_start.model_dump(mode="json")
        selection = payload(run("show-selection"))
        assert [i["listing_id"] for i in selection["items"]] == ids(first) and selection["seed_override"] is True

        # 3. Feedback re-rank: template (one browsing record per shown ID), filled, staged, re-run.
        template = tmp_path / "feedback.json"
        run(f"feedback-template {template}")
        snapshot = json.loads(template.read_text())
        assert [b["listing_id"] for b in snapshot["previous_round"]["browsing"]] == ids(first)
        assert all(b["duration_seconds"] is None for b in snapshot["previous_round"]["browsing"])
        for n, record in enumerate(snapshot["previous_round"]["browsing"]):
            record["duration_seconds"] = 150 if n == 0 else 0
        template.write_text(json.dumps(snapshot))
        assert "Staged feedback snapshot" in run(f"feedback @{template}")
        out = run("recommend")
        rerank = t.response
        assert rerank.status == "success" and rerank.request_id != first.request_id
        assert "cache=reused (HIT)" in out and rerank.pool.pool_version == first.pool.pool_version
        assert "Rank/score changes:" in out
        reference = ids(first)[0]
        scored = t.run_dispatcher.scored
        assert scored.references["browsing"]["ids"] == [reference]
        others = [k for k in scored.ranked if k != reference]
        assert all(scored.breakdown(k).browsing_score is not None for k in others)
        assert list(scored.ranked) != first_ranked  # browsing similarity re-ranked the full pool

        # 4. Config comparison with a fixed seed: same settings + seed -> same selection under a NEW
        # request_id; another setting -> the difference comes from the settings only.
        out = run("recommend")
        same = t.response
        assert same.request_id not in (first.request_id, rerank.request_id)
        assert ids(same) == ids(first) and same.selection.seed == first.selection.seed
        assert same.execution.config_hash == first.execution.config_hash
        assert "cache=reused (HIT)" in out
        run("set-config cold_start.enabled false")
        compared = (run("recommend"), t.response)[1]
        assert compared.execution.config_hash != first.execution.config_hash
        assert compared.selection.seed == first.selection.seed  # the fixed base seed
        assert first.selection.mode == "cold_start_exploration" and compared.selection.mode == "near_tie_sampling"
        assert set(ids(compared)) <= D05 and set(ids(first)) - D05  # exploration drew outside the tie group
        # Weight comparison under the same fixed seed (plan: 可固定测试种子比较不同权重): the same
        # previous-round feedback is staged for both runs and only the fusion weights change, so
        # the component scores are identical and the combined scores, the order and the selection
        # differ only through the weights.
        weights_feedback = tmp_path / "weights-feedback.json"
        run(f"feedback-template {weights_feedback}")
        snapshot = json.loads(weights_feedback.read_text())
        for n, record in enumerate(snapshot["previous_round"]["browsing"]):
            record["duration_seconds"] = 150 if n == 0 else 0
        weights_feedback.write_text(json.dumps(snapshot))
        runs = {}
        for name, weights in (("0.6/0.4", '{"soft_weight":0.6,"behavior_weight":0.4}'),
                              ("0.0/1.0", '{"soft_weight":0.0,"behavior_weight":1.0}')):
            run(f"set-config ranking {weights}")
            run(f"feedback @{weights_feedback}")
            runs[name] = (run("recommend"), t.response, t.run_dispatcher.scored)[1:]
        (base, before), (reweighted, after) = runs["0.6/0.4"], runs["0.0/1.0"]
        assert base.selection.seed == reweighted.selection.seed == first.selection.seed  # fixed seed
        assert base.execution.selection_hash != reweighted.execution.selection_hash
        assert base.pool.pool_version == reweighted.pool.pool_version
        assert before.references == after.references and sorted(before.ids) == sorted(after.ids)
        for k in after.ids:
            old, new = before.breakdown(k), after.breakdown(k)
            assert (old.soft_score, old.behavior_score) == (new.soft_score, new.behavior_score)
            assert (old.soft_weight, old.behavior_weight, new.soft_weight, new.behavior_weight) == (0.6, 0.4, 0.0, 1.0)
            for b, w in ((old, 0.6), (new, 0.0)):
                assert b.combined_score == pytest.approx(w * (b.soft_score or 0) + (1 - w) * (b.behavior_score or 0))
        assert list(before.ranked) != list(after.ranked)  # the weights alone re-ordered the full pool
        assert ids(base) != ids(reweighted)  # behaviour-only fusion changes the drawn listing
        assert all(i.scores.soft_weight == 0.0 and i.scores.behavior_weight == 1.0 for i in reweighted.recommendations)
        run.transcript.append("# weight comparison (fixed seed): " + json.dumps(
            {"0.6/0.4": ids(base), "0.0/1.0": ids(reweighted)}) + "\n")
        run('set-config ranking {"soft_weight":0.6,"behavior_weight":0.4}')
        run("set-seed off")
        unseeded = (run("recommend"), t.response)[1]
        assert unseeded.selection.seed != first.selection.seed
        assert t.store.trace(CONV, unseeded.request_id).get("seed_override") is None

        # 5. Cache: a soft change reuses the pool, a hard change rebuilds it.
        out = run(f"requirements @{requirements(tmp_path, base_criteria(district='D21'), 'soft.json')}")
        assert "cache=reused (HIT)" in run("recommend")
        run(f"requirements @{requirements(tmp_path, base_criteria(budget=2600), 'hard.json')}")
        out = run("recommend")
        assert "cache=built (MISS)" in out and t.response.pool.pool_key != first.pool.pool_key

        # 6. Entity ambiguity: an ID and a name naming different entities -> needs_clarification.
        run(f"requirements @{requirements(tmp_path, base_criteria()[:1] + [GEO_CONFLICT], 'ambiguous.json')}")
        out = run("recommend")
        ambiguous = t.response
        assert ambiguous.status == "needs_clarification" and not ambiguous.recommendations
        assert "resolution: geo ambiguous (id_name_conflict)" in out
        out = run("show-resolution")
        assert "geo: AMBIGUOUS" in out and "needs clarification (2 candidate(s))" in out
        assert [c["entity_id"] for c in payload(out)[0]["candidates"]] == ["URA:AREA:BEDOK", "URA:AREA:CLEMENTI"]

        # 7. Original-request replay: the stored body returns the stored response, whatever the
        # session settings now are; terminal state is unchanged.
        runs_before = t.store.db.execute("SELECT count(*) FROM runs_v2").fetchone()[0]
        out = run(f"replay {first.request_id}")
        assert f"REPLAY of {first.request_id} (stored response)" in out
        assert "Replay returned the stored response unchanged: True" in out
        assert t.store.db.execute("SELECT count(*) FROM runs_v2").fetchone()[0] == runs_before
        assert t.response is ambiguous

        # Export of the latest committed run: request, response, trace, effective config.
        out = run(f"export {tmp_path / 'export'}")
        files = {p.name for p in (tmp_path / "export").iterdir()}
        assert files == {"request.json", "response.json", "trace.json", "config.json"}
        exported = json.loads((tmp_path / "export/config.json").read_text())
        assert exported["config_hash"] == ambiguous.execution.config_hash
        assert RecommendationConfig.from_data(exported["settings"]).config_hash == exported["config_hash"]
        assert json.loads((tmp_path / "export/request.json").read_text())["request_id"] == ambiguous.request_id
    finally:
        (tmp_path / "transcript.txt").write_text("".join(run.transcript))
        run.close()
    assert hashlib.sha256(SETTINGS_PATH.read_bytes()).hexdigest() == yaml_digest  # file never written


def debug_seed(value):
    from recommendation.ranking.selection import derive_seeds

    return derive_seeds(RecommendationConfig.default(), "c", "r", "p", value)["base"]


# --- settings ------------------------------------------------------------------------------------
def test_set_config_is_validated_session_only_and_applies_to_new_requests(tmp_path, capsys):
    yaml_digest = hashlib.sha256(SETTINGS_PATH.read_bytes()).hexdigest()
    # P7: the transcript's tie / draw assertions were written for the pre-calibration values
    # (epsilon 0.02, iqr); they are pinned by a fixture settings file instead of the YAML.
    run = Session(terminal(tmp_path, config=str(PRE_P7_SETTINGS)), capsys)
    t = run.t
    try:
        run(f"requirements @{requirements(tmp_path, base_criteria())}")
        original = t.config.config_hash
        for bad in ("set-config ranking.soft_weight 0.5",  # layer no longer sums to 1
                    "set-config selection.tie_max_score_span -1",
                    "set-config nope.key 1",
                    "set-config ranking.unknown_key 1",
                    "set-config trace.api_key \"x\"",
                    "set-config selection.tie_max_score_span not-json"):
            with pytest.raises(ValueError):
                t.command(bad)
            assert t.config.config_hash == original and t.overrides == {}
        run('set-config ranking {"soft_weight": 0.5, "behavior_weight": 0.5}')
        assert t.config.ranking.soft_weight == 0.5 and t.config.config_hash != original
        shown = payload(run("show-config"))
        assert shown["session_overrides"] == {"ranking": {"soft_weight": 0.5, "behavior_weight": 0.5}}
        assert shown["config_hash"] == t.config.config_hash
        assert payload(run("show-config ranking"))["ranking"]["soft_weight"] == 0.5
        assert payload(run("show-config ranking.soft_weight")) == {"ranking.soft_weight": 0.5}
        with pytest.raises(ValueError, match="Unknown settings key"):
            t.command("show-config ranking.nope")
        # Export round trip: the exported file loads (--config) to the same settings.
        for name in ("effective.yaml", "effective.json"):
            run(f"export-config {tmp_path / name}")
            assert RecommendationConfig.from_file(tmp_path / name).config_hash == t.config.config_hash
        with pytest.raises(ValueError):
            t.command(f"export-config {tmp_path / 'effective.txt'}")
        # New requests only: a staged/pending request is dropped and the next one is new.
        run("recommend")
        response = t.response
        assert response.execution.config_hash == t.config.config_hash
        run("set-config selection.tie_max_score_span 0.05")
        assert t.pending_request is None
        run("recommend")
        assert t.response.request_id != response.request_id
        assert t.response.execution.config_hash == t.config.config_hash
        # The earlier run's stored configuration is unchanged.
        assert t.store.cached(CONV, response.request_id)[2]["config_hash"] == response.execution.config_hash
        run("reset-config")
        assert t.config.config_hash == original and t.overrides == {}
    finally:
        run.close()
    assert hashlib.sha256(SETTINGS_PATH.read_bytes()).hexdigest() == yaml_digest


# --- request identity --------------------------------------------------------------------------
def test_recommend_mints_new_ids_except_retrying_a_failed_pending_request(tmp_path, capsys, monkeypatch):
    run = Session(terminal(tmp_path), capsys)
    t = run.t
    try:
        run(f"requirements @{requirements(tmp_path, base_criteria())}")
        t.ensure_service()
        inner = t.controller.inner
        propose = inner.propose
        failures = iter([True])

        def flaky(dispatcher, context, trace):
            if next(failures, False):
                raise RecommendationError("MODEL_UNAVAILABLE", "simulated outage", True)
            return propose(dispatcher, context, trace)

        monkeypatch.setattr(inner, "propose", flaky)
        run("recommend")
        failed = t.pending_request.request_id
        assert t.response is None and t.store.current(CONV) is None
        run("recommend")  # retry of the failed pending request: same request_id
        assert t.response.request_id == failed
        seen = {failed}
        for change in ("recommend", "set-seed s1", "set-config selection.tie_max_score_span 0.03", None):
            if change == "recommend":
                run("recommend")
            elif change is None:
                # load-request with an already used request_id never reuses it
                body = t.store.cached(CONV, failed)[2]["request"]
                (tmp_path / "request.json").write_text(json.dumps(body))
                out = run(f"load-request @{tmp_path / 'request.json'}")
                assert "recommend mints a new request_id" in out
                run("recommend")
            else:
                run(change)
                run("recommend")
            assert t.response.status == "success" and t.response.request_id not in seen
            seen.add(t.response.request_id)
    finally:
        run.close()


def test_load_request_show_input_and_validate_input(tmp_path, capsys):
    run = Session(terminal(tmp_path), capsys)
    t = run.t
    try:
        run(f"requirements @{requirements(tmp_path, base_criteria())}")
        run("recommend")
        first = t.response
        shown = ids(first)
        body = {"contract_version": "2.0", "request_id": first.request_id, "conversation_id": "elsewhere",
                "requirements_version": 1, "requirements": {"listing_type": "RENT", "criteria": base_criteria()},
                "result_limit": 4, "cold_start": {"exited": True, "exited_pool_key": first.pool.pool_key},
                "feedback": {"previous_round": {"request_id": first.request_id, "shown_listing_ids": shown,
                                                "browsing": [{"listing_id": i, "status": "recorded",
                                                              "duration_seconds": 30} for i in shown]},
                             "appointments": [{"listing_id": shown[0], "statuses": ["CONFIRMED"],
                                               "action_ids": ["a-1"]}]}}
        path = tmp_path / "request.json"
        path.write_text(json.dumps(body))
        run(f"load-request @{path}")
        staged = json.loads(run("show-input").split("\n", 1)[1].rsplit("\nseed override", 1)[0])
        assert staged["request_id"] == "<new request_id minted by recommend>"
        assert staged["conversation_id"] == CONV and staged["previous_state_id"] == first.state.state_id
        assert staged["result_limit"] == 4 and staged["cold_start"] == body["cold_start"]
        assert staged["feedback"] == body["feedback"]
        assert "Input valid" in run("validate-input")
        run("recommend")
        assert t.response.status == "success" and len(t.response.recommendations) == 4
        assert t.store.cached(CONV, t.response.request_id)[2]["request"]["cold_start"]["exited"] is True
        # Contract 1.0 bodies and invalid snapshots are rejected; staged input is kept.
        for bad in ({**body, "contract_version": "1.0"},
                    {**body, "feedback": {**body["feedback"], "previous_round": {
                        **body["feedback"]["previous_round"], "browsing": []}}}):
            path.write_text(json.dumps(bad))
            with pytest.raises(ValueError):
                t.command(f"load-request @{path}")
        # validate-input reports run-consistency problems without submitting anything.
        t.current = None  # simulate a stale head reference
        runs = t.store.db.execute("SELECT count(*) FROM runs_v2").fetchone()[0]
        assert "Input invalid: STALE_REFERENCE" in run("validate-input")
        assert t.store.db.execute("SELECT count(*) FROM runs_v2").fetchone()[0] == runs
    finally:
        run.close()


# --- step mode ---------------------------------------------------------------------------------
def test_step_mode_enforces_dependencies_invalidates_on_input_change_and_is_never_a_recommendation(tmp_path, capsys):
    near = {"criterion_id": "near", "field": "distance", "operator": "lte", "value": 3000, "unit": "m",
            "strength": "soft", "distance_type": "straight_line", "destination_name": "Clementi MRT",
            "weight": 1, "utility": "proximity"}
    run = Session(terminal(tmp_path), capsys)
    t = run.t
    try:
        run(f"requirements @{requirements(tmp_path, base_criteria() + [near])}")
        run("set-config recommendation.default_result_limit 3")
        with pytest.raises(RecommendationError) as raised:
            t.command("step rank_candidates")
        assert raised.value.detail.code == "STALE_REFERENCE"
        assert "Call get_geographic_evidence" in raised.value.detail.message
        with pytest.raises(RecommendationError) as raised:
            t.command("step build_pool")
        assert raised.value.detail.code == "STALE_REFERENCE" and "Call resolve_entities" in raised.value.detail.message
        out = run("run-until build_pool")
        assert "INTERMEDIATE" in out and "pool_build_or_load: ok" in out
        # P6 review: the step's pool_cache write was rolled back, so the record says so.
        assert '"persisted": false' in out and '"persisted": true' not in out
        built = [r for r in t.step.trace["stages"] if r["stage"] == "pool_build_or_load"][-1]
        assert built["outputs"]["persisted"] is False and "rolled back" in built["note"]
        assert t.step.dispatcher.pool_result.persisted is False
        assert t.step.completed() == ["resolve_entities", "build_pool"]
        assert "already current" in run("run-until resolve_entities")
        with pytest.raises(RecommendationError):
            t.command("step final_gate")
        out = run("run-until final_gate")
        assert "final_gate dry run passed (rolled back)" in out
        assert "candidate selection (intermediate, not a recommendation)" in out
        assert "success" not in out and t.response is None
        assert t.store.current(CONV) is None  # nothing committed
        assert t.store.db.execute("SELECT count(*) FROM pool_cache").fetchone()[0] == 0  # rolled back
        assert "INTERMEDIATE" in run("show-selection") and "INTERMEDIATE" in run("show-scores")
        # Seed (or feedback) change -> only the rank stage and the gate.
        out = run("set-seed other")
        assert "invalidated: rank_candidates, final_gate" in out
        assert t.step.completed() == ["resolve_entities", "build_pool", "get_geographic_evidence"]
        # Soft non-geographic change -> rank only; the pool and geographic evidence are kept.
        run("run-until rank_candidates")
        changed = base_criteria(district="D21") + [near]
        out = run(f"requirements @{requirements(tmp_path, changed, 'soft.json')}")
        assert "invalidated: rank_candidates;" in out
        assert t.step.completed() == ["resolve_entities", "build_pool", "get_geographic_evidence"]
        # Soft geographic change (radius) -> geographic evidence and rank; the pool is kept.
        run("run-until rank_candidates")
        out = run(f"requirements @{requirements(tmp_path, changed[:2] + [{**near, 'value': 5000}], 'soft-geo.json')}")
        assert "invalidated: get_geographic_evidence, rank_candidates;" in out
        assert t.step.completed() == ["resolve_entities", "build_pool"]
        # Hard-criteria change -> the pool is invalidated.
        run("run-until rank_candidates")
        out = run(f"requirements @{requirements(tmp_path, base_criteria(budget=2600) + [near], 'hard.json')}")
        assert "invalidated: build_pool, get_geographic_evidence, rank_candidates;" in out
        assert t.step.completed() == ["resolve_entities"]
        # Geographic target change -> resolution too.
        out = run(f"requirements @{requirements(tmp_path, base_criteria()[:1] + [GEO_CONFLICT], 'geo.json')}")
        assert "invalidated: resolve_entities;" in out and t.step.completed() == []
        # Settings: cache -> the pool and later stages.
        run(f"requirements @{requirements(tmp_path, base_criteria())}")
        run("set-seed off")
        run("run-until rank_candidates")
        assert "invalidated: build_pool, get_geographic_evidence, rank_candidates;" in run(
            "set-config cache.ttl_seconds 5")
        # recommend submits exactly the staged request the step session previewed.
        run("run-until final_gate")
        pending = t.pending_request.request_id
        step_ids = list(t.step.dispatcher.selected)
        out = run("recommend")
        assert "Step session closed" in out and t.step is None
        assert t.response.request_id == pending and ids(t.response) == step_ids
    finally:
        run.close()


def test_step_mode_is_scripted_only(tmp_path):
    t = terminal(tmp_path, controller="live")
    try:
        for command in ("step", "run-until build_pool"):
            with pytest.raises(ValueError, match="scripted mode only"):
                t.command(command)
    finally:
        t.store.close()


# --- live progress events ------------------------------------------------------------------------
class FakeLive(LiveController):
    """Scripted fake model: one turn with the pipeline, then the recorded selection."""

    def __init__(self):
        super().__init__()
        self.turn = 0

    def invoke(self, messages, tools, remaining):
        self.turn += 1
        ref = json.loads(messages[1]["content"])["context_ref"]
        if self.turn == 1:
            return {"content": "", "tool_calls": [{"id": str(i), "name": n, "args": {"context_ref": ref}}
                                                  for i, n in enumerate(PIPELINE)]}
        selected = json.loads(messages[-1]["content"])["selected_ids"]
        return {"content": json.dumps({"listing_ids": selected}), "tool_calls": []}


def test_live_mode_prints_structured_progress_events(tmp_path, capsys, monkeypatch):
    import recommendation.terminal.__main__ as main

    monkeypatch.setattr(main, "LiveController", FakeLive)
    run = Session(terminal(tmp_path, controller="live"), capsys)
    t = run.t
    try:
        run(f"requirements @{requirements(tmp_path, base_criteria())}")
        out = run("recommend")
        assert t.response.status == "success" and t.response.execution.controller_mode == "live"
        log = t.events.log
        kinds = [e.get("event") for e in log]
        assert kinds[:2] == ["model_start", "model_end"] and log[1]["tool_calls"] == 4
        tools = [e for e in log if e.get("event") == "tool_end"]
        assert [e["tool"] for e in tools] == list(PIPELINE)
        assert all(e["status"] == "ok" and e["duration_ms"] >= 0 for e in tools)
        assert tools[1]["pool"] == 9 and tools[-1]["ranked"] == 9 and tools[-1]["selected"] == 9
        assert kinds[-3:] == ["model_end", "gate_start", "gate_end"] and log[-1]["status"] == "ok"
        assert "[progress] model: turn 1 start" in out and "[progress] model: turn 2 end" in out
        assert "final answer" in out and "[progress] gate: final_gate ok (selected=9, rows_read=9)" in out
        assert "[progress] tool: rank_candidates end ok" in out and "pool=9, ranked=9, selected=9" in out
    finally:
        run.close()


def test_live_progress_reports_a_failed_gate_before_the_repair(tmp_path, capsys, monkeypatch):
    import recommendation.terminal.__main__ as main

    class WrongFirst(FakeLive):
        def invoke(self, messages, tools, remaining):
            if self.turn == 0:
                return super().invoke(messages, tools, remaining)
            self.turn += 1
            if self.turn == 2:  # not the recorded selection -> INVALID_PROPOSAL, then a repair turn
                return {"content": json.dumps({"listing_ids": ["fixture-01"]}), "tool_calls": []}
            ranked = [json.loads(m["content"]) for m in messages if m.get("role") == "tool"][-1]
            return {"content": json.dumps({"listing_ids": ranked["selected_ids"]}), "tool_calls": []}

    monkeypatch.setattr(main, "LiveController", WrongFirst)
    run = Session(terminal(tmp_path, controller="live"), capsys)
    try:
        run(f"requirements @{requirements(tmp_path, base_criteria())}")
        out = run("recommend")
        assert run.t.response.status == "success"
        gates = [e for e in run.t.events.log if e.get("event") == "gate_end"]
        assert [g["status"] for g in gates] == ["error", "ok"]
        assert "gate: final_gate error INVALID_PROPOSAL" in out
    finally:
        run.close()


# --- trace, help, replay errors ----------------------------------------------------------------
def test_show_trace_debug_flag_and_help(tmp_path, capsys):
    run = Session(terminal(tmp_path), capsys)
    t = run.t
    try:
        run(f"requirements @{requirements(tmp_path, base_criteria())}")
        run("recommend")
        summary = payload(run("show-trace"))
        assert summary["layout"] == "slim-v1" and "debug" not in summary
        assert "no debug block" in run("show-trace --debug")
        run('set-config trace.mode "debug"')
        run("recommend")
        out = run("show-trace")
        assert "debug rows omitted: show-trace --debug" in out
        full = payload(run(f"show-trace {t.response.request_id} --debug"))
        assert full["debug"]["rows"] > 0 and full["trace_mode"] == "debug"
        with pytest.raises(ValueError):
            t.command("show-trace --bogus")
        help_text = run("help")
        assert "without soft criteria C = 0, so cold start cannot be exited without soft criteria" in help_text
        for command in debug.COMMANDS:
            assert command in help_text
        with pytest.raises(ValueError, match="No committed run"):
            t.command("replay not-a-request")
    finally:
        run.close()


def test_inspection_after_restart_is_labelled_recomputed(tmp_path, capsys):
    first = Session(terminal(tmp_path), capsys)
    try:
        first(f"requirements @{requirements(tmp_path, base_criteria())}")
        first("recommend")
        response = first.t.response
    finally:
        first.close()
    resumed = Session(terminal(tmp_path), capsys)
    try:
        assert resumed.t.run_dispatcher is None and resumed.t.response.request_id == response.request_id
        chosen = response.recommendations[0]
        out = resumed(f"inspect-listing {chosen.listing.listing_id}")
        assert "RECOMPUTED from the current data" in out
        info = payload(out)
        assert info["recomputed"] is True and info["score_rank"] == chosen.score_rank
        out = resumed("show-pool")
        assert "pool_cache content of pool_version" in out and payload(out)["pool"]["size"] == response.pool.size
        out = resumed("show-scores --top 3")
        assert "Retained full-pool scores are unavailable" in out
        assert len(payload(out)["returned_items"]) == len(response.recommendations)
    finally:
        resumed.close()


def test_inspection_recomputes_a_run_stored_before_trace_max_string_chars(tmp_path, capsys):
    # P7 review: snapshots stored before P7 lack the trace-only key trace.max_string_chars; the
    # recompute fills it from the current settings (never result-affecting) and says so.
    import sqlite3

    first = Session(terminal(tmp_path), capsys)
    try:
        first(f"requirements @{requirements(tmp_path, base_criteria())}")
        first("recommend")
        response = first.t.response
    finally:
        first.close()
    db = sqlite3.connect(tmp_path / "state.sqlite")
    snapshot = json.loads(db.execute("SELECT snapshot FROM runs_v2 WHERE request_id=?", (response.request_id,)).fetchone()[0])
    del snapshot["configuration"]["trace"]["max_string_chars"]
    with pytest.raises(Exception, match="trace.max_string_chars"):
        RecommendationConfig.from_data(snapshot["configuration"], "pre-P7 snapshot")
    db.execute("UPDATE runs_v2 SET snapshot=? WHERE request_id=?", (json.dumps(snapshot), response.request_id))
    db.commit()
    db.close()
    resumed = Session(terminal(tmp_path), capsys)
    try:
        chosen = response.recommendations[0]
        out = resumed(f"inspect-listing {chosen.listing.listing_id}")
        assert "predates trace.max_string_chars" in out and "RECOMPUTED from the current data" in out
        info = payload(out)
        assert info["recomputed"] is True and info["score_rank"] == chosen.score_rank
    finally:
        resumed.close()
    config, filled = debug.stored_configuration(snapshot["configuration"], RecommendationConfig.default())
    assert filled == ["trace.max_string_chars"] and config.trace.max_string_chars == RecommendationConfig.default().trace.max_string_chars
    incomplete = json.loads(json.dumps(snapshot["configuration"]))
    del incomplete["selection"]["tie_max_score_span"]  # result-affecting keys are never filled in
    with pytest.raises(Exception, match="tie_max_score_span"):
        debug.stored_configuration(incomplete, RecommendationConfig.default())


def test_export_show_trace_and_show_scores_use_the_runs_own_trace_after_a_replay(tmp_path, capsys):
    # P6 review: a replay of a request saves a newer trace (replay gate only) under the same
    # request_id; export / show-trace <id> / the show-scores fallback must still read the run's
    # own trace (response.execution.trace_id).
    run = Session(terminal(tmp_path), capsys)
    try:
        run(f"requirements @{requirements(tmp_path, base_criteria())}")
        run("recommend")
        response = run.t.response
        run(f"replay {response.request_id}")
        replay_trace = run.t.store.trace(CONV, response.request_id)
        assert replay_trace["trace_id"] != response.execution.trace_id
        assert [s["stage"] for s in replay_trace["stages"]] == ["input_validation", "final_gate"]
        run(f"export {tmp_path / 'export'}")
        exported = json.loads((tmp_path / "export/trace.json").read_text())
        assert exported["trace_id"] == response.execution.trace_id
        assert [s["stage"] for s in exported["stages"] if s["stage"] == "scoring"] == ["scoring"]
        replayed = json.loads((tmp_path / "export/latest-replay-trace.json").read_text())
        assert replayed["trace_id"] == replay_trace["trace_id"]
        out = run(f"show-trace {response.request_id}")
        assert payload(out)["trace_id"] == response.execution.trace_id
        assert f"has a newer trace {replay_trace['trace_id']} (immutable replay)" in out
        out = run(f"show-trace {response.request_id} --latest")
        assert payload(out)["trace_id"] == replay_trace["trace_id"] and "immutable replay trace of request" in out
        assert "immutable replay trace of request" in run("show-trace")
    finally:
        run.close()
    resumed = Session(terminal(tmp_path), capsys)
    try:
        preview = payload(resumed("show-scores --top 3"))["trace_scoring_preview"]
        assert [r["listing_id"] for r in preview] and len(preview) == 3
    finally:
        resumed.close()


def test_script_mode_echoes_commands(tmp_path, capsys):
    script = tmp_path / "commands.txt"
    req = requirements(tmp_path, base_criteria())
    script.write_text(f"# comment\nrequirements @{req}\nshow-config selection\nbogus\nexit\nrecommend\n")
    t = terminal(tmp_path, script=str(script))
    t.run()
    out = capsys.readouterr().out
    assert f"recommendation> requirements @{req}" in out and "recommendation> show-config selection" in out
    assert "Unknown command" in out and "recommendation> recommend" not in out
