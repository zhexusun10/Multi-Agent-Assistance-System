"""Generator for tests/fixtures/recommendation-v2/single-reference.json (the JSON is the committed source).

Fixture data, ScriptedController, temporary state. Round 1: hard budget <= 6000 (all 12 fixture
listings eligible, only price suppressed). Round 2: the same requirements plus a previous-round
snapshot with exactly ONE usable browsing reference (fixture-05, 90 s), one 0-second record and
missing records for the rest, no appointments. The fixture stores the result-affecting settings the
TS oracle needs (tests/helpers/select-v1-oracle.ts oracleSettings shape) so the offline TS test does
not depend on later edits of recommendation.yaml.

Usage (repository root): .venv/bin/python evaluation/recommendation-exploration-2026-10-23/p7/gen_single_reference_fixture.py
"""
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
from recommendation.contracts.request import RecommendationRequest  # noqa: E402

assert app.state_path().startswith(tmp)
requirements = {"listing_type": "RENT", "criteria": [
    {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 6000, "unit": "SGD/month", "strength": "hard"}]}
body = {"contract_version": "2.0", "request_id": "single-r1", "conversation_id": "single-reference", "requirements_version": 1,
        "requirements": requirements, "previous_state_id": None, "result_limit": 10,
        "feedback": {"previous_round": None, "appointments": []}, "cold_start": {"exited": False, "exited_pool_key": None}}
first = app.execute(RecommendationRequest.model_validate(body), app_view=False)
shown = [i["listing"]["listing_id"] for i in first["recommendations"]]
assert "fixture-05" in shown and "fixture-07" in shown, shown
browsing = [{"listing_id": i, "status": "recorded", "duration_seconds": 90} if i == "fixture-05"
            else {"listing_id": i, "status": "recorded", "duration_seconds": 0} if i == "fixture-07"
            else {"listing_id": i, "status": "missing", "duration_seconds": None} for i in shown]
second_body = {**body, "request_id": "single-r2", "previous_state_id": first["state"]["state_id"],
               "feedback": {"previous_round": {"request_id": "single-r1", "shown_listing_ids": shown, "browsing": browsing}, "appointments": []}}
second = app.execute(RecommendationRequest.model_validate(second_body), app_view=False)
assert second["status"] == "success"
settings = RecommendationConfig.default()
ranking = settings.ranking
oracle_settings = {
    "soft_weight": ranking.soft_weight, "behavior_weight": ranking.behavior_weight,
    "appointment_weight": ranking.behavior.appointment_weight, "browsing_weight": ranking.behavior.browsing_weight,
    "feature_weights": ranking.similarity.feature_weights.as_dict(), "numeric_scaling": ranking.similarity.numeric_scaling,
    "geographic_scale_m": ranking.similarity.geographic_scale_m,
    "geography_district_fallback": ranking.similarity.geography_district_fallback,
    "min_comparison_coverage": ranking.similarity.min_comparison_coverage,
}
out = {"description": "Fixture data, scripted controller: round 2 of a hard-budget-only request with exactly one usable browsing "
                      "reference (fixture-05, 90 s; fixture-07 recorded 0 s; others missing). Checked by tests/select-v1-oracle.test.ts "
                      "against the independent TS fused-v2 oracle (single-reference normalisation, self pair excluded, p05_p95 spread, "
                      "district fallback for fixture-12 without coordinates) and the select-v1 oracle.",
       "oracle_settings": oracle_settings, "native_body": second_body, "response": json.loads(json.dumps(second, default=str))}
target = ROOT / "tests/fixtures/recommendation-v2/single-reference.json"
target.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("ok", second["selection"]["mode"], [(i["listing"]["listing_id"], round(i["scores"]["combined_score"], 6)) for i in second["recommendations"]])
