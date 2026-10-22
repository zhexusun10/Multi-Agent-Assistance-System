"""Real multiround search regression; isolated owner and session, no external sends."""
from pathlib import Path
import json,time,uuid,sys
import httpx
from playwright.sync_api import sync_playwright,expect,TimeoutError

ROOT=Path(__file__).resolve().parents[1]
reuse=json.loads((ROOT/'data/.runtime/requirement-update-session.json').read_text(encoding='utf-8')) if '--replay-update-only' in sys.argv else None
owner=reuse['owner'] if reuse else uuid.uuid4().hex+uuid.uuid4().hex
client=httpx.Client(base_url='http://127.0.0.1:8010/api/',headers={'Authorization':'Bearer '+owner},timeout=480)
report={'external_messages_sent':0,'cases':[]}

def api(path,body=None):
    response=client.get(path) if body is None else client.post(path,json=body)
    response.raise_for_status()
    return {} if response.headers.get('content-type','').startswith('text/event-stream') else response.json()

def state():return api('state?session_id='+sid)

def query(name,text):
    before=state();started=time.monotonic()
    api('query',{'query':text,'session_id':sid,'request_id':str(uuid.uuid4()),**({'source_request_id':before['latest_request_id']} if before.get('latest_request_id') else {})})
    deadline=time.monotonic()+450
    while time.monotonic()<deadline:
        current=state()
        messages=current.get('messages',[])
        if messages and messages[-1]['role']=='assistant' and messages[-1]['text'].startswith('本轮未完成'):raise AssertionError(messages[-1]['text'])
        if current.get('pending'):raise AssertionError('Unexpected clarification: '+str(current['pending'].get('clarifications')))
        request=current.get('latest_request_id')
        if request and request!=before.get('latest_request_id') and (current.get('explanation') or {}).get('source_request_id')==request:break
        time.sleep(.7)
    else:raise AssertionError('Workflow deadline exceeded')
    assert current['recommendation']['status']!='error'
    assert len({c['criterion_id'] for c in current['requirements']['criteria']})==len(current['requirements']['criteria'])
    report['cases'].append({'name':name,'query':text,'requirements':current['requirements'],'requirements_version':current['requirements_version'],'request_id':request,'answer':messages[-1]['text'],'duration_seconds':round(time.monotonic()-started,2)})
    print(name+' completed',flush=True)
    return current

def has(r,field,value):return any(c['field']==field and c['value']==value for c in r['requirements']['criteria'])
def transit(r):return [c for c in r['requirements']['criteria'] if c.get('target',{}).get('category')=='PublicTransport']
def area(r,id):return [c for c in r['requirements']['criteria'] if c.get('target',{}).get('place_id')==id]

def screenshot_update_ui():
    errors=[];checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,channel='chrome')
        for name,options in [('desktop',{'viewport':{'width':1600,'height':1000}}),('mobile',{'viewport':{'width':390,'height':844},'is_mobile':True,'has_touch':True})]:
            context=browser.new_context(**options)
            context.add_init_script('const f='+json.dumps({'owner':owner,'session_id':sid})+';localStorage.setItem("rental.owner",f.owner);localStorage.setItem("rental.session",f.session_id);')
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto('http://127.0.0.1:8010/')
            try:page.wait_for_load_state('networkidle',timeout=2000)
            except TimeoutError:pass
            page.wait_for_selector('body[data-ready=true]')
            for text in ['2500','JURONG EAST','卫浴','MRT']:expect(page.locator('#criteria')).to_contain_text(text)
            expect(page.locator('.listing-card')).to_have_count(4)
            messages=page.locator('#messages').inner_text()
            assert '那我修改要求' in messages and '本轮未完成' not in messages
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            if name=='mobile':page.locator('.results').scroll_into_view_if_needed()
            page.screenshot(path=str(ROOT/f'evaluation/reports/requirement-update-{name}.png'))
            checks.append(name+': screenshot update completes, all prior conditions retained, four pending cards, no error/overflow');context.close()
        browser.close()
    assert not errors,errors
    report['ui']={'status':'passed','checks':checks,'browser_errors':errors}

try:
    sid=reuse['session_id'] if reuse else api('sessions',{})['session_id'];report['session_id']=sid
    (ROOT/'data/.runtime/requirement-update-session.json').write_text(json.dumps({'owner':owner,'session_id':sid}),encoding='utf-8')
    s=state() if reuse else query('initial','你好，我要租裕廊东附近5公里内的房子，预算在2500新币每月，房子附近1公里内最好有MRT或公交站，要求房型为1B1B')
    assert has(s,'bedrooms',1) and has(s,'bathrooms',1) and has(s,'price',2500)
    previous=s['requirements'];version=s['requirements_version']
    s=query('screenshot-update','那我修改要求，我要租裕廊东附近5公里内的房子，预算在2500新币每月，房子附近1公里内最好有MRT或公交站，')
    assert s['requirements']==previous and s['requirements_version']==version
    assert len(transit(s))==1 and transit(s)[0]['value']==1000
    report['screenshot_state']=s
    screenshot_update_ui()
    if '--screenshot-only' not in sys.argv and not reuse:
        s=query('cancel-layout','不用1B1B了，卧室和卫生间数量都不作要求，其他条件照旧。')
        assert not any(c['field'] in ['bedrooms','bathrooms'] for c in s['requirements']['criteria'])
        assert has(s,'price',2500) and len(transit(s))==1 and area(s,'URA:AREA:JURONG_EAST')
        s=query('replace-area','不找裕廊东了，改为新加坡国立大学附近5公里内，预算和交通偏好保持不变。')
        assert not area(s,'URA:AREA:JURONG_EAST')
        assert any(c.get('target',{}).get('name')=='National University of Singapore' and c['strength']=='hard' and c['value']==5000 for c in s['requirements']['criteria'])
        assert has(s,'price',2500) and len(transit(s))==1
    report.update(status='passed',final_requirements=s['requirements'])
except Exception as error:
    report.update(status='failed',error=str(error));raise
finally:
    # Keep only the required screenshot facts, not the complete recommendation archive.
    if 'screenshot_state' in report:
        ss=report.pop('screenshot_state');report['screenshot_result']={'requirements':ss['requirements'],'answer':ss['messages'][-1]['text'],'verified':len(ss['recommendation']['recommendations']),'unverified':len(ss['recommendation']['unverified_candidates'])}
    (ROOT/'evaluation/reports/requirement-update-workflow.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    client.close()
print(json.dumps({'status':report['status'],'cases':len(report['cases']),'external_messages_sent':0}))
