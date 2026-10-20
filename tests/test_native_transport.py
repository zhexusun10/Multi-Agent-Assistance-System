"""Transport-only tests; native algorithm tests are kept under recommendation/tests."""
import json
from pathlib import Path
from fastapi.testclient import TestClient
from recommendation.app import app


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
