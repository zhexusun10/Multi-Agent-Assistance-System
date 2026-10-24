"""Dedicated Python LangChain recommendation agent: configuration isolation, the model client (with a fake
LangChain chat model, never a provider), ranking-artifact handoff and invalidation, read_pool, evidence
coverage, and the code-controlled repair loop. Offline only: no model request leaves the process.
"""
import json
import subprocess

import pytest

from recommendation import app as app_module
from recommendation.agent import llm
from recommendation.agent.controller import Completion, LiveController, ScriptedController
from recommendation.contracts.errors import RecommendationError
from recommendation.models.repair import FailureReport
from recommendation.tools.registry import PIPELINE
from recommendation.validation import gate as gate_module

from .test_geo_tools import BUDGET, CONV, HUB, INJECTION, PLACES, body, call, dispatcher, geo, service, settings

ENV = {"RECOMMENDATION_LLM_MODEL": "gpt-test", "RECOMMENDATION_LLM_API_KEY": "sk-test-secret-value"}


def run_pipeline(d):
    for name in PIPELINE:
        result = call(d, name)
        assert result.error is None, result.error
    return d


# --- configuration isolation -----------------------------------------------------------------
def test_settings_come_only_from_the_recommendation_namespace():
    outer = {"CHAT_MODEL": "openai:gpt-4.1-mini", "OPENAI_API_KEY": "sk-outer", "OPENAI_BASE_URL": "https://x.example"}
    with pytest.raises(RecommendationError) as missing:
        llm.LlmSettings.from_env(outer)  # never falls back to the application's model or key
    assert missing.value.detail.code == "MODEL_NOT_CONFIGURED" and "RECOMMENDATION_LLM_MODEL" in missing.value.detail.message
    with pytest.raises(RecommendationError, match="RECOMMENDATION_LLM_API_KEY"):
        llm.LlmSettings.from_env({"RECOMMENDATION_LLM_MODEL": "gpt-test"})
    s = llm.LlmSettings.from_env(ENV)
    assert (s.provider, s.model, s.base_url, s.temperature, s.max_tokens, s.timeout_seconds, s.max_retries) == \
        ("openai", "gpt-test", None, 0.0, None, 60.0, 0)
    assert s.identifier == "openai:gpt-test" and llm.configured_identifier(outer) is None
    public = s.public()
    assert "sk-test-secret-value" not in json.dumps(public) and "api_key" not in public
    full = llm.LlmSettings.from_env({**ENV, "RECOMMENDATION_LLM_BASE_URL": "https://api.deepseek.com/v1",
                                     "RECOMMENDATION_LLM_TEMPERATURE": "0.3", "RECOMMENDATION_LLM_MAX_TOKENS": "512",
                                     "RECOMMENDATION_LLM_TIMEOUT_SECONDS": "15", "RECOMMENDATION_LLM_MAX_RETRIES": "1"})
    assert full.public() == {"provider": "openai", "model": "gpt-test", "base_url_host": "api.deepseek.com",
                             "temperature": 0.3, "max_tokens": 512, "timeout_seconds": 15.0, "max_retries": 1}


@pytest.mark.parametrize("key,value", [("PROVIDER", "anthropic"), ("TEMPERATURE", "warm"), ("TEMPERATURE", "-1"),
                                       ("MAX_TOKENS", "0"), ("TIMEOUT_SECONDS", "inf"), ("MAX_RETRIES", "-1"),
                                       ("BASE_URL", "ftp://x")])
def test_invalid_settings_name_the_variable_not_the_value(key, value):
    with pytest.raises(RecommendationError) as bad:
        llm.LlmSettings.from_env({**ENV, f"RECOMMENDATION_LLM_{key}": value})
    assert bad.value.detail.code == "MODEL_NOT_CONFIGURED" and f"RECOMMENDATION_LLM_{key}" in bad.value.detail.message
    assert value not in bad.value.detail.message or value in ("0", "-1")


def test_app_uses_the_python_agent():
    assert app_module.LiveController is LiveController
    source = open(app_module.__file__).read()
    assert "ApplicationLiveController" not in source and "model_bridge" not in source


def test_model_settings_load_as_one_group_and_blank_values_never_overwrite(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("RECOMMENDATION_LLM_MODEL=gpt-from-file\nRECOMMENDATION_LLM_API_KEY=sk-from-file\n"
                        "RECOMMENDATION_LLM_BASE_URL=\n")
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("RECOMMENDATION_LLM_BASE_URL", raising=False)
    app_module.load_project_environment(env_file)  # nothing from the caller: all from the file
    assert (llm.LlmSettings.from_env().model, llm.LlmSettings.from_env().api_key) == ("gpt-from-file", "sk-from-file")
    assert "RECOMMENDATION_LLM_BASE_URL" not in __import__("os").environ  # the blank line set nothing
    # The caller set the group: the file's key is never paired with the caller's endpoint.
    monkeypatch.setenv("RECOMMENDATION_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("RECOMMENDATION_LLM_API_KEY", "sk-caller")
    monkeypatch.setenv("RECOMMENDATION_LLM_BASE_URL", "https://api.deepseek.com")
    app_module.load_project_environment(env_file)
    s = llm.LlmSettings.from_env()
    assert (s.model, s.api_key, s.base_url) == ("deepseek-chat", "sk-caller", "https://api.deepseek.com")


def test_the_client_never_reads_the_applications_openai_variables(monkeypatch):
    import os

    outer = {"OPENAI_BASE_URL": "http://outer.invalid/v1", "OPENAI_API_BASE": "http://outer2.invalid/v1",
             "OPENAI_ORG_ID": "org-outer", "OPENAI_PROJECT_ID": "proj-outer", "OPENAI_API_KEY": "sk-outer"}
    for key, value in outer.items():
        monkeypatch.setenv(key, value)
    model = llm.chat_model(llm.LlmSettings.from_env(ENV), 5)  # construction only, no request
    client = model.root_client
    assert str(client.base_url).rstrip("/") == llm.DEFAULT_BASE_URL and client.organization is None
    assert client.project is None and client.api_key == "sk-test-secret-value"
    assert all(os.environ[k] == v for k, v in outer.items())  # restored after construction
    deepseek = llm.chat_model(llm.LlmSettings.from_env({**ENV, "RECOMMENDATION_LLM_BASE_URL": "https://api.deepseek.com"}), 5)
    assert deepseek.extra_body == {"thinking": {"type": "disabled"}}


def test_settings_never_print_the_key_and_retries_share_the_remaining_time():
    s = llm.LlmSettings.from_env({**ENV, "RECOMMENDATION_LLM_MAX_RETRIES": "2"})
    assert "sk-test-secret-value" not in repr(s) and "sk-test-secret-value" not in str(s)
    assert s.attempt_timeout(9) == 3 and s.attempt_timeout(600) == 60  # (retries + 1) attempts fit in the run
    assert llm.LlmSettings.from_env(ENV).attempt_timeout(5) == 5


def test_unconfigured_live_agent_is_a_structured_error_without_a_model_call(tmp_path, monkeypatch):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-outer")  # present, but never used by the recommendation agent
    svc = service(tmp_path, LiveController())
    try:
        response = svc.recommend(body([BUDGET]))
        assert response.status == "error" and response.error.code == "MODEL_NOT_CONFIGURED"
        assert response.execution.model_calls == 0 and response.execution.model_identifier is None
    finally:
        svc.close()


# --- the model client (fake LangChain chat model; no provider, no subprocess) --------------------
class FakeChat:
    """Stands in for ChatOpenAI: records bind_tools / invoke, answers from a script."""

    def __init__(self, script):
        self.script = script
        self.bound = None
        self.received = []

    def bind_tools(self, tools):
        self.bound = tools
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage

        self.received.append(messages)
        content, calls = self.script(len(self.received), messages)
        return AIMessage(content=content, tool_calls=calls,
                         usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})


def test_live_agent_runs_through_python_langchain_with_no_subprocess(tmp_path, monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    made = []

    def script(turn, messages):
        ref = json.loads(messages[1].content)["context_ref"]
        if turn == 1:
            return "", [{"name": n, "args": {"context_ref": ref}, "id": f"c{i}", "type": "tool_call"}
                        for i, n in enumerate(PIPELINE)]
        artifact = json.loads(messages[-1].content)["ranking_artifact_id"]
        return json.dumps({"ranking_artifact_id": artifact}), []

    fake = FakeChat(script)

    def chat_model(settings, timeout):
        made.append((settings, timeout))
        return fake

    monkeypatch.setattr(llm, "chat_model", chat_model)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("no subprocess may run a model call"))
    svc = service(tmp_path, LiveController())
    try:
        response = svc.recommend(body([BUDGET]))
        assert response.status == "success", response.error
        assert response.execution.model_identifier == "openai:gpt-test" and response.execution.controller_mode == "live"
        assert response.execution.model_calls == 2 and response.execution.tokens == 20
        settings_used, timeout = made[0]
        assert settings_used.max_retries == 0 and 0 < timeout <= 60
        assert [t["function"]["name"] for t in fake.bound] == [t["name"] for t in __import__(
            "recommendation.tools.registry", fromlist=["model_tools"]).model_tools()]
        kinds = [type(m).__name__ for m in fake.received[1]]
        assert kinds[:2] == ["SystemMessage", "HumanMessage"] and "AIMessage" in kinds and "ToolMessage" in kinds
        trace = svc.store.trace(CONV)
        assert trace["agent"]["protocol"] == "recommendation-agent-v1" and trace["agent"]["model"]["model"] == "gpt-test"
        assert "sk-test-secret-value" not in json.dumps(trace)
        assert "sk-test-secret-value" not in json.dumps(response.model_dump(mode="json"))
    finally:
        svc.close()


def test_invalid_tool_calls_are_reported_and_corrected_not_dropped(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage

    class Malformed:
        turns = 0

        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            self.turns += 1
            ref = json.loads(messages[1].content)["context_ref"]
            if self.turns == 1:
                return AIMessage(content="", invalid_tool_calls=[
                    {"name": "resolve_entities", "args": '{"context_ref": ', "id": "bad", "error": "bad json",
                     "type": "invalid_tool_call"}])
            if self.turns == 2:
                self.corrected = "Return valid tool calls" in messages[-1].content  # told what was wrong
                return AIMessage(content="", tool_calls=[{"name": n, "args": {"context_ref": ref}, "id": f"c{i}",
                                                          "type": "tool_call"} for i, n in enumerate(PIPELINE)])
            artifact = json.loads(messages[-1].content)["ranking_artifact_id"]
            return AIMessage(content=json.dumps({"ranking_artifact_id": artifact}))

    fake = Malformed()
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(llm, "chat_model", lambda settings, timeout: fake)
    svc = service(tmp_path, LiveController())
    try:
        response = svc.recommend(body([BUDGET]))
        assert response.status == "success", response.error
        assert response.execution.model_calls == 3
        first = next(s for s in svc.store.trace(CONV)["steps"] if s["kind"] == "model")
        assert first["output"]["invalid_tool_calls"][0]["name"] == "resolve_entities" and fake.corrected
    finally:
        svc.close()


def test_provider_failure_is_reported_without_the_provider_text(monkeypatch):
    class Boom:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            raise RuntimeError("401 for key sk-test-secret-value at https://user:pw@x")

    monkeypatch.setattr(llm, "chat_model", lambda settings, timeout: Boom())
    with pytest.raises(RecommendationError) as failed:
        llm.invoke(llm.LlmSettings.from_env(ENV), [{"role": "system", "content": "s"}], [], 5)
    assert failed.value.detail.code == "MODEL_UNAVAILABLE" and "sk-" not in failed.value.detail.message


# --- ranking artifact: handoff, invalidation, evidence coverage ---------------------------------
def test_ranking_artifact_is_current_code_managed_and_invalidated_by_upstream_changes():
    d = run_pipeline(dispatcher([BUDGET]))
    first = d.artifact
    assert first.artifact_id.startswith(f"{d.ref}/ranking/") and d.selection is None
    assert first.eligible == len(d.ranked) == len(d.pool)
    coverage = first.coverage
    assert coverage["complete"] and coverage["pool"] == len(d.pool)
    [budget] = coverage["criteria"]
    assert budget["known"] == len(d.pool) and budget["missing"] == budget["ambiguous"] == 0
    # Identical inputs give the identical artifact digest; rerunning a stage discards it.
    again = call(d, "rank_candidates")
    assert again.ranking_artifact_id == first.artifact_id
    call(d, "get_geographic_evidence")
    assert d.artifact is None and call(d, "read_pool", pool_version=d.pool_result.version).error is None
    with pytest.raises(RecommendationError) as stale:
        d.select_ranking(first.artifact_id)
    assert stale.value.detail.code == "INVALID_SELECTION"
    call(d, "rank_candidates")
    selected = d.select_ranking(d.artifact.artifact_id)
    assert selected == d.selected and d.selection_input["algorithm_version"] == "select-v2"


def test_bind_entity_invalidates_the_artifact_and_unknown_hard_evidence_never_passes():
    d = dispatcher([BUDGET, geo(value=20000, destination_name="Holland Hub")], places=HUB)
    run_pipeline(d)
    # The ambiguous hard target leaves every listing unknown: none eligible, an empty (valid) artifact.
    assert d.artifact is not None and d.artifact.eligible == 0 and d.ranked == []
    call(d, "search_places", query="Holland Hub", category="Mall", criterion_id="geo")
    bound = call(d, "bind_entity", criterion_id="geo", entity_id="OSM:node:7")
    assert bound.status == "ok" and d.artifact is None and bound.ranking_artifact_id is None


def test_empty_ranking_is_a_completed_no_match(tmp_path):
    svc = service(tmp_path)
    try:
        response = svc.recommend(body([dict(BUDGET, value=1)]))
        assert response.status == "no_match" and response.recommendations == []
        assert response.selection.mode == "empty"
    finally:
        svc.close()


def test_scores_are_unchanged_for_identical_evidence():
    # Two independent runs over the same evidence: identical full-pool order and scores (selection seeds
    # may differ between conversations; scoring never does).
    a, b = run_pipeline(dispatcher([BUDGET])), run_pipeline(dispatcher([BUDGET]))
    assert a.ranked == b.ranked and a.scored.fused.combined.tolist() == b.scored.fused.combined.tolist()
    assert a.artifact.artifact_id == b.artifact.artifact_id


# --- read_pool ------------------------------------------------------------------------------
def test_read_pool_pages_totals_fields_and_listing_selection():
    d = dispatcher([BUDGET])
    call(d, "resolve_entities")
    assert call(d, "read_pool", pool_version="x").error.code == "STALE_REFERENCE"  # needs build_pool first
    call(d, "build_pool")
    version = d.pool_result.version
    page = call(d, "read_pool", pool_version=version, limit=3)
    data = page.data
    assert page.error is None and data["order"] == "listing_id" and data["total"] == len(d.pool)
    assert data["returned"] == 3 and data["has_more"] and data["next_offset"] == 3 and data["offset"] == 0
    assert data["pool"] == d.pool_result.reference() and data["data_version"] == d.pool_result.data_version
    assert [r["listing_id"] for r in data["records"]] == [r.listing_id for r in d.pool][:3]
    assert page.candidate_ids == [] and page.ranked_ids == [] and page.previews == []  # lean envelope
    record = data["records"][0]
    assert set(record) >= {"listing_id", "facts", "criteria", "ranking_fields"}
    assert record["criteria"][0]["criterion_id"] == "budget" and record["criteria"][0]["status"] == "known"
    # Paging through the whole pool returns every listing exactly once.
    seen, offset = [], 0
    while offset is not None:
        chunk = call(d, "read_pool", pool_version=version, offset=offset, limit=4).data
        seen += [r["listing_id"] for r in chunk["records"]]
        offset = chunk["next_offset"]
    assert seen == [r.listing_id for r in d.pool]
    past = call(d, "read_pool", pool_version=version, offset=10_000).data
    assert past["returned"] == 0 and not past["has_more"]
    narrow = call(d, "read_pool", pool_version=version, fields=["price"], listing_ids=[d.pool[1].listing_id]).data
    assert narrow["total"] == 1 and narrow["records"][0]["facts"] == {"price": d.pool[1].price}
    # Page size has a schema ceiling (model context), not a tunable.
    from recommendation.tools.arguments import MAX_PAGE_ROWS

    assert call(d, "read_pool", pool_version=version, limit=MAX_PAGE_ROWS + 1).error.code == "INVALID_ARGUMENT"


def test_read_pool_rejects_stale_cross_run_and_out_of_pool_references():
    d = dispatcher([BUDGET])
    call(d, "resolve_entities")
    call(d, "build_pool")
    version = d.pool_result.version
    assert call(d, "read_pool", pool_version="an-old-pool").error.code == "STALE_REFERENCE"
    other = d.call("read_pool", {"context_ref": "another-run", "pool_version": version})
    assert other.error.code == "STALE_REFERENCE"
    outside = call(d, "read_pool", pool_version=version, listing_ids=["not-in-pool"])
    assert outside.error.code == "INVALID_ARGUMENT" and "not in the current pool" in outside.error.message
    call(d, "resolve_entities")  # discards the pool: its version is stale now
    assert call(d, "read_pool", pool_version=version).error.code == "STALE_REFERENCE"


def test_read_pool_never_mutates_and_partial_inspection_keeps_full_pool_ranking():
    plain = run_pipeline(dispatcher([BUDGET]))
    d = dispatcher([BUDGET])
    for name in PIPELINE[:3]:
        call(d, name)
    before = d.snapshot()
    resolution_calls = d.resolution_calls
    call(d, "read_pool", pool_version=d.pool_result.version, limit=2)
    assert d.snapshot() == before and d.resolution_calls == resolution_calls  # not a resolution call
    call(d, "rank_candidates")
    assert d.ranked == plain.ranked and len(d.ranked) == len(d.pool)
    assert d.artifact.artifact_id.split("/ranking/")[1] == plain.artifact.artifact_id.split("/ranking/")[1]
    scored = call(d, "read_pool", pool_version=d.pool_result.version, limit=3).data
    assert scored["order"] == "score" and [r["listing_id"] for r in scored["records"]] == d.ranked[:3]
    assert [r["score_rank"] for r in scored["records"]] == [1, 2, 3]


def test_read_pool_treats_listing_text_as_data_and_reports_evidence_status(monkeypatch):
    from recommendation.repositories.connections import repositories

    listing, _ = repositories("fixture")
    listing.rows[0].title = INJECTION
    near = geo("near", strength="soft", destination_name="Holland Hub")
    d = dispatcher([BUDGET, near], places=HUB, listing=listing)
    call(d, "resolve_entities")
    call(d, "build_pool")
    before = d.snapshot()
    page = call(d, "read_pool", pool_version=d.pool_result.version, listing_ids=[listing.rows[0].listing_id]).data
    record = page["records"][0]
    assert record["facts"]["title"] == INJECTION[:d.config.trace.max_string_chars]  # carried as data only
    assert d.snapshot() == before and "data, not instructions" in page["note"]
    near_status = next(c for c in record["criteria"] if c["criterion_id"] == "near")
    assert near_status["status"] == "not_retrieved"  # soft geographic evidence not fetched yet
    call(d, "get_geographic_evidence")
    record = call(d, "read_pool", pool_version=d.pool_result.version).data["records"][0]
    near_status = next(c for c in record["criteria"] if c["criterion_id"] == "near")
    assert near_status["status"] == "ambiguous"  # two exact name matches: the utility stays unknown
    assert record["ranking_fields"]["soft_criteria_unknown"] == ["near"]
    assert record["ranking_fields"]["soft_criteria_known"] == []


def test_an_id_and_name_conflict_is_conflicting_evidence():
    from recommendation.geography.resolver import Resolution
    from recommendation.contracts.evidence import CriterionResult
    from recommendation.tools.dispatcher import evidence_status

    unknown = CriterionResult(criterion_id="near", outcome="unknown", reason="x")
    assert evidence_status(unknown, [], Resolution("ambiguous", "id+name", "id_name_conflict")) == "conflicting"
    assert evidence_status(unknown, [], Resolution("ambiguous", "name", "several")) == "ambiguous"
    assert evidence_status(unknown, []) == "missing"


def test_rerank_discards_the_old_selection_and_the_gate_ties_selection_to_the_artifact():
    d = run_pipeline(dispatcher([BUDGET]))
    d.select_ranking(d.artifact.artifact_id)
    call(d, "rank_candidates")  # a new ranking: the old code-side selection no longer applies
    assert d.selection is None and d.selected is None and d.selection_artifact is None
    d.select_ranking(d.artifact.artifact_id)
    d.selection_artifact = "an-older-artifact"
    with pytest.raises(RecommendationError) as stale:
        gate_module.final_gate(d, {"steps": []})
    assert stale.value.detail.code == "INVALID_SELECTION"


def test_selection_step_resolves_from_the_artifact_reference(tmp_path):
    svc = service(tmp_path)
    try:
        svc.recommend(body([BUDGET]))
        trace = svc.store.trace(CONV)
        artifact = next(s for s in trace["steps"] if s["kind"] == "selection")["ranking_artifact_id"]
        resolved = svc.store.resolve_reference(CONV, artifact)
        assert resolved["kind"] == "selection" and resolved["selected_ids"]
        assert stage_calls(trace) == {artifact}
    finally:
        svc.close()


def stage_calls(trace):
    return {s["call"] for s in trace["stages"] if s["stage"] in ("cold_start_diagnostics", "price_sampling",
                                                                "final_selection")}


# --- repair loop ----------------------------------------------------------------------------
class Repairing(LiveController):
    """Fake model: runs the pipeline on the first turn and after every repair report, else finishes."""

    def __init__(self):
        super().__init__()
        self.reports = []

    def invoke(self, messages, tools, remaining):
        ref = json.loads(messages[1]["content"])["context_ref"]
        last = messages[-1]
        if last["role"] == "user" and '"repair"' in last["content"]:
            self.reports.append(json.loads(last["content"])["repair"])
        if last["role"] == "user":
            return {"content": "", "tool_calls": [{"id": f"p{len(messages)}-{i}", "name": n, "args": {"context_ref": ref}}
                                                  for i, n in enumerate(PIPELINE)]}
        artifact = [json.loads(m["content"]) for m in messages if m["role"] == "tool"][-1]["ranking_artifact_id"]
        return {"content": json.dumps({"ranking_artifact_id": artifact}), "tool_calls": []}


def change_data_once(svc):
    original, state = svc.listing.lookup_versioned, {"calls": 0}

    def change(ids):
        state["calls"] += 1
        if state["calls"] == 1:
            svc.listing.rows[0].title = "changed"
        return original(ids)

    svc.listing.lookup_versioned = change
    return state


def test_repairable_failure_returns_a_structured_report_to_the_agent_and_succeeds(tmp_path):
    model = Repairing()
    svc = service(tmp_path, model)
    change_data_once(svc)
    try:
        response = svc.recommend(body([BUDGET]))
        assert response.status == "success", response.error
        [report] = model.reports
        FailureReport.model_validate(report)
        assert report["code"] == "STALE_EVIDENCE" and report["action"] == "refresh_and_recompute"
        assert report["resolvable_by"] == "recomputation" and "build_pool" in report["invalidated"]
        assert report["references"]["ranking_artifact_id"] and report["references"]["pool_version"]
        trace = svc.store.trace(CONV)
        assert [r["outcome"] for r in trace["repairs"]] == ["repaired"]
        gates = [s["passed"] for s in trace["steps"] if s["kind"] == "final_gate"]
        assert gates == [False, True]
    finally:
        svc.close()


def change_gate_evidence_once(monkeypatch):
    """The first final gate sees changed geographic evidence (only inside the gate, once)."""
    import recommendation.service as service_module
    from recommendation.tools import pool_builder

    original, gate, state = pool_builder.normalize_evidence, service_module.final_gate, {"in_gate": False, "done": False}

    def changed(evidence, *args, **kwargs):
        out = original(evidence, *args, **kwargs)
        if out and state["in_gate"] and not state["done"]:
            state["done"] = True
            out[0] = out[0].model_copy(update={"snapshot_hash": "changed"})
        return out

    def observed_gate(dispatcher, trace):
        state["in_gate"] = True
        try:
            return gate(dispatcher, trace)
        finally:
            state["in_gate"] = False

    monkeypatch.setattr(pool_builder, "normalize_evidence", changed)
    monkeypatch.setattr(service_module, "final_gate", observed_gate)
    return state


@pytest.mark.parametrize("strength,action,invalidated", [
    ("soft", "targeted_retrieval", "get_geographic_evidence"),
    ("hard", "refresh_and_recompute", "build_pool"),
])
def test_stale_geographic_evidence_is_repaired_by_the_right_recovery(tmp_path, monkeypatch, strength, action, invalidated):
    criterion = geo("near", strength=strength, destination_name="Fixture School", value=50000)
    model = Repairing()
    svc = service(tmp_path, model, places=PLACES)
    try:
        change_gate_evidence_once(monkeypatch)
        response = svc.recommend(body([BUDGET, criterion]))
        assert response.status == "success", response.error
        [report] = model.reports
        assert report["code"] == "STALE_EVIDENCE" and report["action"] == action
        assert invalidated in report["invalidated"] and report["criterion_ids"] == ["near"]
        assert [r["outcome"] for r in svc.store.trace(CONV)["repairs"]] == ["repaired"]
    finally:
        svc.close()


def test_a_failed_repair_turn_is_recorded(tmp_path, monkeypatch):
    class GivesUp(Repairing):
        def invoke(self, messages, tools, remaining):
            if messages[-1]["role"] == "user" and '"repair"' in messages[-1]["content"]:
                raise RecommendationError("MODEL_UNAVAILABLE", "fixture outage", True)
            return super().invoke(messages, tools, remaining)

    svc = service(tmp_path, GivesUp())
    change_data_once(svc)
    try:
        response = svc.recommend(body([BUDGET]))
        assert response.status == "error" and response.error.code == "MODEL_UNAVAILABLE"
        assert [r["outcome"] for r in svc.store.trace(CONV)["repairs"]] == ["agent_failed: MODEL_UNAVAILABLE"]
    finally:
        svc.close()


def test_repairs_are_bounded(tmp_path):
    svc = service(tmp_path, Repairing(), config=settings(**{"controller.repairs": 0}))
    change_data_once(svc)
    try:
        response = svc.recommend(body([BUDGET]))
        assert response.status == "error" and response.error.code == "STALE_EVIDENCE"
        assert [r["outcome"] for r in svc.store.trace(CONV)["repairs"]] == ["exhausted"]
        assert svc.store.current(CONV) is None
    finally:
        svc.close()


def test_repeated_failure_without_new_evidence_stops(tmp_path, monkeypatch):
    original = gate_module.final_gate

    def always_hard_fail(dispatcher, trace):
        original(dispatcher, trace)
        raise gate_module.GateFailure(
            "HARD_CONSTRAINT_FAILED", "Final hard constraints did not pass", True, action="refilter_and_rerank",
            resolvable_by="recomputation", listing_ids=[dispatcher.selected[0]], criterion_ids=["budget"],
            invalidate=gate_module.ALL_STAGES)

    import recommendation.service as service_module

    monkeypatch.setattr(service_module, "final_gate", always_hard_fail)
    model = Repairing()
    svc = service(tmp_path, model, config=settings(**{"controller.repairs": 2}))
    try:
        response = svc.recommend(body([BUDGET]))
        assert response.status == "error" and response.error.code == "REPAIR_NO_PROGRESS"
        outcomes = [r["outcome"] for r in svc.store.trace(CONV)["repairs"]]
        assert outcomes == ["returned_to_agent", "no_progress"]
        assert model.reports[0]["code"] == "HARD_CONSTRAINT_FAILED" and model.reports[0]["action"] == "refilter_and_rerank"
    finally:
        svc.close()


def test_internal_selection_inconsistency_is_never_sent_back(tmp_path, monkeypatch):
    from recommendation.tools.dispatcher import Dispatcher

    original = Dispatcher.select_ranking

    def corrupt(self, artifact_id):
        original(self, artifact_id)
        self.selected = self.selected[1:]
        return self.selected

    monkeypatch.setattr(Dispatcher, "select_ranking", corrupt)
    model = Repairing()
    svc = service(tmp_path, model)
    try:
        response = svc.recommend(body([BUDGET]))
        assert response.status == "error" and response.error.code == "INVALID_SELECTION"
        assert model.reports == [] and svc.store.trace(CONV)["repairs"][0]["outcome"] == "unrepairable"
    finally:
        svc.close()


def test_scripted_controller_finishes_with_the_current_artifact():
    d = dispatcher([BUDGET])
    completion = ScriptedController().propose(d, {"context_ref": d.ref}, {"steps": []})
    assert completion == Completion(ranking_artifact_id=d.artifact.artifact_id)
    assert [s["result"]["tool"] for s in d.trace["steps"] if s["kind"] == "tool"] == list(PIPELINE)


def test_operational_errors_are_not_no_match(tmp_path):
    svc = service(tmp_path)

    def outage(*_a, **_k):
        raise RecommendationError("DATA_UNAVAILABLE", "test outage")

    svc.listing.retrieve_versioned = outage
    svc.listing.retrieve = outage
    try:
        response = svc.recommend(body([BUDGET], "outage"))
        assert response.status == "error" and response.error.code == "DATA_UNAVAILABLE"
    finally:
        svc.close()
