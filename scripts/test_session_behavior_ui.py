"""Real browser UI with intercepted business APIs; zero paid model calls."""
from pathlib import Path
from urllib.parse import urlparse
from copy import deepcopy
from uuid import uuid4
import json
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
fixture=json.loads((ROOT/'data/.runtime/refinement-ui-fixture.json').read_text(encoding='utf-8'))
sid=fixture['before']['session_id'];other=str(uuid4());new=str(uuid4())
state={'value':deepcopy(fixture['before']),'packets':[],'deletes':[],'query_posts':0}
errors=[];checks=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='chrome')
    context=browser.new_context(viewport={'width':1550,'height':1000})
    context.add_init_script('if(!localStorage.getItem("rental.owner")){localStorage.setItem("rental.owner","isolated-session-behavior-owner");localStorage.setItem("rental.session",'+json.dumps(sid)+');localStorage.setItem("rental.sessions",'+json.dumps(json.dumps([{'id':sid,'title':'当前找房对话'},{'id':other,'title':'旧的找房对话'}],ensure_ascii=False))+');}')
    def mock(route):
        req=route.request;path=urlparse(req.url).path
        if path=='/api/config':value={'dataset':fixture['dataset'],'today':'2026-10-23','action_mode':'simulation'}
        elif path=='/api/state':value=state['value']
        elif path=='/api/events':route.fulfill(status=200,content_type='text/event-stream',body=': test\n\n');return
        elif path=='/api/behavior/views':
            state['packets'].append(req.post_data_json);value={'status':'recorded','granted':True}
        elif path.startswith('/api/listings/') and path.endswith('/presentation'):
            listing=path.split('/')[3];candidate=next(c for c in state['value']['recommendation']['recommendations']+state['value']['recommendation']['unverified_candidates'] if c['listing']['listing_id']==listing)
            value={'listing_id':listing,'source_request_id':state['value']['latest_request_id'],'dataset':fixture['dataset'],'details':{**candidate['listing'],'bathrooms':2},'photos':[],'nearby':[],'warnings':[],'sections':{'details':'available','photos':'available','nearby':'available'},'photos_has_more':False}
        elif req.method=='DELETE' and path.startswith('/api/sessions/'):
            state['deletes'].append(path.split('/')[-1]);value={'status':'deleted'}
        elif path=='/api/sessions' and req.method=='POST':
            value=deepcopy(fixture['before']);value.update(session_id=new,requirements_version=0,requirements={'listing_type':'RENT','criteria':[]},recommendation=None,explanation=None,messages=[],actions=[],refinement_plans=[],latest_request_id=None,active_operation_id=None,pending_operations=[],last_event_id=0,revision=0);state['value']=value
        else:
            if path=='/api/query':state['query_posts']+=1
            errors.append(req.method+' '+path);route.abort();return
        route.fulfill(status=200,content_type='application/json',body=json.dumps(value,ensure_ascii=False))
    context.route('**/api/**',mock);context.route('https://**',lambda r:r.abort())
    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8010/');page.wait_for_load_state('networkidle');page.wait_for_function('()=>document.body.dataset.ready==="true"')
    cards=page.locator('.listing-card');ids=cards.evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)')
    cards.first.get_by_role('button',name='展开详情',exact=False).click()
    page.wait_for_function('()=>document.querySelector("#property-detail").dataset.readingState==="active"')
    page.wait_for_timeout(1250)
    page.evaluate('window.dispatchEvent(new Event("blur"))')
    page.wait_for_function('()=>document.querySelector("#property-detail").dataset.readingState==="paused"')
    page.wait_for_timeout(250)
    completed=lambda:[x for x in state['packets'] if x['kind']=='complete']
    assert completed() and completed()[-1]['active_duration_ms']>0
    count=len([x for x in state['packets'] if x['kind']!='exposure']);page.wait_for_timeout(1200);assert len([x for x in state['packets'] if x['kind']!='exposure'])==count
    checks.append('detail visible/focused starts; blur/covered-window event stops and sends accepted milliseconds')
    page.evaluate('window.dispatchEvent(new Event("focus"))')
    page.wait_for_function('()=>document.querySelector("#property-detail").dataset.readingState==="active"');page.wait_for_timeout(1100)
    page.evaluate('Object.defineProperty(document,"hidden",{configurable:true,value:true});document.dispatchEvent(new Event("visibilitychange"))')
    page.wait_for_function('()=>document.querySelector("#property-detail").dataset.readingState==="paused"');page.wait_for_timeout(250)
    count=len([x for x in state['packets'] if x['kind']!='exposure']);page.wait_for_timeout(1200);assert len([x for x in state['packets'] if x['kind']!='exposure'])==count
    checks.append('hidden/minimized/tab-background visibility event excludes hidden interval')
    page.evaluate('delete document.hidden;document.dispatchEvent(new Event("visibilitychange"));window.dispatchEvent(new Event("focus"))')
    page.wait_for_function('()=>document.querySelector("#property-detail").dataset.readingState==="active"')
    page.evaluate('document.querySelector("#property-detail").style.transform="translateX(200vw)"')
    page.wait_for_function('()=>document.querySelector("#property-detail").dataset.readingState==="paused"')
    checks.append('detail outside visible viewport does not accrue time')
    page.evaluate('document.querySelector("#property-detail").style.transform=""')
    page.get_by_role('button',name='← 返回推荐列表',exact=True).click();page.wait_for_timeout(250)
    assert cards.evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)')==ids
    count=len([x for x in state['packets'] if x['kind']!='exposure']);page.wait_for_timeout(1100);assert len([x for x in state['packets'] if x['kind']!='exposure'])==count
    checks.append('return completes detail interval; list/exposure never becomes duration; displayed order remains fixed')
    menu=page.get_by_role('button',name='对话操作：旧的找房对话');menu.click()
    expect(page.get_by_role('menuitem',name='删除对话')).to_be_visible()
    page.screenshot(path=str(ROOT/'evaluation/reports/session-delete-menu.png'))
    assert page.get_by_role('menuitem',name='删除对话').evaluate('(e)=>getComputedStyle(e).color')=='rgb(183, 43, 43)'
    page.get_by_role('menuitem',name='删除对话').click();expect(page.locator('.session-row')).to_have_count(1)
    assert state['deletes']==[other];assert page.evaluate('localStorage.getItem("rental.session")')==sid
    checks.append('arrow opens red delete button; deleting inactive chat never selects it')
    page.reload();page.wait_for_load_state('networkidle');expect(page.locator('.session-row')).to_have_count(1)
    page.get_by_role('button',name='对话操作：当前找房对话').click();page.get_by_role('menuitem',name='删除对话').click()
    page.wait_for_function('(id)=>localStorage.getItem("rental.session")===id',arg=new)
    expect(page.locator('.session-row')).to_have_count(1)
    assert state['deletes']==[other,sid];assert state['query_posts']==0
    checks.append('delete persists after refresh; deleting last active chat creates clean conversation without a model query')
    assert not errors,errors
    browser.close()
report={'status':'passed','model_calls':0,'query_posts':state['query_posts'],'checks':checks,'complete_packets':len(completed()),'visibility_test':'real browser event handlers; hidden/blur properties simulated, no claim to measure eye attention'}
(ROOT/'evaluation/reports/session-behavior-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
