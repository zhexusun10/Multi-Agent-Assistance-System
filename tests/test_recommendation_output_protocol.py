"""Agent output protocol (recommendation-agent-v1): the model finishes by naming the current ranking
artifact; it never submits listing IDs, and code selects and validates after the loop."""
import json
import pytest

from recommendation.agent.controller import LiveController, ScriptedController, parse_completion
from recommendation.config import RecommendationConfig
from recommendation.contracts.request import RecommendationRequest
from recommendation.repositories.connections import repositories
from recommendation.service import RecommendationService
from recommendation.state.store import StateStore

ARTIFACT = "trace/ranking/0123456789abcdef"


@pytest.mark.parametrize("content", [
    json.dumps({"ranking_artifact_id": ARTIFACT}),
    f'Ranking is ready.\n\n{{"ranking_artifact_id":"{ARTIFACT}"}}',
    f'```json\n{{"ranking_artifact_id":"{ARTIFACT}"}}\n```',
    [{"type": "text", "text": f'Done.\n{{"ranking_artifact_id":"{ARTIFACT}"}}'}],
])
def test_one_completion_object_with_prose_is_accepted(content):
    assert parse_completion(content).ranking_artifact_id == ARTIFACT


@pytest.mark.parametrize("content", [
    f'{{"ranking_artifact_id":"{ARTIFACT}"}} {{"ranking_artifact_id":"other"}}',
    f'{{"extra":{{"ranking_artifact_id":"{ARTIFACT}"}}}}',
    f'{{"ranking_artifact_id":"{ARTIFACT}","change_budget":true}}',
    '{"listing_ids":["A","B"]}',  # listing IDs are never a completion
    '{"ranking_artifact_id":""}',
    'not JSON',
])
def test_invalid_or_ambiguous_completions_are_not_silently_repaired(content):
    assert parse_completion(content) is None


def make_service(tmp_path, controller, conversation):
    listing, geo = repositories("fixture")
    store = StateStore(tmp_path / (conversation + ".sqlite"))
    return RecommendationService(listing, geo, store, controller, config=RecommendationConfig.default())


def request(value=1):
    # A hard budget no fixture listing meets gives an empty (still completed) ranking.
    return RecommendationRequest(request_id="empty-prefix", conversation_id="output-protocol", requirements_version=1,
                                 requirements={"listing_type": "RENT", "criteria": [
                                     {"criterion_id": "budget", "field": "price", "operator": "lte", "value": value,
                                      "unit": "SGD/month", "strength": "hard"}]})


class Staged(LiveController):
    """Fake model: the pipeline in three turns, then ``final(messages)``."""

    def __init__(self, final):
        super().__init__()
        self.final = final
        self.calls = []

    def invoke(self, messages, tools, remaining):
        self.calls.append(messages)
        ref = json.loads(messages[1]["content"])["context_ref"]
        order = [["resolve_entities", "build_pool"], ["get_geographic_evidence"], ["rank_candidates"]]
        if len(self.calls) <= 3:
            return {"content": "", "tool_calls": [{"id": f"call-{len(self.calls)}-{i}", "name": n,
                                                   "args": {"context_ref": ref}}
                                                  for i, n in enumerate(order[len(self.calls) - 1])]}
        return self.final(messages)


def test_empty_ranking_completes_without_another_model_call(tmp_path):
    model = Staged(lambda messages: pytest.fail("no model call is needed after an empty ranking"))
    service = make_service(tmp_path, model, "live-protocol")
    oracle = make_service(tmp_path, ScriptedController(), "native-oracle")
    try:
        response = service.recommend(request())
        expected = oracle.recommend(request())
        assert response.status == expected.status == "no_match" and not response.recommendations
        assert response.execution.model_calls == 3 and response.execution.tool_calls == 4
        assert response.pool.model_dump() == expected.pool.model_dump()
        trace = service.store.trace(request().conversation_id)
        assert trace["steps"][-1]["kind"] == "final_gate" and trace["steps"][-1]["passed"]
        assert trace["controller_completion"] == "assessed_empty_ranking"
        assert len(model.calls) == 3
    finally:
        service.close()
        oracle.close()


def test_invented_shortlists_and_artifacts_never_reach_selection(tmp_path):
    answers = iter([
        'I suggest these instead: {"listing_ids":["invented-id"]}',
        '{"ranking_artifact_id":"invented-artifact"}',
    ] * 10)
    model = Staged(lambda messages: {"content": next(answers), "tool_calls": []})
    service = make_service(tmp_path, model, "invalid-proposal")
    try:
        result = service.recommend(request(10000))
        assert result.status == "error" and result.error.code == "LOOP_EXHAUSTED" and not result.recommendations
        assert service.store.current(request().conversation_id) is None
        trace = service.store.trace(request().conversation_id)
        assert not [s for s in trace["steps"] if s["kind"] in ("selection", "final_gate")]
    finally:
        service.close()


def test_the_model_cannot_finish_before_ranking(tmp_path):
    class Early(LiveController):
        turns = 0

        def invoke(self, messages, tools, remaining):
            self.turns += 1
            ref = json.loads(messages[1]["content"])["context_ref"]
            if self.turns == 1:
                return {"content": '{"ranking_artifact_id":"' + ref + '/ranking/0000000000000000"}', "tool_calls": []}
            if self.turns == 2:
                assert json.loads(messages[-1]["content"])["completion_rejected"] == "no_current_ranking_artifact"
                return {"content": "", "tool_calls": [{"id": f"p{i}", "name": n, "args": {"context_ref": ref}} for i, n in
                                                      enumerate(("resolve_entities", "build_pool",
                                                                 "get_geographic_evidence", "rank_candidates"))]}
            return {"content": "still thinking", "tool_calls": []}

    service = make_service(tmp_path, Early(), "early")
    try:
        result = service.recommend(request(10000))
        # The current artifact exists after turn 2, but the model never named it: no result.
        assert result.status == "error" and result.error.code == "LOOP_EXHAUSTED"
    finally:
        service.close()
