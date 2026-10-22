"""Fresh real searches: unresolved hard geography, no matches, valid ranked prefix."""
from pathlib import Path
from collections import Counter
import json,time,uuid,sqlite3,sys
import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
resume='--resume' in sys.argv
saved=json.loads((ROOT/'data/.runtime/recommendation-empty-prefix-session.json').read_text(encoding='utf-8')) if resume else None
owner=saved['owner'] if saved else str(uuid.uuid4())+str(uuid.uuid4())
client=httpx.Client(base_url='http://127.0.0.1:8010/api/',headers={'Authorization':'Bearer '+owner},timeout=480)
def api(path,body=None):
    response=client.get(path) if body is None else client.post(path,json=body)
    response.raise_for_status()
    return {} if response.headers.get('content-type','').startswith('text/event-stream') else response.json()

prior=json.loads((ROOT/'evaluation/reports/recommendation-empty-prefix-workflow.json').read_text(encoding='utf-8')) if resume else None
report={'external_messages_sent':0,'cases':[c for c in prior['cases'] if c['status']=='passed'] if prior else []}
values=dotenv_values(ROOT/'.env');db_path=Path(values.get('RECOMMENDATION_STATE_PATH') or ROOT/'data/.runtime/recommendation/state.sqlite')
cases=[
    ('jurong-east-1b1b','你好，我要租裕廊东附近5公里内的房子，预算在2500新币每月，房子附近1公里内最好有MRT或公交站，要求房型为1B1B'),
    ('no-matching-bedrooms','我要租D01的房子，至少5间卧室，预算每月不超过2000新币。'),
    ('known-geography','我要租新加坡国立大学附近5公里内的房子，预算在2000新币每月。'),
]
try:
    for name,query in cases:
        if any(c['name']==name for c in report['cases']):continue
        sid=api('sessions',{})['session_id'];before=time.monotonic()
        if name=='jurong-east-1b1b':(ROOT/'data/.runtime/recommendation-empty-prefix-session.json').write_text(json.dumps({'owner':owner,'session_id':sid}),encoding='utf-8')
        api('query',{'query':query,'session_id':sid,'request_id':str(uuid.uuid4())})
        deadline=time.monotonic()+450
        while time.monotonic()<deadline:
            state=api('state?session_id='+sid)
            messages=state.get('messages',[])
            if messages and messages[-1]['role']=='assistant' and messages[-1]['text'].startswith('本轮未完成'):raise AssertionError(messages[-1]['text'])
            if state.get('pending'):raise AssertionError('Unexpected intake clarification: '+str(state['pending'].get('clarifications')))
            if state.get('recommendation') and (state.get('explanation') or {}).get('source_request_id')==state.get('latest_request_id'):break
            time.sleep(.7)
        else:raise AssertionError('Workflow deadline exceeded')
        rec=state['recommendation'];exchange=api('evidence?session_id='+sid+'&ref='+rec['native']['exchange_ref'])['data'];native=exchange['response']
        assert rec['status']!='error' and native['status']!='error'
        with sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            raw=db.execute('SELECT content FROM traces WHERE conversation_id=? AND trace_id=?',(sid,native['execution']['trace_id'])).fetchone()
        trace=json.loads(raw[0]);tools=[s['result']['tool'] for s in trace['steps'] if s['kind']=='tool']
        assert Counter(tools)==Counter(['retrieve_listings','get_geographic_evidence','validate_hard_constraints','rank_candidates']),tools
        assert any(s['kind']=='final_gate' and s.get('passed') for s in trace['steps'])
        assert native['execution']['model_calls']<=8 and native['execution']['tool_calls']==4
        assert 'Model turn or time budget exhausted' not in state['messages'][-1]['text']
        criteria=state['requirements']['criteria']
        if name=='jurong-east-1b1b':
            for field,value in [('bathrooms',1),('bedrooms',1),('price',2500)]:assert any(c['field']==field and c['value']==value and c['strength']=='hard' for c in criteria)
            assert any(c.get('target',{}).get('place_id')=='URA:AREA:JURONG_EAST' and c['strength']=='hard' and c['value']==5000 for c in criteria)
            assert any(c.get('target',{}).get('category')=='PublicTransport' and c['strength']=='soft' and c['value']==1000 for c in criteria)
            assert not rec['recommendations'] and rec['unverified_candidates']
            for item in rec['unverified_candidates']:
                for c in criteria:
                    r=next(r for r in item['criterion_results'] if r['criterion_id']==c['criterion_id'])
                    if c['field']=='bathrooms' or c.get('target',{}).get('place_id')=='URA:AREA:JURONG_EAST':assert r['status']=='unknown'
        elif name=='no-matching-bedrooms':assert not rec['recommendations'] and not rec['unverified_candidates'] and rec['status']=='no_match'
        else:
            assert rec['recommendations'];assert [c['listing']['listing_id'] for c in rec['recommendations']]==[c['listing']['listing_id'] for c in native['recommendations']]
        report['cases'].append({'name':name,'query':query,'status':'passed','duration_seconds':round(time.monotonic()-before,2),'requirements':state['requirements'],'answer':state['messages'][-1]['text'],'app_status':rec['status'],'native_status':native['status'],'verified':len(rec['recommendations']),'unverified':len(rec['unverified_candidates']),'execution':native['execution'],'tools':tools,'normalized_model_outputs':sum(bool(s.get('output',{}).get('app_output_protocol',{}).get('final_output_normalized')) for s in trace['steps'])})
        print(name+' passed',flush=True)
    report['status']='passed'
except Exception as error:
    report.update(status='failed',error=str(error));raise
finally:
    (ROOT/'evaluation/reports/recommendation-empty-prefix-workflow.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');client.close()
print(json.dumps({'status':report['status'],'cases':len(report['cases']),'external_messages_sent':0}))
