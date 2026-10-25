"""Native request/response validation driven by the shared fixtures in tests/fixtures/recommendation-v2/
(the TS side reads the same files in tests/recommendation-v2-fixtures.test.ts). Request = main contract 1.0
(feedback events + behavior_context) + cold_start; response = contract 2.0 (merge with main c7dc536)."""
import json, os, subprocess, sys, textwrap
from pathlib import Path
import pytest
from pydantic import ValidationError
from recommendation.contracts.request import RecommendationRequest
from recommendation.contracts.behavior import BehaviorContext, request_uuid
from recommendation.repositories.connections import repositories
from recommendation.service import RecommendationService
from recommendation.state.store import StateStore
from recommendation.agent.controller import ScriptedController
# The app keeps caller-set RECOMMENDATION_* selections over the project .env, and conftest.py
# isolates every test (fixture data, scripted controller, private state, live state refused).
import recommendation.app as app_module
from .isolation import subprocess_env

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/recommendation-v2"


def load(name):
    return json.loads((FIXTURES / name).read_text())


CONTRACT, FLOW = load("contract-cases.json"), load("flow.json")
REQUIREMENTS = CONTRACT["setup_requirements"]


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    """recommendation.app.execute (the /app/recommend path) on a private state file."""
    path = tmp_path / "state.sqlite"
    monkeypatch.setenv("RECOMMENDATION_STATE_PATH", str(path))
    monkeypatch.setenv("RECOMMENDATION_DATA_MODE", "fixture")
    monkeypatch.setenv("RECOMMENDATION_CONTROLLER", "scripted")
    monkeypatch.delenv("RECOMMENDATION_CONFIG", raising=False)
    monkeypatch.delenv("RECOMMENDATION_TRACE_MODE", raising=False)
    assert app_module.state_path() == str(path)
    return path


def post(body):
    assert os.environ["RECOMMENDATION_DATA_MODE"] == "fixture" and os.environ["RECOMMENDATION_CONTROLLER"] == "scripted"
    return app_module.execute(RecommendationRequest.model_validate(body), app_view=True)


def initial(conversation, request_id):
    return {"contract_version": "1.0", "request_id": request_id, "conversation_id": conversation,
            "requirements_version": 1, "requirements": REQUIREMENTS}


def current(path, conversation):
    store = StateStore(path)
    try:
        return store.current(conversation)
    finally:
        store.close()


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda c: c["name"])
def test_contract_cases_schema(case):
    if case["valid"]:
        RecommendationRequest.model_validate(case["body"])
    else:
        with pytest.raises(ValidationError) as raised:
            RecommendationRequest.model_validate(case["body"])
        assert case["error"] in str(raised.value), str(raised.value)


def test_unversioned_body_is_read_as_main_contract_but_2_0_request_is_rejected():
    body = next(c["body"] for c in CONTRACT["cases"] if c["name"] == "contract-version-omitted")
    assert "contract_version" not in body
    assert RecommendationRequest.model_validate(body).contract_version == "1.0"
    with pytest.raises(ValidationError, match="UNSUPPORTED_VERSION"):
        RecommendationRequest.model_validate({**body, "contract_version": "2.0"})


def scoreable_browsing(context):
    return sorted(v["listing_id"] for v in (context or {}).get("listing_view_durations", [])
                  if v["interest_eligible"] and v["duration_seconds"] > 0)


@pytest.mark.parametrize("case", [c for c in CONTRACT["cases"] if c["valid"]], ids=lambda c: c["name"])
def test_contract_cases_at_service_level(app_env, case):
    """Every schema-valid case after round v2-r1: success (cumulative browsing, 0 s, not interest_eligible,
    empty behaviour, viewing drafts, out-of-pool references, feedback events, result_limit 10/None, cold
    start) or the declared service error."""
    assert post(initial("v2-fixture", "v2-r1"))["response"]["status"] == "success"
    assert post(initial("v2-other", "v2-other-r1"))["response"]["status"] == "success"
    response = post(case["body"])["response"]
    if "service_error" in case:
        assert response["status"] == "error" and response["error"]["code"] == case["service_error"]
        assert current(app_env, "v2-fixture")["request"]["request_id"] == "v2-r1"  # nothing committed
        return
    assert response["status"] == "success", response.get("error")
    assert len(response["recommendations"]) == min(10, response["pool"]["size"]) == 9
    profile = current(app_env, "v2-fixture")["profile"]
    context = case["body"].get("behavior_context")
    # Only interest_eligible, positive cumulative durations become browsing weights.
    assert sorted(profile["interest_weights"]) == scoreable_browsing(context)
    assert profile["appointment_listing_ids"] == sorted(d["listing_id"] for d in (context or {}).get("book_viewing_listings", []))
    # Feedback events are validated, never scored (no double counting with behavior_context).
    assert profile["feedback_events"] == {"count": len(case["body"].get("feedback", [])),
                                          "sources": sorted({e["source"] for e in case["body"].get("feedback", [])}),
                                          "scored": False}
    pool = {item["listing"]["listing_id"] for item in response["recommendations"]}
    if not set(scoreable_browsing(context)) & pool:
        assert all(item["scores"]["browsing_score"] is None for item in response["recommendations"])
    drafts = {d["listing_id"] for d in (context or {}).get("book_viewing_listings", [])} & pool
    for item in response["recommendations"]:
        others = drafts  # identity references are included
        assert (item["scores"]["appointment_score"] is None) == (not others)


def test_three_rounds_no_loss_no_accumulation_appointments_only_via_input(app_env):
    """Flow fixture: round 2 scores the cumulative behaviour summary and a saved draft; round 3 sends
    summaries with the interest gate closed and no draft and is scored exactly like round 1 (no native
    accumulation, no native appointment lookup)."""
    responses = []
    for round_ in FLOW["rounds"]:
        envelope = post(round_["native_body"])
        response, expect = envelope["response"], round_["expect"]
        assert response["status"] == expect["status"], response.get("error")
        # app.py rewrites previous_state_id to the native v2 head (the TS sends its own copy).
        head = responses[-1]["state"]["state_id"] if responses else None
        assert envelope["request"]["previous_state_id"] == head
        assert envelope["transport_request"]["previous_state_id"] == round_["native_body"]["previous_state_id"]
        canonical = lambda c: BehaviorContext.model_validate(c).model_dump(mode="json") if c else None
        assert canonical(envelope["transport_request"].get("behavior_context")) == canonical(round_["native_body"].get("behavior_context"))
        state = current(app_env, FLOW["conversation_id"])
        profile = state["profile"]
        assert sorted(profile["interest_weights"]) == sorted(expect["browsing_weighted"])
        assert profile["appointment_listing_ids"] == expect["appointment_listing_ids"]
        sent = round_["native_body"].get("behavior_context")
        assert profile["browsing"] == ({v["listing_id"]: {"duration_seconds": v["duration_seconds"], "view_count": v["view_count"],
                                                          "interest_eligible": v["interest_eligible"]}
                                        for v in sent["listing_view_durations"]} if sent else {})
        assert state["sequence"] == len(responses) + 1
        responses.append(response)
    second, third = responses[1], responses[2]
    assert any(item["scores"]["behavior_score"] is not None for item in second["recommendations"])
    assert all(item["scores"]["behavior_score"] is None for item in third["recommendations"])
    assert [i["scores"]["combined_score"] for i in third["recommendations"]] == [
        i["scores"]["combined_score"] for i in responses[0]["recommendations"]]
    # Same request, same input: stored response; same request, different input (incl. behaviour): rejected.
    assert post(FLOW["rounds"][2]["native_body"])["response"] == third
    from fastapi import HTTPException

    for changed in ({**FLOW["rounds"][2]["native_body"], "result_limit": 5},
                    {**FLOW["rounds"][2]["native_body"], "behavior_context": FLOW["rounds"][1]["native_body"]["behavior_context"]}):
        with pytest.raises(HTTPException) as conflict:
            post(changed)
        assert conflict.value.status_code == 409 and "IDEMPOTENCY_CONFLICT" in conflict.value.detail


def test_exactly_ten_results_when_pool_is_larger(app_env):
    soft_only = {"listing_type": "RENT", "criteria": [r for r in REQUIREMENTS["criteria"] if r["strength"] == "soft"]}
    for limit in (None, 10):
        body = {**initial(f"v2-ten-{limit}", "v2-ten"), "requirements": soft_only, "result_limit": limit}
        response = post(body)["response"]
        assert response["pool"]["size"] == 12
        assert len(response["recommendations"]) == 10
        assert [i["rank"] for i in response["recommendations"]] == list(range(1, 11))
        assert response["selection"]["result_limit"] == 10


def test_ten_results_fixture_is_valid_and_reproduced(app_env):
    """tests/fixtures/recommendation-v2/ten-results.json (also projected by the TS test): a valid
    contract 2.0 response with exactly 10 recommendations, reproduced by a fresh run."""
    from recommendation.models.candidate import Response
    from recommendation.schemas import validate_response
    fixture = load("ten-results.json")
    stored = fixture["envelope"]["response"]
    validate_response(Response.model_validate(stored))
    assert len(stored["recommendations"]) == 10 and stored["pool"]["size"] == 12
    assert [i["rank"] for i in stored["recommendations"]] == list(range(1, 11))
    assert stored["selection"]["result_limit"] == fixture["native_body"]["result_limit"] == 10
    fresh = post(fixture["native_body"])["response"]
    key = lambda r: [(i["listing"]["listing_id"], i["scores"]["combined_score"], i["selection_reason"]) for i in r["recommendations"]]
    assert key(fresh) == key(stored)
    assert fresh["execution"]["selection_hash"] == stored["execution"]["selection_hash"]


def test_cold_start_exploration_fixture_is_valid_and_reproduced(app_env):
    """tests/fixtures/recommendation-v2/cold-start-exploration.json (TS oracle / explanation): mpd-v1 D = 0 on
    an all-tied pool, price exploration; a fresh run reproduces the exact draws, bins and selection block."""
    from recommendation.models.candidate import Response
    from recommendation.schemas import validate_response
    fixture = load("cold-start-exploration.json")
    stored = fixture["envelope"]["response"]
    validate_response(Response.model_validate(stored))
    cold = stored["selection"]["cold_start"]
    assert stored["selection"]["mode"] == "cold_start_exploration" and cold["active"] and cold["mean_pairwise_difference"] == 0
    fresh = post(fixture["native_body"])["response"]
    key = lambda r: [(i["listing"]["listing_id"], i["scores"]["combined_score"], i["selection_reason"]) for i in r["recommendations"]]
    assert key(fresh) == key(stored)
    assert fresh["selection"] == stored["selection"]  # seed, exploration bins / quotas, cold_start
    assert fresh["execution"]["selection_hash"] == stored["execution"]["selection_hash"]


def test_legacy_v1_head_starts_fresh_v2_chain(tmp_path):
    """A conversation that only has legacy contract-1.0 rows: a v2 request without a state
    reference starts at run_sequence 1; the legacy state id itself is not a v2 reference."""
    store = StateStore(tmp_path / "state.sqlite")
    store.db.execute("INSERT INTO conversations VALUES('legacy-conv','legacy-state',3)")
    store.db.execute("INSERT INTO runs VALUES('legacy-conv','legacy-r3','h','{}','{}')")
    store.db.commit()
    listing, geo = repositories("fixture")
    service = RecommendationService(listing, geo, store, ScriptedController())
    try:
        assert store.legacy_head("legacy-conv") == ("legacy-state", 3)
        stale = service.recommend(RecommendationRequest.model_validate(
            {**initial("legacy-conv", "v2-a"), "previous_state_id": "legacy-state"}))
        assert stale.error.code == "STALE_REFERENCE"  # service contract unchanged; app.py maps it to null
        fresh = service.recommend(RecommendationRequest.model_validate(initial("legacy-conv", "v2-b")))
        assert fresh.status == "success" and fresh.state.run_sequence == 1
        assert store.legacy_head("legacy-conv") == ("legacy-state", 3)  # legacy rows untouched
    finally:
        service.close()


SERVICE_SCRIPT = textwrap.dedent("""
    import json, sys
    from recommendation.contracts.request import RecommendationRequest
    from recommendation.repositories.connections import repositories
    from recommendation.service import RecommendationService
    from recommendation.state.store import StateStore
    from recommendation.agent.controller import ScriptedController
    from recommendation.terminal.__main__ import feedback_example
    path, conversation, requirements = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
    listing, geo = repositories("fixture")
    service = RecommendationService(listing, geo, StateStore(path), ScriptedController())
    previous = None
    for n in range(1, 4):
        body = {"request_id": f"{conversation}-r{n}", "conversation_id": conversation, "requirements_version": 1,
                "requirements": requirements, "previous_state_id": previous.state.state_id if previous else None,
                "behavior_context": feedback_example(previous) if previous else None}
        previous = service.recommend(RecommendationRequest.model_validate(body))
        assert previous.status == "success", previous.error
    service.close()
""")


def test_multiple_processes_terminal_and_service_share_db_without_cross_talk(tmp_path):
    """Two service processes and one terminal process write the same SQLite file concurrently;
    every conversation keeps its own chain (sequence, head, previous rounds)."""
    path = tmp_path / "shared.sqlite"
    StateStore(path).close()
    script = tmp_path / "service_rounds.py"
    script.write_text(SERVICE_SCRIPT)
    (tmp_path / "requirements.json").write_text(json.dumps(REQUIREMENTS))
    env = subprocess_env(PYTHONPATH=ROOT, RECOMMENDATION_STATE_PATH=path)
    processes = [subprocess.Popen([sys.executable, str(script), str(path), conversation, json.dumps(REQUIREMENTS)],
                                  cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for conversation in ("mp-service-1", "mp-service-2")]
    terminal_input = (f"requirements @{tmp_path / 'requirements.json'}\nrecommend\n"
                      f"feedback-example {tmp_path / 'fb.json'}\nfeedback @{tmp_path / 'fb.json'}\nrecommend\nexit\n")
    processes.append(subprocess.Popen(
        [sys.executable, "-m", "recommendation.terminal", "--data-mode", "fixture", "--controller", "scripted",
         "--conversation-id", "mp-terminal", "--state-path", str(path)],
        cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    terminal_out, terminal_err = processes[2].communicate(terminal_input, timeout=240)
    for process in processes[:2]:
        out, err = process.communicate(timeout=240)
        assert process.returncode == 0, err
    assert processes[2].returncode == 0, terminal_err
    assert "Staged behavior_context as of" in terminal_out
    store = StateStore(path)
    try:
        expected = {"mp-service-1": 3, "mp-service-2": 3, "mp-terminal": 2}
        for conversation, runs in expected.items():
            rows = store.db.execute(
                "SELECT request_id,state_id,sequence,response,snapshot FROM runs_v2 WHERE conversation_id=? ORDER BY sequence",
                (conversation,)).fetchall()
            assert [r[2] for r in rows] == list(range(1, runs + 1))
            assert store.current(conversation)["state_id"] == rows[-1][1]
            for index, (request_id, _state, _seq, response, snapshot) in enumerate(rows):
                response, snapshot = json.loads(response), json.loads(snapshot)
                assert response["conversation_id"] == snapshot["request"]["conversation_id"] == conversation
                context = snapshot["request"].get("behavior_context")
                assert (context is None) == (index == 0)
                if context:  # built from the previous response of the SAME chain
                    sources = {s for v in context["listing_view_durations"] for s in v["source_request_ids"]}
                    assert sources == {request_uuid(rows[index - 1][0])}
        total = store.db.execute("SELECT COUNT(*) FROM runs_v2").fetchone()[0]
        assert total == sum(expected.values())
        states = [r[0] for r in store.db.execute("SELECT state_id FROM runs_v2")]
        assert len(states) == len(set(states))
    finally:
        store.close()


def test_app_import_keeps_caller_runtime_selection(tmp_path):
    """Incident regression: importing recommendation.app must not replace a caller's state file,
    data mode, controller or settings file with the project .env (live) values."""
    state, config = tmp_path / "caller.sqlite", tmp_path / "caller.yaml"
    env = subprocess_env(PYTHONPATH=ROOT, RECOMMENDATION_STATE_PATH=state, RECOMMENDATION_CONFIG=config)
    probe = ("import json, os, recommendation.app as a; print(json.dumps([a.state_path()] + "
             "[os.environ[k] for k in ('RECOMMENDATION_DATA_MODE', 'RECOMMENDATION_CONTROLLER', 'RECOMMENDATION_CONFIG')]))")
    out = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env, check=True, capture_output=True, text=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [str(state), "fixture", "scripted", str(config)]
    assert not state.exists()  # importing the app opens no state


def test_isolation_guard_refuses_live_state_and_connections():
    from .isolation import is_live_path
    assert is_live_path(ROOT / "data/.runtime/recommendation/state.sqlite")
    assert is_live_path(ROOT / "recommendation/.runtime/state.sqlite") and not is_live_path(":memory:")
    with pytest.raises(AssertionError, match="live recommendation state"):
        StateStore(ROOT / "data/.runtime/recommendation/state.sqlite")
    with pytest.raises(AssertionError, match="live database"):
        repositories("database")
    assert app_module.state_path() != str(ROOT / "data/.runtime/recommendation/state.sqlite")
    assert os.environ["RECOMMENDATION_DATA_MODE"] == "fixture" and os.environ["RECOMMENDATION_CONTROLLER"] == "scripted"


def apply_ops(document, ops):
    """Shared mutation ops of response-cases.json (the TS test applies the same ones)."""
    import copy
    document = copy.deepcopy(document)
    for op in ops:
        target = document
        for key in op["path"]:
            target = target[key]
        if op["op"] == "merge":
            target.update(copy.deepcopy(op["value"]))
        elif op["op"] == "append":
            target.append(copy.deepcopy(op["value"]))
        else:
            raise AssertionError(f"unknown op {op['op']}")
    return document


@pytest.mark.parametrize("case", load("response-cases.json")["cases"], ids=lambda c: c["name"])
def test_response_cases(case):
    """Response invariants enforced by the Python model; the strict TS mirror must agree."""
    from recommendation.models.candidate import Response
    body = apply_ops(load("ten-results.json")["envelope"]["response"], case["ops"])
    if case["valid"]:
        Response.model_validate(body)
    else:
        with pytest.raises(ValidationError) as raised:
            Response.model_validate(body)
        assert case["error"] in str(raised.value), str(raised.value)


@pytest.mark.parametrize("strength", ["hard", "soft"])
def test_ambiguous_name_resolution_matches_scoring(tmp_path, strength):
    """A name-only destination matching several places is reported ambiguous AND evaluated as
    unknown (never the nearest of all matches): hard -> empty pool, needs_clarification; soft ->
    the criterion is unknown on every returned item."""
    listing, geo = repositories("fixture")
    geo.places.append({**geo.places[0], "place_id": "fixture-school-2", "latitude": 1.31})
    service = RecommendationService(listing, geo, StateStore(tmp_path / "s.sqlite"), ScriptedController())
    near = {"criterion_id": "near", "field": "distance", "operator": "lte", "value": 50000, "unit": "m",
            "distance_type": "straight_line", "destination_name": "Fixture School", "strength": strength, "weight": 1}
    body = {**initial("amb", f"amb-{strength}"),
            "requirements": {"listing_type": "RENT", "criteria": [*REQUIREMENTS["criteria"], near]}}
    try:
        response = service.recommend(RecommendationRequest.model_validate(body))
    finally:
        service.close()
    [resolved] = [r for r in response.resolution if r.criterion_id == "near"]
    assert resolved.status == "ambiguous" and resolved.entity_id is None
    if strength == "hard":
        assert response.status == "needs_clarification" and not response.recommendations
        assert any("near" in c and "ambiguous" in c for c in response.clarifications)
    else:
        assert response.status == "success"
        for item in response.recommendations:
            [result] = [r for r in item.criterion_results if r.criterion_id == "near"]
            assert result.outcome == "unknown"
