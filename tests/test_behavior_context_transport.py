"""No provider calls: fixture repositories and original scripted controller."""
import json
import sqlite3
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient
from recommendation.app import app
from recommendation.app_contract import ApplicationRecommendationRequest


def test_app_handoff_archives_behavior_without_changing_native_ranking(monkeypatch,tmp_path):
    monkeypatch.setenv('RECOMMENDATION_SERVICE_TOKEN','test-context-only')
    monkeypatch.setenv('RECOMMENDATION_STATE_PATH',str(tmp_path/'native.sqlite'))
    monkeypatch.setenv('RECOMMENDATION_DATA_MODE','fixture')
    monkeypatch.setenv('RECOMMENDATION_CONTROLLER','scripted')
    context={'version':'1.0','as_of':'2026-10-23T00:00:00Z','listing_view_durations':[
        {'listing_id':'123','duration_seconds':12.345,'view_count':1,'last_viewed_at':'2026-10-22T23:00:00Z','source_request_ids':[str(uuid4())],'interest_eligible':False}],
        'book_viewing_listings':[{'listing_id':'123','action_id':str(uuid4()),'source_request_id':str(uuid4()),'requirements_version':1,'starts_at':'2026-10-24T06:00:00Z','ends_at':'2026-10-24T07:00:00Z','time_zone':'Asia/Singapore','recorded_at':'2026-10-22T23:00:00Z','signal':'viewing_draft_saved','appointment_confirmed':False}]}
    request={'request_id':'with-behavior','conversation_id':'app-context','requirements_version':1,'requirements':json.loads(Path('recommendation/examples/requirements.json').read_text()),'behavior_context':context}
    headers={'x-recommendation-token':'test-context-only'}
    with TestClient(app) as client:
        assert client.post('/recommend',json=request,headers=headers).status_code==422
        result=client.post('/app/recommend',json=request,headers=headers)
        assert result.status_code==200,result.text
        value=result.json();assert value['response']['status']=='success'
        assert value['transport_request']['behavior_context']==context
        assert 'behavior_context' not in value['request']
        assert client.post('/app/recommend',json=request,headers=headers).json()['response']==value['response']
        changed=json.loads(json.dumps(request));changed['behavior_context']['listing_view_durations'][0]['duration_seconds']=15
        assert client.post('/app/recommend',json=changed,headers=headers).status_code==409
        plain={k:v for k,v in request.items() if k!='behavior_context'};plain.update(request_id='without-behavior',conversation_id='other')
        control=client.post('/app/recommend',json=plain,headers=headers).json()
        assert [(r['listing']['listing_id'],r['scores']) for r in value['response']['recommendations']]==[(r['listing']['listing_id'],r['scores']) for r in control['response']['recommendations']]
    with sqlite3.connect(tmp_path/'native.sqlite') as db:
        persisted=json.loads(db.execute('SELECT request FROM app_transport_requests WHERE conversation_id=?',('app-context',)).fetchone()[0])
        assert persisted['behavior_context']==context
    bad=json.loads(json.dumps(request));bad['behavior_context']['book_viewing_listings'][0]['appointment_confirmed']=True
    try:
        ApplicationRecommendationRequest.model_validate(bad)
    except ValueError:
        pass
    else:
        raise AssertionError('draft signal must not become confirmed booking')
