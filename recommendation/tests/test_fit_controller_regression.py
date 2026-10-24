"""User-visible recovery behavior for the recommendation-fit defects; no API calls."""
import json

from recommendation.agent.controller import LiveController
from recommendation.tools.registry import PIPELINE
from recommendation.tests.test_policy_controller import make_service, request


class RankedModel(LiveController):
    def __init__(self):
        super().__init__()
        self.turn = 0

    def invoke(self, messages, tools, remaining):
        self.turn += 1
        if self.turn == 1:
            ref = json.loads(messages[1]["content"])["context_ref"]
            # The four pipeline tools in order (the query/bind tools take other arguments).
            return {"content": "", "tool_calls": [
                {"id": f"tool-{i}", "name": tool["name"], "args": {"context_ref": ref}}
                for i, tool in enumerate(t for t in tools if t["name"] in PIPELINE)
            ]}
        raise AssertionError("An assessed empty ranking needs no invented final IDs")


def test_assessed_empty_ranking_finishes_and_still_runs_final_gate(tmp_path):
    model = RankedModel()
    service = make_service(tmp_path, model)
    rows = [r.model_copy(update={"bedrooms": 0.0}) for r in service.listing.retrieve()]
    service.listing.retrieve = lambda: rows
    req = request()
    req.requirements.criteria = [req.requirements.criteria[1].model_copy(update={
        "criterion_id": "impossible-bedroom-count", "field": "bedrooms",
        "operator": "gte", "value": 20.0, "strength": "hard",
    })]
    try:
        result = service.recommend(req)
        assert result.status == "no_match"
        assert result.recommendations == []
        assert result.execution.model_calls == 1
        assert result.execution.tool_calls == 4
        trace = service.store.trace(req.conversation_id)
        assert any(s["kind"] == "final_gate" and s["passed"] for s in trace["steps"])
    finally:
        service.close()


def test_empty_verified_ranking_keeps_unknown_candidates_unverified(tmp_path):
    # Contract 2.0 (R8): unknown hard data is a data problem, not a criterion problem. The
    # listings stay unverified (excluded, counted with reasons, warned), status no_match.
    model = RankedModel()
    service = make_service(tmp_path, model)
    rows = [r.model_copy(update={"bedrooms": None}) for r in service.listing.retrieve()]
    service.listing.retrieve = lambda: rows
    req = request()
    req.requirements.criteria = [req.requirements.criteria[1].model_copy(update={
        "criterion_id": "minimum-bedrooms", "field": "bedrooms",
        "operator": "gte", "value": 2.0, "strength": "hard",
    })]
    try:
        result = service.recommend(req)
        assert result.status == "no_match"
        assert result.recommendations == []
        assert result.pool.size == 0 and result.pool.excluded_hard_unknown > 0
        assert result.pool.excluded_hard_unknown + result.pool.excluded_hard_fail == len(rows)
        assert result.pool.unknown_reasons == {"bedrooms: Missing listing attribute": result.pool.excluded_hard_unknown}
        assert any("hard-criterion data is unknown" in w for w in result.warnings)
        assert result.execution.model_calls == 1
    finally:
        service.close()


def test_invalid_completion_is_corrected_with_existing_tool_history(tmp_path):
    class RepairModel(RankedModel):
        def invoke(self, messages, tools, remaining):
            if self.turn == 0:
                return super().invoke(messages, tools, remaining)
            self.turn += 1
            if self.turn == 2:
                return {"content": json.dumps({"ranking_artifact_id": "not-the-current-artifact"}), "tool_calls": []}
            assert any(m["role"] == "tool" for m in messages), "Correction discarded the validated tool history"
            assert json.loads(messages[-1]["content"])["completion_rejected"] == "not_the_current_ranking_artifact"
            artifact = [json.loads(m["content"])["ranking_artifact_id"] for m in messages if m["role"] == "tool"][-1]
            return {"content": json.dumps({"ranking_artifact_id": artifact}), "tool_calls": []}

    service = make_service(tmp_path, RepairModel())
    try:
        result = service.recommend(request())
        assert result.status == "success"
        assert result.execution.model_calls == 3
        assert result.execution.tool_calls == 4
        gates = [s for s in service.store.trace("policy")["steps"] if s["kind"] == "final_gate"]
        assert [g["passed"] for g in gates] == [True]  # rejected before any selection or gate
    finally:
        service.close()
