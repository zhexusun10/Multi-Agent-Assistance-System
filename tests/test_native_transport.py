"""Transport-only tests; native algorithm tests are kept under recommendation/tests."""
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from recommendation.app import app
from recommendation.tests.isolation import isolate_runtime


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch, tmp_path):
    """Fixture data mode, scripted controller, private state file; live state/connections refused."""
    isolate_runtime(monkeypatch, tmp_path)


def test_native_http_auth_replay_and_presentation(monkeypatch, tmp_path):
    monkeypatch.setenv('RECOMMENDATION_SERVICE_TOKEN', 'test-only-credential')
    monkeypatch.setenv('RECOMMENDATION_STATE_PATH', str(tmp_path/'state.sqlite'))
    monkeypatch.setenv('RECOMMENDATION_DATA_MODE', 'fixture')
    monkeypatch.setenv('RECOMMENDATION_CONTROLLER', 'scripted')
    request = {'request_id':'native-transport','conversation_id':'transport-session','requirements_version':1,'requirements':json.loads(Path('recommendation/examples/requirements.json').read_text())}
    with TestClient(app) as client:
        assert client.get('/health').json()['provider'] == 'jiangshan-native'
        assert client.post('/recommend',json=request).status_code == 401
        headers={'x-recommendation-token':'test-only-credential'}
        response=client.post('/recommend',json=request,headers=headers)
        assert response.status_code==200 and response.json()['status']=='success'
        replay=client.post('/app/recommend',json=request,headers=headers).json()
        assert replay['response']==response.json()
        assert len(replay['presentation']['listings'])>=4
        counts=replay['presentation']['counts']
        assert counts['assessed']==sum(counts[k] for k in ['eligible','unverified','excluded'])
        conflict={**request,'result_limit':1}
        failed=client.post('/recommend',json=conflict,headers=headers).json()
        assert failed['error']['code']=='IDEMPOTENCY_CONFLICT'
        stale={**request,'request_id':'stale','previous_state_id':'unknown'}
        assert client.post('/recommend',json=stale,headers=headers).json()['error']['code']=='STALE_REFERENCE'
        assert client.post('/recommend',json={**request,'unexpected':1},headers=headers).status_code==422
        # A superseded application operation may leave the UI's previous state
        # behind the native head. Only /app/recommend resolves that reference;
        # /recommend above must continue to enforce the upstream contract.
        next_request={**request,'request_id':'app-next','previous_state_id':None}
        adapted=client.post('/app/recommend',json=next_request,headers=headers).json()
        assert adapted['response']['status']=='success'
        assert adapted['request']['previous_state_id']==response.json()['state']['state_id']
        assert adapted['transport_request']['previous_state_id'] is None
        assert client.post('/app/recommend',json=next_request,headers=headers).json()['response']==adapted['response']
        assert client.post('/app/recommend',json={**next_request,'result_limit':1},headers=headers).status_code==409
        direct_only={**request,'request_id':'direct-only','conversation_id':'separate-direct-conversation'}
        assert client.post('/recommend',json=direct_only,headers=headers).json()['status']=='success'
        conflict=client.post('/app/recommend',json={**direct_only,'result_limit':1},headers=headers).json()
        assert conflict['response']['error']['code']=='IDEMPOTENCY_CONFLICT'
        assert conflict['presentation']=={'listings':[],'counts':None}


def test_transport_rejects_2_0_requests_and_maps_legacy_or_unknown_state_to_fresh_chain(monkeypatch, tmp_path):
    """R7 conversions at the transport boundary: a contract 1.0 body is a 422 (UNSUPPORTED_VERSION);
    on /app/recommend a previous_state_id that only resolves to a legacy v1 run, or to nothing,
    becomes null and the request starts a fresh v2 chain (no STALE_REFERENCE); /recommend keeps
    the native optimistic check."""
    from recommendation.state.store import StateStore
    path = tmp_path/'state.sqlite'
    store = StateStore(path)
    store.db.execute("INSERT INTO conversations VALUES('legacy-session','legacy-state-7',7)")
    store.db.commit(); store.close()
    monkeypatch.setenv('RECOMMENDATION_SERVICE_TOKEN', 'test-only-credential')
    monkeypatch.setenv('RECOMMENDATION_STATE_PATH', str(path))
    monkeypatch.setenv('RECOMMENDATION_DATA_MODE', 'fixture')
    monkeypatch.setenv('RECOMMENDATION_CONTROLLER', 'scripted')
    headers = {'x-recommendation-token': 'test-only-credential'}
    requirements = json.loads(Path('recommendation/examples/requirements.json').read_text())
    base = {'contract_version': '1.0', 'conversation_id': 'legacy-session', 'requirements_version': 1, 'requirements': requirements}
    with TestClient(app) as client:
        # The request keeps the main orchestrator contract 1.0; a 2.0 request body is rejected.
        old = client.post('/app/recommend', json={**base, 'request_id': 'old', 'contract_version': '2.0'}, headers=headers)
        assert old.status_code == 422 and 'UNSUPPORTED_VERSION' in old.text
        direct = client.post('/recommend', json={**base, 'request_id': 'direct', 'previous_state_id': 'legacy-state-7'}, headers=headers).json()
        assert direct['error']['code'] == 'STALE_REFERENCE'
        fresh = client.post('/app/recommend', json={**base, 'request_id': 'fresh', 'previous_state_id': 'legacy-state-7'}, headers=headers).json()
        assert fresh['response']['status'] == 'success' and fresh['response']['state']['run_sequence'] == 1
        assert fresh['request']['previous_state_id'] is None and fresh['transport_request']['previous_state_id'] == 'legacy-state-7'
        unknown = client.post('/app/recommend', json={**base, 'conversation_id': 'never-seen', 'request_id': 'u', 'previous_state_id': 'nothing'}, headers=headers).json()
        assert unknown['response']['status'] == 'success' and unknown['request']['previous_state_id'] is None
        chained = client.post('/app/recommend', json={**base, 'request_id': 'next', 'previous_state_id': 'legacy-state-7'}, headers=headers).json()
        assert chained['request']['previous_state_id'] == fresh['response']['state']['state_id']
        assert chained['response']['state']['run_sequence'] == 2
    store = StateStore(path)
    assert store.legacy_head('legacy-session') == ('legacy-state-7', 7)
    store.close()
