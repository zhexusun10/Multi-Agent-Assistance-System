import json
import pytest

from recommendation.app_controller import ApplicationLiveController, canonical_proposal
from recommendation.agent.controller import LiveController, ScriptedController
from recommendation.config import RecommendationConfig
from recommendation.contracts.request import RecommendationRequest
from recommendation.repositories.connections import repositories
from recommendation.service import RecommendationService
from recommendation.state.store import StateStore


@pytest.mark.parametrize("content", [
    '{"listing_ids":[]}',
    'No verified homes; pending options are handled separately.\n\n{"listing_ids":[]}',
    '```json\n{"listing_ids":[]}\n```',
    [{"type":"text","text":"No verified homes.\n{\"listing_ids\":[]}"}],
])
def test_empty_proposal_with_prose_is_a_valid_empty_proposal(content):
    assert json.loads(canonical_proposal(content)) == {"listing_ids":[]}


@pytest.mark.parametrize("content", [
    '{"listing_ids":[]} {"listing_ids":["A"]}',
    '{"extra":{"listing_ids":[]}}',
    '{"listing_ids":[],"change_budget":true}',
    '{"listing_ids":["A","B","C","D","E"]}',
    'not JSON',
])
def test_invalid_or_ambiguous_envelopes_are_not_silently_repaired(content):
    assert canonical_proposal(content) is None


def make_service(tmp_path, controller, conversation):
    listing,geo = repositories("fixture")
    store = StateStore(tmp_path / (conversation+".sqlite"))
    return RecommendationService(listing,geo,store,controller,config=RecommendationConfig())


def request():
    return RecommendationRequest(request_id="empty-prefix",conversation_id="output-protocol",requirements_version=1,requirements={"listing_type":"RENT","criteria":[{"criterion_id":"region","field":"distance","operator":"lte","value":5000,"unit":"m","strength":"hard","destination_id":"URA:AREA:JURONG_EAST","distance_type":"straight_line"}]})


def test_real_failure_replay_stops_after_one_four_tool_pass(tmp_path,monkeypatch):
    calls=[]
    def invoke(self,messages,tools,remaining):
        calls.append(messages)
        ref=self._dispatcher.ref
        order=[['retrieve_listings','get_geographic_evidence'],['validate_hard_constraints'],['rank_candidates']]
        if len(calls)<=3:
            return {"content":"", "tool_calls":[{"id":f"call-{len(calls)}-{i}","name":n,"args":{"context_ref":ref}} for i,n in enumerate(order[len(calls)-1])]}
        assert '"rank_computed": true' in messages[0]['content']
        assert '"current_ranked_prefix": []' in messages[0]['content']
        return {"content":'No ranked homes are verified.\n\n{"listing_ids":[]}',"tool_calls":[]}
    monkeypatch.setattr(LiveController,"invoke",invoke)
    service=make_service(tmp_path,ApplicationLiveController(),"live-protocol")
    oracle=make_service(tmp_path,ScriptedController(),"native-oracle")
    try:
        response=service.recommend(request());expected=oracle.recommend(request())
        assert response.status==expected.status=="needs_clarification"
        assert not response.recommendations and response.unverified_candidates
        assert response.execution.model_calls==3 and response.execution.tool_calls==4
        assert [v.model_dump() for v in response.unverified_candidates]==[v.model_dump() for v in expected.unverified_candidates]
        trace=service.store.trace(request().conversation_id)
        assert trace['steps'][-1]['kind']=='final_gate' and trace['steps'][-1]['passed']
        assert trace['controller_completion']=='assessed_empty_ranking'
        assert len(calls)==3  # The merged controller needs no extra model call for an empty ranking.
    finally:
        service.close();oracle.close()


def test_normalization_does_not_fix_invented_or_reordered_ids():
    assert json.loads(canonical_proposal('Here: {"listing_ids":["B","A"]}'))['listing_ids']==['B','A']


def test_native_final_gate_still_rejects_invented_shortlists(tmp_path,monkeypatch):
    turns=[]
    def invoke(self,messages,tools,remaining):
        turns.append(1);ref=self._dispatcher.ref
        stages=[['retrieve_listings','get_geographic_evidence'],['validate_hard_constraints'],['rank_candidates']]
        if len(turns)<=3:return {"content":"","tool_calls":[{"id":f"bad-{len(turns)}-{i}","name":name,"args":{"context_ref":ref}} for i,name in enumerate(stages[len(turns)-1])]}
        return {"content":'I suggest these instead: {"listing_ids":["invented-id"]}',"tool_calls":[]}
    monkeypatch.setattr(LiveController,'invoke',invoke)
    service=make_service(tmp_path,ApplicationLiveController(),'invalid-proposal')
    try:
        # Use a known, satisfiable hard condition so ranking is nonempty and the
        # malicious final proposal actually reaches the final gate.
        req=RecommendationRequest.model_validate({**request().model_dump(),"requirements":{"listing_type":"RENT","criteria":[{"criterion_id":"budget","field":"price","operator":"lte","value":10000,"unit":"SGD/month","strength":"hard"}]}})
        result=service.recommend(req)
        assert result.status=='error' and result.error.code=='INVALID_PROPOSAL'
        assert not result.recommendations
        assert service.store.current(request().conversation_id) is None
        trace=service.store.trace(request().conversation_id)
        gates=[s for s in trace['steps'] if s['kind']=='final_gate']
        assert len(gates)==3 and all(not s['passed'] for s in gates)
    finally:service.close()


def test_tool_calls_are_never_rewritten_as_final_proposals(monkeypatch):
    original={"content":'{"listing_ids":[]}',"tool_calls":[{"id":"tool","name":"rank_candidates","args":{"context_ref":"ref"}}]}
    monkeypatch.setattr(LiveController,"invoke",lambda *_:original)
    result=ApplicationLiveController().invoke([{"role":"system","content":"original"}],[],10)
    assert result['tool_calls']==original['tool_calls']
    assert not result['app_output_protocol']['final_output_normalized']
