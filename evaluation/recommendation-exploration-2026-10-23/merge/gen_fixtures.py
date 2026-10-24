"""Generator for the shared Python/TS fixtures in tests/fixtures/recommendation-v2/ after the merge with
main c7dc536 (request = main contract 1.0 + behavior_context + cold_start; response = recommendation 2.0).

The JSON files are the committed source; this script documents how they were produced. Fixture data,
ScriptedController, temporary state (never data/.runtime). Listing IDs are the fixture's "fixture-NN";
the TS tests map them to numeric IDs (910000NN) because the app contract requires numeric IDs.

Usage (repository root): .venv/bin/python evaluation/recommendation-exploration-2026-10-23/merge/gen_fixtures.py
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ.update(RECOMMENDATION_DATA_MODE="fixture", RECOMMENDATION_CONTROLLER="scripted",
                  RECOMMENDATION_STATE_PATH=str(Path(tmp) / "state.sqlite"))
os.environ.pop("RECOMMENDATION_CONFIG", None)
os.environ.pop("RECOMMENDATION_TRACE_MODE", None)
import recommendation.app as app  # noqa: E402
from recommendation.config import RecommendationConfig  # noqa: E402
from recommendation.contracts.behavior import request_uuid  # noqa: E402
from recommendation.contracts.request import RecommendationRequest  # noqa: E402

assert app.state_path().startswith(tmp)
OUT = ROOT / "tests/fixtures/recommendation-v2"
AS_OF = "2026-10-23T08:00:00.000Z"

# V1 (app) criteria and their native mapping (src/adapters/native-recommendation.ts nativeRequest).
V1_CRITERIA = [
    {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 3000, "unit": "SGD/month", "strength": "hard"},
    {"criterion_id": "bedrooms", "field": "bedrooms", "operator": "gte", "value": 2, "unit": "count", "strength": "hard"},
    {"criterion_id": "district", "field": "district", "operator": "in", "value": ["D05"], "strength": "soft", "weight": 1},
]
NATIVE_CRITERIA = [
    {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 3000, "strength": "hard", "weight": 1,
     "utility": "threshold", "unit": "SGD/month"},
    {"criterion_id": "bedrooms", "field": "bedrooms", "operator": "gte", "value": 2, "strength": "hard", "weight": 1,
     "utility": "threshold"},
    {"criterion_id": "district", "field": "district_code", "operator": "in", "value": ["D05"], "strength": "soft",
     "weight": 1, "utility": "threshold"},
]
REQUIREMENTS = {"listing_type": "RENT", "criteria": NATIVE_CRITERIA}
NOT_EXITED = {"exited": False, "exited_pool_key": None}


def view(listing_id, seconds, eligible=True, rounds=("v2-r1",), views=1):
    return {"listing_id": listing_id, "duration_seconds": seconds, "view_count": views, "last_viewed_at": AS_OF,
            "source_request_ids": sorted(request_uuid(r) for r in rounds), "interest_eligible": eligible}


def draft(listing_id, action="draft", round_id="v2-r1"):
    return {"listing_id": listing_id, "action_id": request_uuid(f"{action}-{listing_id}"),
            "source_request_id": request_uuid(round_id), "requirements_version": 0,
            "starts_at": "2026-10-24T06:00:00.000Z", "ends_at": "2026-10-24T07:00:00.000Z", "time_zone": "Asia/Singapore",
            "recorded_at": AS_OF, "signal": "viewing_draft_saved", "appointment_confirmed": False}


def behavior(views=(), drafts=(), as_of=AS_OF):
    return {"version": "1.0", "as_of": as_of, "listing_view_durations": list(views), "book_viewing_listings": list(drafts)}


def native_event(event_id, conversation, listing_id, seconds, round_id="v2-r1", source="web_detail"):
    return {"event_id": event_id, "conversation_id": conversation, "recommendation_request_id": request_uuid(round_id),
            "listing_id": listing_id, "event_type": "listing_view_duration", "source": source, "occurred_at": AS_OF,
            "payload": {"view_session_id": f"view-{event_id}", "duration_seconds": seconds}}


def v1_event(event_id, listing_id, seconds, round_id="v2-r1", source="web_detail"):
    return {"event_id": event_id, "listing_id": listing_id, "occurred_at": AS_OF, "event_type": "active_view",
            "payload": {"source": source, "recommendation_request_id": request_uuid(round_id),
                        "view_session_id": f"view-{event_id}", "active_duration_ms": seconds * 1000}}


def body(request_id, conversation="v2-fixture", **extra):
    out = {"contract_version": "1.0", "request_id": request_id, "conversation_id": conversation, "requirements_version": 1,
           "requirements": copy.deepcopy(REQUIREMENTS), "feedback": [], "previous_state_id": None, "result_limit": 10,
           "cold_start": dict(NOT_EXITED)}
    out.update(extra)
    return out


def post(payload):
    return app.execute(RecommendationRequest.model_validate(payload), app_view=True)


SHOWN = ["fixture-01", "fixture-03", "fixture-05", "fixture-07", "fixture-09", "fixture-02", "fixture-04", "fixture-06",
         "fixture-08"]

# --- contract-cases.json -------------------------------------------------------------------------------
cumulative = behavior([view("fixture-01", 90, rounds=("v2-r1", "v2-r0"), views=2), view("fixture-07", 30),
                       view("fixture-09", 12, eligible=False)], [draft("fixture-07")])
cases = [
    {"name": "initial", "valid": True, "body": body("v2-case")},
    {"name": "behavior-context-cumulative", "valid": True, "body": body("v2-case", behavior_context=cumulative)},
    {"name": "zero-seconds", "valid": True, "body": body("v2-case", behavior_context=behavior([view("fixture-01", 0)]))},
    {"name": "not-interest-eligible-only", "valid": True,
     "body": body("v2-case", behavior_context=behavior([view("fixture-01", 90, eligible=False)]))},
    {"name": "empty-behavior-context", "valid": True, "body": body("v2-case", behavior_context=behavior())},
    {"name": "viewing-drafts-only", "valid": True,
     "body": body("v2-case", behavior_context=behavior(drafts=[draft("fixture-03"), draft("fixture-07")]))},
    {"name": "out-of-pool-references", "valid": True,
     "body": body("v2-case", behavior_context=behavior([view("fixture-10", 60)], [draft("fixture-11")]))},
    {"name": "feedback-events-validated-not-scored", "valid": True,
     "body": body("v2-case", feedback=[native_event("web-1", "v2-fixture", "fixture-01", 40)])},
    {"name": "result-limit-10", "valid": True, "body": body("v2-case", result_limit=10)},
    {"name": "result-limit-null-uses-config-default", "valid": True, "body": body("v2-case", result_limit=None)},
    {"name": "contract-version-omitted", "valid": True,
     "body": {k: v for k, v in body("v2-case").items() if k != "contract_version"}},
    {"name": "cold-start-exited", "valid": True,
     "body": body("v2-case", cold_start={"exited": True, "exited_pool_key": "some-other-pool"})},
    # invalid
    {"name": "duplicate-view-listing", "valid": False, "error": "duplicate listing summary",
     "body": body("v2-case", behavior_context=behavior([view("fixture-01", 30), view("fixture-01", 40)]))},
    {"name": "duplicate-draft-listing", "valid": False, "error": "duplicate listing summary",
     "body": body("v2-case", behavior_context=behavior(drafts=[draft("fixture-07", "a"), draft("fixture-07", "b")]))},
    {"name": "negative-seconds", "valid": False, "error": "greater than or equal to 0",
     "body": body("v2-case", behavior_context=behavior([view("fixture-01", -1)]))},
    {"name": "naive-as-of", "valid": False, "error": "timezone required",
     "body": body("v2-case", behavior_context=behavior(as_of="2026-10-23T08:00:00"))},
    {"name": "viewing-interval-over-4h", "valid": False, "error": "invalid viewing interval",
     "body": body("v2-case", behavior_context=behavior(drafts=[{**draft("fixture-07"), "ends_at": "2026-10-24T11:00:01.000Z"}]))},
    {"name": "appointment-confirmed-true", "valid": False, "error": "appointment_confirmed",
     "body": body("v2-case", behavior_context=behavior(drafts=[{**draft("fixture-07"), "appointment_confirmed": True}]))},
    {"name": "unknown-viewing-signal", "valid": False, "error": "signal",
     "body": body("v2-case", behavior_context=behavior(drafts=[{**draft("fixture-07"), "signal": "manual_sent"}]))},
    {"name": "source-request-id-not-uuid", "valid": False, "error": "source_request_ids",
     "body": body("v2-case", behavior_context=behavior([{**view("fixture-01", 30), "source_request_ids": ["v2-r1"]}]))},
    {"name": "result-limit-11", "valid": False, "error": "less than or equal to 10", "body": body("v2-case", result_limit=11)},
    {"name": "contract-2.0-body", "valid": False, "error": "UNSUPPORTED_VERSION",
     "body": {**body("v2-case"), "contract_version": "2.0"}},
    {"name": "feedback-other-conversation", "valid": False, "error": "feedback conversation mismatch",
     "body": body("v2-case", feedback=[native_event("web-1", "v2-other", "fixture-01", 40)])},
    {"name": "duplicate-feedback-event", "valid": False, "error": "duplicate feedback event_id",
     "body": body("v2-case", feedback=[native_event("web-1", "v2-fixture", "fixture-01", 40),
                                       native_event("web-1", "v2-fixture", "fixture-03", 20)])},
    {"name": "old-feedback-snapshot-object", "valid": False, "error": "feedback",
     "body": body("v2-case", feedback={"previous_round": None, "appointments": []})},
    {"name": "cold-start-exited-without-pool-key", "valid": False, "error": "exited_pool_key",
     "body": body("v2-case", cold_start={"exited": True, "exited_pool_key": None})},
    {"name": "unknown-field", "valid": False, "error": "Extra inputs are not permitted", "body": body("v2-case", surprise=1)},
    {"name": "stale-requirements-changed-same-version", "valid": True, "service_error": "STALE_REQUIREMENTS",
     "body": body("v2-case", requirements={"listing_type": "RENT", "criteria": [{**NATIVE_CRITERIA[0], "value": 2500},
                                                                                *NATIVE_CRITERIA[1:]]})},
]
contract = {
    "description": "Native request cases after the merge with main c7dc536: main contract 1.0 (feedback events + "
                   "behavior_context) + recommendation-owned cold_start, result_limit 1..10. valid = schema-valid "
                   "(Python RecommendationRequest and TS nativeRequestSchema after mapping fixture-NN to numeric IDs); "
                   "service_error = schema-valid but rejected by the service after round v2-r1. Service-level cases run "
                   "against /app/recommend after rounds v2-r1 (v2-fixture) and v2-other-r1 (v2-other).",
    "setup_requirements": REQUIREMENTS, "cases": cases}

# --- flow.json ------------------------------------------------------------------------------------------
V1_BASE = {"contract_version": "1.0", "conversation_id": "v2-flow", "requirements_version": 0,
           "requirements": {"listing_type": "RENT", "criteria": V1_CRITERIA}, "result_limit": 10}
rounds = []
previous = None
flow_specs = [
    ("initial", None, [], NOT_EXITED, {"browsing_weighted": [], "appointment_listing_ids": []}),
    ("cumulative-browsing-and-draft",
     behavior([view("fixture-01", 90, rounds=("v2-flow-r1",)), view("fixture-07", 30, rounds=("v2-flow-r1",)),
               view("fixture-09", 12, eligible=False, rounds=("v2-flow-r1",))],
              [draft("fixture-03", round_id="v2-flow-r1")]),
     [("web-r1-1", "fixture-01", 90)], NOT_EXITED,
     {"browsing_weighted": ["fixture-01", "fixture-07"], "appointment_listing_ids": ["fixture-03"]}),
    ("gate-closed-draft-withdrawn",
     behavior([view("fixture-01", 95, eligible=False, rounds=("v2-flow-r1", "v2-flow-r2"), views=2),
               view("fixture-07", 30, eligible=False, rounds=("v2-flow-r1",))]),
     [], NOT_EXITED, {"browsing_weighted": [], "appointment_listing_ids": []}),
]
for n, (name, context, events, cold, expect) in enumerate(flow_specs, 1):
    request_id = f"v2-flow-r{n}"
    state = f"native:{previous['state']['state_id']}" if previous else None
    v1 = {**copy.deepcopy(V1_BASE), "request_id": request_id,
          "feedback": [v1_event(e, l, s, round_id="v2-flow-r1") for e, l, s in events],
          **({"behavior_context": context} if context else {}), **({"previous_state_id": state} if state else {})}
    native = {"contract_version": "1.0", "request_id": request_id, "conversation_id": "v2-flow", "requirements_version": 1,
              "requirements": copy.deepcopy(REQUIREMENTS),
              "feedback": [native_event(e, "v2-flow", l, s, round_id="v2-flow-r1") for e, l, s in events],
              **({"behavior_context": context} if context else {}),
              "previous_state_id": previous["state"]["state_id"] if previous else None, "result_limit": 10,
              "cold_start": dict(cold)}
    envelope = post(native)
    response = envelope["response"]
    assert response["status"] == "success", response.get("error")
    rounds.append({"name": name, "v1_request": v1, "cold_start": cold, "native_body": native,
                   "expect": {"status": "success", **expect}})
    previous = response
flow = {"description": "Three orchestrator rounds (main contract): round 2 sends the session-cumulative "
                       "behavior_context (two interest_eligible summaries, one not eligible, one saved viewing draft) and "
                       "a web_detail event (validated, not scored); round 3 sends summaries with the gate closed and the "
                       "draft withdrawn, so it is scored like round 1 (nothing accumulated natively). TS: "
                       "nativeRequest(v1_request, cold_start) == native_body after fixture-NN -> 910000NN.",
        "conversation_id": "v2-flow", "rounds": rounds}

# --- ten-results.json -----------------------------------------------------------------------------------
soft_only = {"listing_type": "RENT", "criteria": [c for c in NATIVE_CRITERIA if c["strength"] == "soft"]}
ten_body = {"contract_version": "1.0", "request_id": "v2-ten", "conversation_id": "v2-ten", "requirements_version": 1,
            "requirements": soft_only, "feedback": [], "previous_state_id": None, "result_limit": 10,
            "cold_start": dict(NOT_EXITED)}
ten_envelope = json.loads(json.dumps(post(ten_body), default=str))
assert len(ten_envelope["response"]["recommendations"]) == 10
ten = {"description": "Native /app/recommend envelope with exactly 10 recommendations (fixture data, scripted controller, "
                      "soft-only requirements, pool 12; request = main contract 1.0). Python: Response model + "
                      "validate_response, and a fresh run returns the same IDs/scores. TS: nativeResponseSchema + "
                      "projectNative (ranks 1..10, V1 validateResponse with result_limit 10).",
       "v1_request": {"contract_version": "1.0", "request_id": "v2-ten", "conversation_id": "v2-ten", "requirements_version": 0,
                      "requirements": {"listing_type": "RENT", "criteria": [c for c in V1_CRITERIA if c["strength"] == "soft"]},
                      "feedback": [], "result_limit": 10},
       "native_body": ten_body, "envelope": ten_envelope}

# --- single-reference.json ------------------------------------------------------------------------------
budget = {"listing_type": "RENT", "criteria": [
    {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 6000, "unit": "SGD/month", "strength": "hard"}]}
single1 = {"contract_version": "1.0", "request_id": "single-r1", "conversation_id": "single-reference", "requirements_version": 1,
           "requirements": budget, "feedback": [], "previous_state_id": None, "result_limit": 10, "cold_start": dict(NOT_EXITED)}
first = app.execute(RecommendationRequest.model_validate(single1), app_view=False)
shown = [i["listing"]["listing_id"] for i in first["recommendations"]]
assert "fixture-05" in shown and "fixture-07" in shown, shown
single_context = behavior([view("fixture-05", 90, rounds=("single-r1",)), view("fixture-07", 0, rounds=("single-r1",)),
                           view("fixture-08", 40, eligible=False, rounds=("single-r1",))])
single2 = {**single1, "request_id": "single-r2", "previous_state_id": first["state"]["state_id"],
           "behavior_context": single_context}
second = app.execute(RecommendationRequest.model_validate(single2), app_view=False)
assert second["status"] == "success"
ranking = RecommendationConfig.default().ranking
oracle_settings = {
    "soft_weight": ranking.soft_weight, "behavior_weight": ranking.behavior_weight,
    "appointment_weight": ranking.behavior.appointment_weight, "browsing_weight": ranking.behavior.browsing_weight,
    "feature_weights": ranking.similarity.feature_weights.as_dict(), "numeric_scaling": ranking.similarity.numeric_scaling,
    "geographic_scale_m": ranking.similarity.geographic_scale_m,
    "geography_district_fallback": ranking.similarity.geography_district_fallback,
    "min_comparison_coverage": ranking.similarity.min_comparison_coverage,
}
single = {"description": "Fixture data, scripted controller: round 2 of a hard-budget-only request whose behavior_context has "
                         "exactly one usable browsing reference (fixture-05, 90 s, interest_eligible); fixture-07 has 0 s and "
                         "fixture-08 is not interest_eligible. Checked by tests/select-v1-oracle.test.ts against the "
                         "independent TS fused-v2 oracle (single-reference normalisation, self pair excluded, p05_p95 spread, "
                         "district fallback for fixture-12 without coordinates) and the select-v1 oracle.",
          "oracle_settings": oracle_settings, "native_body": single2, "response": json.loads(json.dumps(second, default=str))}

for name, data in [("contract-cases.json", contract), ("flow.json", flow), ("ten-results.json", ten),
                   ("single-reference.json", single)]:
    (OUT / name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("ok", {r["name"]: [i["listing"]["listing_id"] for i in []] for r in rounds}, second["selection"]["mode"])
