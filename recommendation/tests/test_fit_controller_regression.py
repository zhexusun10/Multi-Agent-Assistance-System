"""User-visible recovery behavior for the recommendation-fit defects; no API calls."""
import json

from recommendation.agent.controller import LiveController
from recommendation.tests.test_policy_controller import make_service, request


class RankedModel(LiveController):
    def __init__(self):
        super().__init__()
        self.turn = 0

    def invoke(self, messages, tools, remaining):
        self.turn += 1
        if self.turn == 1:
            ref = json.loads(messages[1]["content"])["context_ref"]
            return {"content": "", "tool_calls": [
                {"id": f"tool-{i}", "name": tool["name"], "args": {"context_ref": ref}}
                for i, tool in enumerate(tools)
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
        assert result.status == "needs_clarification"
        assert result.recommendations == []
        assert result.unverified_candidates
        assert all(any(r.outcome == "unknown" for r in c.criterion_results) for c in result.unverified_candidates)
        assert result.execution.model_calls == 1
    finally:
        service.close()


def test_invalid_final_ids_are_repaired_with_existing_tool_history(tmp_path):
    class RepairModel(RankedModel):
        def invoke(self, messages, tools, remaining):
            if self.turn == 0:
                return super().invoke(messages, tools, remaining)
            self.turn += 1
            if self.turn == 2:
                return {"content": json.dumps({"listing_ids": ["not-in-the-ranking"]}), "tool_calls": []}
            assert any(m["role"] == "tool" for m in messages), "Repair discarded the validated tool history"
            assert "INVALID_PROPOSAL" in messages[-1]["content"]
            ranking = [json.loads(m["content"])["ranked_ids"] for m in messages if m["role"] == "tool"][-1]
            return {"content": json.dumps({"listing_ids": ranking[:4]}), "tool_calls": []}

    service = make_service(tmp_path, RepairModel())
    try:
        result = service.recommend(request())
        assert result.status == "success"
        assert result.execution.model_calls == 3
        assert result.execution.tool_calls == 4
        gates = [s for s in service.store.trace("policy")["steps"] if s["kind"] == "final_gate"]
        assert [g["passed"] for g in gates] == [False, True]
    finally:
        service.close()
