"""Real model, real SQL/KG/native recommendation; never sends external messages."""
from pathlib import Path
import json,time,uuid
import httpx

ROOT=Path(__file__).resolve().parents[1]
owner=str(uuid.uuid4())+str(uuid.uuid4())
client=httpx.Client(base_url='http://127.0.0.1:8010/api/',headers={'Authorization':'Bearer '+owner},timeout=480)
def api(path,body=None):
    r=client.get(path) if body is None else client.post(path,json=body)
    r.raise_for_status();return {} if r.headers.get('content-type','').startswith('text/event-stream') else r.json()
sid=api('sessions',{})['session_id']
(ROOT/'data/.runtime/requirement-model-session.json').write_text(json.dumps({'owner':owner,'session_id':sid}),encoding='utf-8')
report={'checks':[],'external_messages_sent':0,'queries':[]}
def state():return api('state?session_id='+sid)
def query(text,clarify=False):
    old=state();report['queries'].append(text)
    api('query',{'session_id':sid,'request_id':str(uuid.uuid4()),'query':text,**({'source_request_id':old['latest_request_id']} if old.get('latest_request_id') else {})})
    deadline=time.monotonic()+450
    while time.monotonic()<deadline:
        s=state()
        if s.get('messages') and s['messages'][-1]['role']=='assistant' and s['messages'][-1]['text'].startswith('本轮未完成'):raise AssertionError(s['messages'][-1]['text'])
        if clarify and s.get('pending'):return s
        if not clarify and s.get('latest_request_id')!=old.get('latest_request_id') and s.get('recommendation') and (s.get('explanation') or {}).get('source_request_id')==s['latest_request_id']:return s
        time.sleep(1)
    raise AssertionError('Workflow deadline exceeded')
try:
    s=query('你好，我要租南洋理工大学附近5公里内的房子，预算在2000新币每月，房子附近1公里内最好有MRT或公交站')
    criteria=s['requirements']['criteria']
    transport=next(c for c in criteria if c.get('target',{}).get('category')=='PublicTransport')
    assert transport['strength']=='soft' and transport['value']==1000
    university=next(c for c in criteria if c.get('target',{}).get('place_id')=='OSM:way:115105262' and c['strength']=='hard')
    assert university['value']==5000
    assert next(c for c in criteria if c['field']=='price')['value']==2000
    rec=s['recommendation'];assert rec['native']['provider']=='jiangshan-native'
    candidates=rec['recommendations']+rec['unverified_candidates'];assert candidates
    # Missing transport coverage remains unknown, rather than silently passing.
    for candidate in candidates:
        check=next(r for r in candidate['criterion_results'] if r['criterion_id']==transport['criterion_id'])
        assert check['status']=='unknown' and check['observed_value'] is None
    exchange=api('evidence?session_id='+sid+'&ref='+rec['native']['exchange_ref'])['data']
    assert any(c.get('category')=='PublicTransport' and c['value']==1000 and c['strength']=='soft' for c in exchange['request']['requirements']['criteria'])
    print('workflow check passed',flush=True);report['checks'].append('Original screenshot request retains budget, real NTU target, 1000m MRT-or-bus soft preference all the way to native recommendation; unavailable transit evidence remains unknown')
    version=s['requirements_version'];old_ids={c['criterion_id'] for c in criteria if c['field']!='price'}
    s=query('预算改为2300新币，其余条件保持不变。')
    assert s['requirements_version']==version+1
    assert old_ids.issubset({c['criterion_id'] for c in s['requirements']['criteria']})
    assert next(c for c in s['requirements']['criteria'] if c['field']=='price')['value']==2300
    print('workflow check passed',flush=True);report['checks'].append('Budget update preserves geographic relationships, soft preference and stable old criterion IDs')
    s=query('按当前条件重新推荐，不过这次最好房子附近700米内有公园。')
    assert any(c.get('target',{}).get('category')=='Park' and c['value']==700 and c['strength']=='soft' for c in s['requirements']['criteria'])
    assert any(c.get('target',{}).get('category')=='PublicTransport' for c in s['requirements']['criteria'])
    print('workflow check passed',flush=True);report['checks'].append('Rerecommendation with an appended new condition goes through model extraction and does not reuse away the change')
    previous=s['latest_request_id'];s=query('还需要两房。',clarify=True)
    assert s['latest_request_id']==previous
    s=query('至少两间卧室，不是恰好两间。')
    assert s['pending'] is None
    assert any(c['field']=='bedrooms' and c['operator']=='gte' and c['value']==2 for c in s['requirements']['criteria'])
    assert any(c.get('target',{}).get('category')=='PublicTransport' for c in s['requirements']['criteria'])
    assert any(c.get('target',{}).get('category')=='Park' for c in s['requirements']['criteria'])
    print('workflow check passed',flush=True);report['checks'].append('Ambiguous room count stops before recommendation; clarification resumes without dropping the budget or earlier transport/park preferences')
    report.update(status='passed',session_id=sid,requirements=s['requirements'],final_counts=s['recommendation']['state']['counts'])
except Exception as e:
    report.update(status='failed',error=str(e));raise
finally:
    (ROOT/'evaluation/reports/requirement-model-workflow.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');client.close()
print(json.dumps({'status':report['status'],'checks':len(report['checks']),'external_messages_sent':0}))
