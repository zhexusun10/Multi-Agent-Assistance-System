"""P6 part 1 (spec section 9, R13, R14; section 13 P6): slim trace stages, compact tool and model
steps, debug mode, references (v2 and legacy), credentials, error location, bounded size and
the /app/recommend proofs.

Fixture data mode with the scripted controller (or a scripted fake model) and a private state
file (conftest isolation). Synthetic pools come from test_selection_v1 (plan_rows: 3 leaders,
an exact tie group, then the rest).
"""
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recommendation.app import app

from recommendation.agent.controller import Completion, LiveController, ScriptedController
from recommendation.config import RecommendationConfig
from recommendation.contracts.errors import RecommendationError
from recommendation.ranking import exploration, selection
from recommendation.repositories.connections import repositories
from recommendation.service import RecommendationService
from recommendation.settings import pending
from recommendation.state import pool_cache as pool_cache_module
from recommendation.state.store import StateStore, is_legacy_trace
from recommendation.tools import dispatcher as dispatcher_module
from recommendation.tools.registry import PIPELINE
from recommendation.tracing import slim

from .test_pool_cache import BUDGET, NEAR_SCHOOL, SCHOOL, body
from .test_selection_v1 import PLAN, make_request, plan_rows, settings, synthetic_service

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "evaluation/recommendation-exploration-2026-10-23/baseline"
PIPELINE_STAGES = ("entity_resolution", "pool_build_or_load", "geographic_evidence", "scoring",
                   "cold_start_diagnostics", "price_sampling", "final_selection")


@pytest.fixture(autouse=True)
def fresh_memory():
    pool_cache_module.MEMORY.clear()
    yield
    pool_cache_module.MEMORY.clear()


def fixture_service(tmp_path, controller=None, config=None):
    listing, geo = repositories("fixture")
    return RecommendationService(listing, geo, StateStore(tmp_path / "state.sqlite"),
                                 controller or ScriptedController(), config=config)


def stored_text(service, conversation):
    """Raw persisted trace content (what a reader of the state file sees)."""
    db = sqlite3.connect(service.store.path)
    try:
        return "\n".join(r[0] for r in db.execute("SELECT content FROM traces WHERE conversation_id=?",
                                                  (conversation,)))
    finally:
        db.close()


def sub(tmp_path, name):
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def stage(trace, name):
    return [s for s in trace["stages"] if s["stage"] == name][-1]


class RepeatedQueries(LiveController):
    """Fake model: the pipeline in one turn, then ``queries`` turns of nearest_mrt over as many
    pool IDs as the model may send (controller.model_result_candidates), then the selection."""

    def __init__(self, queries):
        super().__init__()
        self.queries = queries
        self.turn = 0
        self.received = []

    def invoke(self, messages, tools, remaining):
        self.received.append(json.loads(json.dumps(messages)))
        self.turn += 1
        ref = json.loads(messages[1]["content"])["context_ref"]
        if self.turn == 1:
            return {"content": "", "tool_calls": [
                {"id": f"p{i}", "name": n, "args": {"context_ref": ref}} for i, n in enumerate(PIPELINE)]}
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if self.turn <= self.queries + 1:
            ids = next(r for r in results if r["tool"] == "build_pool")["candidate_ids"]
            return {"content": "", "tool_calls": [
                {"id": f"q{self.turn}", "name": "nearest_mrt", "args": {"context_ref": ref, "listing_ids": ids}}]}
        selected = next(r for r in reversed(results) if r["tool"] == "rank_candidates")["ranking_artifact_id"]
        return {"content": json.dumps({"ranking_artifact_id": selected}), "tool_calls": []}


# --- stage records ------------------------------------------------------------------------------
def test_stage_records_cover_every_stage_with_counts_params_versions_and_bounded_previews(tmp_path):
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        response = service.recommend(make_request(PLAN, "st-1", "st"))
        assert response.status == "success" and response.selection.mode == "cold_start_exploration"
        trace = service.store.trace("st", "st-1")
    finally:
        service.close()
    rows = service.config.trace.preview_rows
    assert trace["format"] == 2 and trace["layout"] == slim.LAYOUT and trace["trace_mode"] == "summary"
    assert [s["stage"] for s in trace["stages"]] == ["input_validation", *PIPELINE_STAGES, "final_gate",
                                                    "response_assembly"]
    for record in trace["stages"]:
        assert record["status"] == "ok", record
        assert record["duration_ms"] >= 0 and isinstance(record["inputs"], dict) and isinstance(record["outputs"], dict)
        assert record["versions"]["config_hash"] == response.execution.config_hash
        assert record["versions"]["selection_hash"] == response.execution.selection_hash
        assert len(record["preview"]) <= rows and record["preview_total"] >= len(record["preview"])
        assert "error" not in record
    for name in PIPELINE_STAGES:
        record = stage(trace, name)
        # Entity resolution runs before any pool exists; every later stage names its pool version.
        assert record["versions"].get("pool_version") == (
            None if name == "entity_resolution" else response.pool.pool_version)
        # Tool stages name their tool call; the code stages after the agent loop name the ranking artifact.
        code_stage = name in ("cold_start_diagnostics", "price_sampling", "final_selection")
        assert record["call"].startswith(f"{trace['trace_id']}/{'ranking' if code_stage else 'tools'}/")
    pool = stage(trace, "pool_build_or_load")
    assert pool["outputs"]["size"] == response.pool.size == 40 and pool["preview_total"] == 40
    assert pool["outputs"]["cache"] == "built" and pool["inputs"]["source_count"] == 40
    scoring = stage(trace, "scoring")
    assert scoring["outputs"]["scored"] == 40 and scoring["params"]["scoring_version"] == response.execution.scoring_version
    assert [r["listing_id"] for r in scoring["preview"]] == [f"L{k:03d}" for k in range(1, rows + 1)]
    assert scoring["preview_total"] == 40 > len(scoring["preview"])  # P6 review: the uncut row count
    cold = stage(trace, "cold_start_diagnostics")
    detector = response.selection.cold_start
    assert cold["outputs"]["active"] is True
    assert cold["outputs"]["mean_pairwise_difference"] == detector.mean_pairwise_difference
    assert cold["outputs"]["evaluated_count"] == detector.evaluated_count == 30
    assert cold["params"] == {"enabled": True, "detector": "mean_pairwise_difference", "detector_version": "mpd-v1",
                              "candidate_window": 30, "min_mean_pairwise_difference": detector.threshold,
                              "request_cold_start": {"exited": False, "exited_pool_key": None},
                              "request_cold_start_used": False}
    sampling = stage(trace, "price_sampling")
    assert sampling["inputs"]["candidates"] == response.selection.exploration.candidate_count
    assert sampling["outputs"]["drawn"] == 7 and len(sampling["preview"]) == len(response.selection.exploration.bins)
    final = stage(trace, "final_selection")
    assert final["outputs"]["mode"] == "cold_start_exploration" and final["params"]["seed"] == response.selection.seed
    assert [r["listing_id"] for r in final["preview"]] == [i.listing.listing_id for i in response.recommendations]
    assert [r["selection_reason"] for r in final["preview"]] == [i.selection_reason for i in response.recommendations]
    validation = stage(trace, "input_validation")
    assert validation["inputs"]["criteria"] == 2 and validation["outputs"]["result_limit"] == 10
    assert stage(trace, "final_gate")["outputs"]["selected"] == 10


def test_price_sampling_is_skipped_outside_cold_start_and_not_evaluated_diagnostics_say_why(tmp_path):
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40),
                                config=settings(**{"cold_start.enabled": False}))
    try:
        service.recommend(make_request(PLAN, "sk-1", "sk"))
        trace = service.store.trace("sk")
    finally:
        service.close()
    assert stage(trace, "price_sampling")["status"] == "skipped"
    assert "near_tie_sampling" in stage(trace, "price_sampling")["note"]
    assert stage(trace, "cold_start_diagnostics")["status"] == "skipped"
    assert stage(trace, "cold_start_diagnostics")["note"] == "cold_start_disabled"
    assert stage(trace, "final_selection")["outputs"]["mode"] == "near_tie_sampling"


# --- tool and model steps -----------------------------------------------------------------------
def test_default_trace_does_not_serialize_the_pool_into_tool_steps(tmp_path):
    service = synthetic_service(tmp_path, plan_rows(3, 30, 4000))
    try:
        response = service.recommend(make_request(PLAN, "np-1", "np"))
        trace = service.store.trace("np")
        text = stored_text(service, "np")
    finally:
        service.close()
    assert response.pool.size == 4000
    tools = [s for s in trace["steps"] if s["kind"] == "tool"]
    assert [s["tool"] for s in tools] == list(PIPELINE)
    for step in tools:
        assert "snapshot" not in step and set(step) == {"kind", "tool", "snapshot_ref", "stages", "arguments",
                                                         "result", "duration_ms"}
        result = step["result"]
        assert len(result["candidate_ids"]) <= service.config.trace.preview_rows
        assert len(result["ranked_ids"]) <= service.config.trace.preview_rows
        assert len(result["previews"]) <= service.config.trace.preview_rows
    build = tools[1]["result"]
    assert build["candidate_count"] == 4000 and build["candidate_ids_truncated"]
    assert build["data"]["pool"]["pool_version"] == response.pool.pool_version
    # No pool rows anywhere: the number of distinct pool listings named in the stored trace is
    # bounded by the previews and the selection, not by the 4,000 pool listings.
    named = set(re.findall(r'"L\d{3,4}"', text))
    assert len(named) <= 70, len(named)
    assert '"rows"' not in text and "nearest_exits" not in text and '"validation"' not in text


def test_trace_size_is_independent_of_pool_size(tmp_path):
    sizes = {}
    for n in (40, 400, 4000):
        service = synthetic_service(sub(tmp_path, str(n)), plan_rows(3, 30, n))
        try:
            response = service.recommend(make_request(PLAN, f"sz-{n}", "sz"))
            assert response.status == "success" and response.pool.size == n
            sizes[n] = len(stored_text(service, "sz"))
        finally:
            service.close()
    # Measured (P6): ~63.5 KB for every pool size; the IDs grow by one character from L999 to L1000.
    assert max(sizes.values()) - min(sizes.values()) < 0.01 * min(sizes.values()), sizes


def test_trace_size_is_bounded_under_repeated_tool_calls_and_capped_by_model_result_candidates(tmp_path):
    sizes = {}
    for n in (40, 400, 4000):
        for queries in (2, 5):
            model = RepeatedQueries(queries)
            service = synthetic_service(sub(tmp_path, f"{n}-{queries}"), plan_rows(3, 30, n), controller=model)
            try:
                response = service.recommend(make_request(PLAN, f"rq-{n}-{queries}", "rq"))
                assert response.status == "success", response.error
                trace = service.store.trace("rq")
                sizes[n, queries] = len(stored_text(service, "rq"))
            finally:
                service.close()
            queries_steps = [s for s in trace["steps"] if s.get("tool") == "nearest_mrt"]
            assert len(queries_steps) == queries
            sent = min(n, service.config.controller.model_result_candidates)
            for step in queries_steps:
                assert len(step["arguments"]["listing_ids"]) == service.config.trace.preview_rows
                assert step["arguments"]["_truncated"] == {"listing_ids": sent}
                assert len(step["result"]["data"]["results"]) == service.config.trace.preview_rows
                assert step["result"]["data_truncated"]["results"] == sent
            # Model steps store each message once: the added messages form the transcript.
            models = [s for s in trace["steps"] if s["kind"] == "model"]
            assert sum(len(s["messages_added"]) for s in models) == models[-1]["message_count"]
            assert [s["message_offset"] for s in models[1:]] == [s["message_count"] for s in models[:-1]]
            assert "messages" not in models[-1] and "tool_schemas" not in models[-1]
    per_call = {n: (sizes[n, 5] - sizes[n, 2]) / 3 for n in (40, 400, 4000)}
    # Above controller.model_result_candidates (100) the pool size changes neither the total nor
    # the growth per tool call (bounded by the settings: preview_rows in tool steps,
    # model_result_candidates in the model-facing messages).
    assert abs(sizes[400, 5] - sizes[4000, 5]) < 0.01 * sizes[400, 5], sizes
    assert abs(per_call[400] - per_call[4000]) < 0.01 * per_call[400], per_call
    # Below the cap (P6 review) a live query call stores what the model saw: the growth per call
    # follows min(pool, model_result_candidates) and never exceeds the capped growth. So a live
    # trace is bounded by max_tool_calls x model_result_candidates, not by the pool size.
    assert per_call[40] < per_call[400], per_call


def test_model_steps_store_added_messages_and_references_reconstruct_what_the_model_received(tmp_path):
    model = RepeatedQueries(2)
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40), controller=model)
    try:
        response = service.recommend(make_request(PLAN, "mm-1", "mm"))
        assert response.status == "success"
        trace = service.store.trace("mm")
        models = [s for s in trace["steps"] if s["kind"] == "model"]
        assert len(models) == len(model.received) == 4
        for step, received in zip(models, model.received):
            resolved = service.store.resolve_reference("mm", step["context_ref"])
            assert resolved["messages"] == received
            assert resolved["tool_schemas"] == trace["tool_schemas"]
        transcript = service.store.resolve_reference("mm", f"{trace['trace_id']}/messages")["messages"]
        assert transcript[:len(model.received[-1])] == model.received[-1]
        # snapshot_ref keeps working for v2 runs: it returns the compact step record.
        for step in (s for s in trace["steps"] if s["kind"] == "tool"):
            assert service.store.resolve_reference("mm", step["snapshot_ref"]) == step
        with pytest.raises(RecommendationError) as unknown:
            service.store.resolve_reference("mm", f"{trace['trace_id']}/tools/999")
        assert unknown.value.detail.code == "STALE_REFERENCE"
        with pytest.raises(RecommendationError):
            service.store.resolve_reference("other-conversation", models[0]["context_ref"])
    finally:
        service.close()


def test_listing_text_with_at_signs_does_not_redact_model_messages(tmp_path):
    # P6 review: compact JSON such as '"title":"Suites@Changi","price":3500.0' was read as a
    # scheme-less 'user:pass@host:port' and the whole tool message became [REDACTED]. Titles of
    # this shape exist in the live table (e.g. 'Studios@Tembeling', '21@Angsana').
    rows = plan_rows(3, 30, 40)
    titles = ["Suites@Changi", "21@Angsana", "Eight@18Fort", "70@Truro", "see https://example.com/a@b"]
    for n, title in enumerate(titles):
        rows[n] = rows[n].model_copy(update={"title": title})
    model = RepeatedQueries(1)
    service = synthetic_service(tmp_path, rows, controller=model)
    try:
        response = service.recommend(make_request(PLAN, "at-1", "at"))
        assert response.status == "success", response.error
        trace = service.store.trace("at")
        models = [s for s in trace["steps"] if s["kind"] == "model"]
        assert len(models) == len(model.received) == 3
        for step, received in zip(models, model.received):
            assert service.store.resolve_reference("at", step["context_ref"])["messages"] == received
        assert slim.REDACTED not in stored_text(service, "at")
        assert any("Suites@Changi" in m["content"] for m in model.received[-1] if m["role"] == "tool")
    finally:
        service.close()
    # A credential inside JSON content still goes; only that value is replaced.
    leaked = json.dumps({"tool": "x", "error": {"message": "connect failed: user:pw@db:5432"}, "title": "Suites@Changi"},
                        separators=(",", ":"))
    cleaned = json.loads(slim.scrub({"content": leaked})["content"])
    assert cleaned == {"tool": "x", "error": {"message": slim.REDACTED}, "title": "Suites@Changi"}
    assert slim.scrub("postgresql://app:pw@db:5432/x") == slim.REDACTED


def test_older_v2_model_steps_resolve_the_whole_transcript_without_a_slim_layout():
    # P6 review: a P2-P5 v2 trace (full 'messages' per model step, no message_offset) raised
    # KeyError for '<trace>/messages'.
    older = {"format": 2, "trace_id": "t", "steps": [
        {"kind": "model", "context_ref": "t/models/0", "messages": [{"role": "user", "content": "a"}], "tool_schemas": []},
        {"kind": "tool", "snapshot_ref": "t/tools/1", "snapshot": {}},
        {"kind": "model", "context_ref": "t/models/1", "tool_schemas": [],
         "messages": [{"role": "user", "content": "a"}, {"role": "tool", "content": "b"}]}]}
    assert slim.resolve_step(older, "t/messages") == {
        "messages": [{"role": "user", "content": "a"}, {"role": "tool", "content": "b"}], "tool_schemas": []}
    assert slim.resolve_step(older, "t/models/0")["messages"] == [{"role": "user", "content": "a"}]
    assert slim.resolve_step({"format": 2, "trace_id": "t", "steps": []}, "t/messages") == {
        "messages": [], "tool_schemas": None}


def test_pool_reference_resolves_from_pool_cache_and_reports_eviction(tmp_path):
    config = settings(**{"cache.pool_retention_per_conversation": 1, "cache.max_entries_per_conversation": 1})
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40), config=config)
    try:
        first = service.recommend(make_request(PLAN, "pr-1", "pr"))
        content = service.store.resolve_reference("pr", first.state.candidate_pool_ref)
        assert len(content["rows"]) == 40 and content["pool_ref"]["pool_version"] == first.pool.pool_version
        # A different hard criterion builds another pool; retention 1 evicts the first one.
        second = service.recommend(make_request([*PLAN, {**BUDGET, "value": 2000}], "pr-2", "pr")
                                   .model_copy(update={"previous_state_id": first.state.state_id,
                                                       "requirements_version": 2}))
        assert second.pool.pool_version != first.pool.pool_version
        with pytest.raises(RecommendationError) as evicted:
            service.store.resolve_reference("pr", first.state.candidate_pool_ref)
        assert evicted.value.detail.code == "STALE_REFERENCE" and "evicted" in evicted.value.detail.message
    finally:
        service.close()


# --- legacy and older v2 traces -----------------------------------------------------------------
def test_legacy_v1_trace_is_readable_with_an_explicit_note(tmp_path, capsys):
    legacy = json.loads((BASELINE / "initial.trace.json").read_text())
    assert "format" not in legacy
    store = StateStore(tmp_path / "state.sqlite")
    try:
        store.db.execute("INSERT INTO traces VALUES(?,?,?,?)", ("old", legacy["trace_id"], "r-old", json.dumps(legacy)))
        store.db.commit()
        assert is_legacy_trace(store.trace("old"))
        whole = store.resolve_reference("old", legacy["trace_id"])
        assert whole["legacy_note"] == slim.LEGACY_NOTE and whole["steps"] == legacy["steps"]
        step = legacy["steps"][0]
        snap = store.resolve_reference("old", step["snapshot_ref"])
        assert snap["legacy_note"] == slim.LEGACY_NOTE
        assert {k: v for k, v in snap.items() if k != "legacy_note"} == step["snapshot"]
        # Older v2 traces (P2-P5) stored the dispatcher snapshot in the step: still returned as is.
        older = {"format": 2, "trace_id": "v2-old", "steps": [
            {"kind": "tool", "snapshot_ref": "v2-old/tools/1", "snapshot": {"rows": [{"listing_id": "x"}]}},
            {"kind": "model", "context_ref": "v2-old/models/0", "messages": [{"role": "user", "content": "hi"}],
             "tool_schemas": []}]}
        store.db.execute("INSERT INTO traces VALUES(?,?,?,?)", ("old", "v2-old", "r-v2", json.dumps(older)))
        store.db.commit()
        assert store.resolve_reference("old", "v2-old/tools/1") == {"rows": [{"listing_id": "x"}]}
        assert store.resolve_reference("old", "v2-old/models/0")["messages"] == [{"role": "user", "content": "hi"}]
    finally:
        store.close()
    from recommendation.terminal.__main__ import Terminal

    class Args:
        state_path = str(tmp_path / "state.sqlite")
        conversation_id = "old"
        data_mode = "fixture"
        controller = "scripted"
        config = None

    terminal = Terminal(Args())
    try:
        terminal.command("show-trace r-old")
    finally:
        terminal.store.close()
    assert "Note: legacy v1 trace" in capsys.readouterr().out


# --- debug mode -------------------------------------------------------------------------------
def test_debug_mode_adds_bounded_rows_with_contributions_tie_group_and_bins(tmp_path):
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40), config=settings(**{"trace.mode": "debug"}))
    try:
        response = service.recommend(make_request(PLAN, "db-1", "db"))
        trace = service.store.trace("db")
    finally:
        service.close()
    debug = trace["debug"]
    assert trace["trace_mode"] == "debug" and debug["max_rows"] == service.config.trace.debug_max_rows == 200
    tie = response.selection.tie_group
    assert debug["tie_group"]["total"] == tie.size == len(debug["tie_group"]["members"]) == 27
    assert {m["score"] for m in debug["tie_group"]["members"]} == {tie.max_score}
    explored = debug["exploration"]
    assert explored["total"] == response.selection.exploration.candidate_count == len(explored["candidates"])
    assert sum(c["drawn"] for c in explored["candidates"]) == 7
    assert sum(c["stage_a"] for c in explored["candidates"]) == response.selection.exploration.sample_size
    top = debug["top_scores"]["rows"]
    assert len(top) == 40 and top[0]["listing_id"] == "L001" and top[0]["contributions"]
    assert debug["rows"] == 27 + 30 + 40 <= debug["max_rows"]
    # Summary mode (default) adds no debug data.
    plain = synthetic_service(sub(tmp_path, "plain"), plan_rows(3, 30, 40))
    try:
        plain.recommend(make_request(PLAN, "db-2", "db"))
        assert "debug" not in plain.store.trace("db")
    finally:
        plain.close()


def _debug_of(tmp_path, name, rows, **overrides):
    config = settings(**{"trace.mode": "debug", **overrides})
    service = synthetic_service(sub(tmp_path, name), rows, config=config)
    try:
        response = service.recommend(make_request(PLAN, f"{name}-1", name))
        return response, service.store.trace(name)["debug"], service.config
    finally:
        service.close()


def test_debug_rows_never_exceed_debug_max_rows_and_keep_every_kind_of_row(tmp_path):
    # P6 review: the budget is shared (drawn listings, preview_rows top scores, then tie members
    # and exploration candidates round robin), so a large tie group no longer crowds out the top
    # scores and the exploration candidates.
    response, debug, config = _debug_of(tmp_path, "dm", plan_rows(3, 30, 400), **{"trace.debug_max_rows": 20})
    assert response.selection.mode == "cold_start_exploration"
    assert debug["rows"] == 20 == debug["max_rows"]
    top, tie, explored = debug["top_scores"], debug["tie_group"], debug["exploration"]
    assert top["total"] == 400 and top["included"] == len(top["rows"]) == config.trace.preview_rows
    assert top["rows"][0]["contributions"] and [r["score_rank"] for r in top["rows"]] == list(range(1, 11))
    assert tie["total"] == 27 and tie["included"] == len(tie["members"]) == 2
    assert explored["total"] == 30 and explored["included"] == len(explored["candidates"]) == 8
    drawn = [i.listing.listing_id for i in response.recommendations if i.selection_reason == "price_exploration_sample"]
    assert sorted(c["listing_id"] for c in explored["candidates"] if c["drawn"]) == sorted(drawn) and len(drawn) == 7
    for rows in (tie["members"], explored["candidates"], top["rows"]):
        assert [r["score_rank"] for r in rows] == sorted(r["score_rank"] for r in rows)  # score order kept
    # The reviewer's case: a tie group (297) larger than debug_max_rows (200).
    response, debug, _ = _debug_of(tmp_path, "big", plan_rows(3, 300, 400))
    assert response.selection.tie_group.size == 297 and debug["rows"] == 200
    assert debug["top_scores"]["included"] >= 10 and debug["tie_group"]["included"] > 0
    assert sum(c["drawn"] for c in debug["exploration"]["candidates"]) == 7
    # Near-tie sampling (no exploration): the drawn tie members come first.
    response, debug, _ = _debug_of(tmp_path, "nt", plan_rows(3, 30, 400),
                                   **{"trace.debug_max_rows": 12, "cold_start.enabled": False})
    assert response.selection.mode == "near_tie_sampling" and debug["exploration"] is None
    assert debug["rows"] == 12 and debug["tie_group"]["included"] == 7 == sum(m["drawn"] for m in debug["tie_group"]["members"])
    assert debug["top_scores"]["included"] == 5


def test_trace_mode_env_toggles_debug_and_is_in_effect(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOMMENDATION_TRACE_MODE", "debug")
    config = RecommendationConfig.default()
    assert config.trace.mode == "debug"
    assert pending.covering_entry("trace.mode") is None and pending.covering_entry("trace.debug_max_rows") is None
    service = fixture_service(tmp_path, config=config)
    try:
        response = service.recommend(body([BUDGET], "env-1", "env"))
        assert response.status == "success"
        trace = service.store.trace("env")
        assert trace["trace_mode"] == "debug" and trace["debug"]["top_scores"]["rows"]
    finally:
        service.close()


# --- credentials ------------------------------------------------------------------------------
def test_trace_never_stores_credentials(tmp_path, monkeypatch):
    secret = "S3cr3t-sentinel-value"
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PGPASSWORD", "NEO4J_PASSWORD", "RECOMMENDATION_SERVICE_TOKEN"):
        monkeypatch.setenv(key, secret)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://app:{secret}@localhost:5432/listings")

    class Leaky(LiveController):
        turn = 0

        def invoke(self, messages, tools, remaining):
            self.turn += 1
            ref = json.loads(messages[1]["content"])["context_ref"]
            if self.turn == 1:
                return {"content": f"postgresql://app:{secret}@db:5432/x", "tool_calls": [
                    {"id": "leak", "name": "resolve_entities",
                     "args": {"context_ref": ref, "api_key": secret, "password": secret,
                              "databaseUrl": f"postgres://u:{secret}@h:5432/d", "accessToken": secret,
                              "note": f"user:{secret}@host:5432/db"}},
                    *({"id": f"p{i}", "name": n, "args": {"context_ref": ref}} for i, n in enumerate(PIPELINE))]}
            selected = next(json.loads(m["content"]) for m in reversed(messages)
                            if m["role"] == "tool" and json.loads(m["content"])["tool"] == "rank_candidates")
            return {"content": json.dumps({"ranking_artifact_id": selected["ranking_artifact_id"]}), "tool_calls": [],
                    "usage": {"total_tokens": 11}}

    service = fixture_service(tmp_path, controller=Leaky())
    try:
        response = service.recommend(body([BUDGET], "cred-1", "cred"))
        assert response.status == "success" and response.execution.tokens == 11
        text = stored_text(service, "cred")
        trace = service.store.trace("cred")
    finally:
        service.close()
    assert secret not in text
    assert slim.REDACTED in text
    leak = next(s for s in trace["steps"] if s["kind"] == "tool")
    assert leak["arguments"]["api_key"] == leak["arguments"]["password"] == slim.REDACTED
    assert leak["arguments"]["accessToken"] == leak["arguments"]["databaseUrl"] == slim.REDACTED
    # Non-secret fields with similar names survive (token usage counts are not credentials).
    assert [s for s in trace["steps"] if s["kind"] == "model"][-1]["output"]["usage"] == {"total_tokens": 11}


# --- errors locate a stage --------------------------------------------------------------------
def _boom(*args, **kwargs):
    raise RuntimeError("injected failure")


def _failed(service, conversation):
    trace = service.store.trace(conversation)
    failed = [s for s in trace["stages"] if s["status"] == "error"]
    return trace, failed


@pytest.mark.parametrize("target,criteria,expected", [
    ("resolve", [SCHOOL], "entity_resolution"),
    ("retrieve", [BUDGET], "pool_build_or_load"),
    ("evidence", [BUDGET, NEAR_SCHOOL], "geographic_evidence"),
    ("score", [BUDGET], "scoring"),
])
def test_errors_locate_the_pipeline_stage(tmp_path, monkeypatch, target, criteria, expected):
    service = fixture_service(tmp_path)
    if target == "resolve":
        monkeypatch.setattr(service.geographic, "resolve_criteria", _boom)
    elif target == "retrieve":
        monkeypatch.setattr(service.listing, "retrieve_versioned", _boom)
    elif target == "evidence":
        monkeypatch.setattr(service.geographic, "evidence", _boom)
    else:
        monkeypatch.setattr(dispatcher_module, "score_pool", _boom)
    try:
        response = service.recommend(body(criteria, "err-1", "err"))
        trace, failed = _failed(service, "err")
    finally:
        service.close()
    assert response.status == "error" and response.error.code == "INTERNAL_TOOL_ERROR"
    assert trace["error"]["stage"] == expected and trace["error"]["code"] == "INTERNAL_TOOL_ERROR"
    assert [s["stage"] for s in failed] == [expected] and failed[0]["error"]["code"] == "INTERNAL_TOOL_ERROR"
    assert failed[0]["call"].startswith(f"{trace['trace_id']}/tools/")
    before = [s["stage"] for s in trace["stages"]][:-1]
    assert all(s["status"] == "ok" for s in trace["stages"][:-1]) and "input_validation" in before


@pytest.mark.parametrize("patch,expected", [
    ("evaluate_cold_start", "cold_start_diagnostics"),
    ("stratified_sample", "price_sampling"),
    ("_notes", "final_selection"),
])
def test_errors_locate_the_rank_sub_stage(tmp_path, monkeypatch, patch, expected):
    owner = exploration if patch == "stratified_sample" else selection
    monkeypatch.setattr(owner, patch, _boom)
    service = synthetic_service(tmp_path, plan_rows(3, 30, 40))
    try:
        response = service.recommend(make_request(PLAN, "rk-1", "rk"))
        trace, failed = _failed(service, "rk")
    finally:
        service.close()
    assert response.status == "error"
    assert trace["error"]["stage"] == expected
    assert [s["stage"] for s in failed] == [expected]
    # Code-side selection after the agent loop (call = the ranking artifact): its earlier sub-stages
    # finished (ok); no record pretends the later ones ran. Scoring finished inside the rank tool.
    assert failed[0]["call"] == next(s for s in trace["steps"] if s["kind"] == "tool"
                                     and s["result"]["tool"] == "rank_candidates")["result"]["ranking_artifact_id"]
    code = [s for s in trace["stages"] if s.get("call") == failed[0]["call"]]
    order = ["cold_start_diagnostics", "price_sampling", "final_selection"]
    assert [s["stage"] for s in code] == order[:order.index(expected) + 1]
    assert all(s["status"] == "ok" and s["duration_ms"] >= 0 for s in code[:-1])
    assert stage(trace, "scoring")["outputs"]["scored"] == 40 and stage(trace, "scoring")["status"] == "ok"


def test_errors_locate_input_validation_final_gate_and_model(tmp_path):
    service = fixture_service(tmp_path)
    try:
        stale = service.recommend(body([BUDGET], "iv-1", "iv", previous="no-such-state"))
        trace, failed = _failed(service, "iv")
        assert stale.error.code == "STALE_REFERENCE"
        assert trace["error"]["stage"] == "input_validation" and [s["stage"] for s in failed] == ["input_validation"]
        assert failed[0]["error"]["code"] == "STALE_REFERENCE" and failed[0]["duration_ms"] >= 0
    finally:
        service.close()

    class WrongCompletion(ScriptedController):
        def propose(self, dispatcher, context, trace):
            super().propose(dispatcher, context, trace)
            return Completion(ranking_artifact_id=f"{dispatcher.ref}/ranking/invented")

    service = fixture_service(tmp_path / "gate", controller=WrongCompletion())
    try:
        wrong = service.recommend(body([BUDGET], "fg-1", "fg"))
        trace, failed = _failed(service, "fg")
        assert wrong.error.code == "INVALID_SELECTION" and trace["error"]["stage"] == "final_gate"
        assert {s["stage"] for s in failed} == {"final_gate"}
        assert len(failed) == 1 and trace["repairs"][0]["outcome"] == "unrepairable"  # never sent back
    finally:
        service.close()

    class Outage(LiveController):
        def invoke(self, *args):
            raise RecommendationError("MODEL_UNAVAILABLE", "fixture outage", True)

    service = fixture_service(tmp_path / "model", controller=Outage())
    try:
        down = service.recommend(body([BUDGET], "md-1", "md"))
        trace, failed = _failed(service, "md")
        assert down.error.code == "MODEL_UNAVAILABLE" and trace["error"]["stage"] == "model"
        assert failed == [] and trace["steps"][0]["error"]["code"] == "MODEL_UNAVAILABLE"
    finally:
        service.close()


# --- evidence refs and app proofs -------------------------------------------------------------
def test_evidence_refs_are_complete_and_only_referenced_evidence_is_returned(tmp_path):
    service = fixture_service(tmp_path)
    try:
        response = service.recommend(body([BUDGET, SCHOOL, NEAR_SCHOOL], "ev-1", "ev"))
        trace = service.store.trace("ev")
    finally:
        service.close()
    assert response.status == "success"
    evidence = {(e.evidence_id, e.criterion_id): e for e in response.evidence}
    referenced = set()
    for item in response.recommendations:
        for result in item.criterion_results:
            for ref in result.evidence_refs:
                e = evidence[(ref, result.criterion_id)]
                assert e.listing_id == item.listing.listing_id
                referenced.add((ref, result.criterion_id))
            if result.criterion_id in ("school-distance", "nearer-school") and result.outcome != "unknown":
                assert result.evidence_refs
    assert referenced == set(evidence)
    gate = [s for s in trace["steps"] if s["kind"] == "final_gate"][-1]
    gate_refs = {(ref, r["criterion_id"]) for checks in gate["checks"].values() for r in checks
                 for ref in r["evidence_refs"]}
    assert gate_refs <= set(evidence)


def test_app_recommend_counts_and_proofs_are_unaffected_by_the_slim_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOMMENDATION_SERVICE_TOKEN", "test-only-credential")
    headers = {"x-recommendation-token": "test-only-credential"}
    request = {"request_id": "app-1", "conversation_id": "app", "requirements_version": 1,
               "requirements": json.loads((ROOT / "recommendation/examples/requirements.json").read_text())}
    views = {}
    for mode in ("summary", "debug"):
        monkeypatch.setenv("RECOMMENDATION_STATE_PATH", str(tmp_path / f"{mode}.sqlite"))
        monkeypatch.setenv("RECOMMENDATION_TRACE_MODE", mode)
        with TestClient(app) as client:
            views[mode] = client.post("/app/recommend", json=request, headers=headers).json()
        response = views[mode]["response"]
        assert response["status"] == "success"
        presentation = views[mode]["presentation"]
        pool = response["pool"]
        assert presentation["listings"] == [i["listing"] for i in response["recommendations"]]
        assert presentation["counts"] == {"assessed": pool["source_count"], "eligible": pool["size"],
                                          "unverified": pool["excluded_hard_unknown"],
                                          "excluded": pool["excluded_hard_fail"]}
        assert presentation["counts"]["assessed"] == sum(presentation["counts"][k]
                                                         for k in ("eligible", "unverified", "excluded"))
        store = StateStore(tmp_path / f"{mode}.sqlite")
        try:
            trace = store.trace("app")
            assert trace["layout"] == slim.LAYOUT and trace["trace_mode"] == mode
            assert stage(trace, "pool_build_or_load")["outputs"]["size"] == pool["size"]
            assert store.resolve_reference("app", response["state"]["candidate_pool_ref"])["partition"]["size"] == pool["size"]
        finally:
            store.close()
    # The trace mode changes the trace only, never the selection or the proofs.
    assert views["summary"]["presentation"] == views["debug"]["presentation"]
