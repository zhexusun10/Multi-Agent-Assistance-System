"""Regression for the user's D05 search: fresh UI submission and explanation."""
from pathlib import Path
from uuid import uuid4
from datetime import datetime,timezone
import hashlib,json,time
import httpx
from playwright.sync_api import sync_playwright,TimeoutError

ROOT=Path(__file__).resolve().parents[1]
owner=str(uuid4())+str(uuid4())
report={'checked_at':datetime.now(timezone.utc).isoformat(),'checks':[],'external_messages_sent':0}
paths=['src/agents/explanation/contracts.ts','src/agents/explanation/evidence.ts','src/agents/explanation/validation.ts','frontend/app.js','frontend/session-sync.js']
report['source_hashes']={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}
errors=[]
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,channel='chrome')
        context=browser.new_context(viewport={'width':1600,'height':1000})
        context.add_init_script('localStorage.setItem("rental.owner",'+json.dumps(owner)+');localStorage.removeItem("rental.session");')
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        posts=[]
        page.on('request',lambda r:posts.append(r.url) if r.url.endswith('/api/query') and r.method=='POST' else None)
        page.goto('http://127.0.0.1:8010/')
        try:page.wait_for_load_state('networkidle',timeout=2000)
        except TimeoutError:pass
        page.wait_for_selector('body[data-ready=true]')
        assert page.locator('#query').count()==1 and page.locator('#send').count()==1
        sid=page.evaluate('localStorage.getItem("rental.session")')
        assert sid
        (ROOT/'data/.runtime/explanation-district-browser-session.json').write_text(json.dumps({'owner':owner,'session_id':sid}),encoding='utf-8')
        client=httpx.Client(base_url='http://127.0.0.1:8010/api/',headers={'Authorization':'Bearer '+owner},timeout=30)
        def state():
            r=client.get('state',params={'session_id':sid});r.raise_for_status();return r.json()
        for query in ['帮我找租房，预算不超过5000新币，至少2房，D05','解释一下为什么推荐这几套房子？']:
            before=state();count=page.locator('#messages .assistant').count();start=time.monotonic()
            page.locator('#query').fill(query);page.locator('#send').click()
            deadline=time.monotonic()+480;current=None
            while time.monotonic()<deadline:
                current=state()
                if len(current['messages'])>=len(before['messages'])+2 and current['messages'][-1]['role']=='assistant' and not current.get('pending_operations'):
                    break
                page.wait_for_timeout(250)
            else:raise AssertionError('Search/explanation did not finish')
            answer=current['messages'][-1]['text']
            assert not answer.startswith('本轮未完成'),answer
            # The empty-session welcome bubble is replaced by the first real
            # reply, so assistant count need not increase on a fresh search.
            page.wait_for_function('(text)=>Array.from(document.querySelectorAll("#messages .assistant .message-text")).some(e=>e.innerText===text)',arg=answer,timeout=10000)
            assert page.locator('#messages .assistant .message-text').last.inner_text()==answer
            assert 'D05' in answer and all(s in answer for s in ['您的要求：','符合的部分：','还需要了解的部分：','注意：'])
            assert current['explanation']['generation']['mode']=='llm_grounded'
            assert current['explanation']['generation']['semantic_audit']=='passed'
            assert len(current['recommendation']['recommendations'])==4 and len(current['recommendation']['unverified_candidates'])==4
            if before.get('recommendation'):
                assert before['recommendation']==current['recommendation']
                assert before['requirements']==current['requirements']
            assert not any(s in answer for s in ['GROUNDING','criterion_id','support_refs','native:'])
            lag=(datetime.now(timezone.utc)-datetime.fromisoformat(current['messages'][-1]['at'].replace('Z','+00:00'))).total_seconds()
            assert lag<8,lag
            report['checks'].append({'query':query,'answer':answer,'generation':current['explanation']['generation'],'completion_to_display_s':round(lag,2),'total_s':round(time.monotonic()-start,2)})
            print('D05 UI query passed',flush=True)
        assert len(posts)==2,'Each user question must submit only once'
        page.locator('#messages .assistant').last.evaluate('(e)=>{const box=e.parentElement;box.scrollTo({top:box.scrollTop+e.getBoundingClientRect().top-box.getBoundingClientRect().top,behavior:"instant"});}')
        page.screenshot(path=str(ROOT/'evaluation/reports/explanation-district-desktop.png'))
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.locator('#messages .assistant').last.scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/'evaluation/reports/explanation-district-mobile.png'))
        assert not errors,errors
        browser.close();client.close()
    assert report['source_hashes']=={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},'Sources changed during UI verification'
    report.update(status='passed',browser_errors=errors,query_posts=len(posts))
except Exception as e:
    report.update(status='failed',error=str(e),browser_errors=errors)
    try:
        report['visible_messages']=page.locator('#messages').inner_text()
        page.screenshot(path=str(ROOT/'evaluation/reports/explanation-district-ui-failure.png'))
    except Exception:pass
finally:
    (ROOT/'evaluation/reports/explanation-district-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':report['status'],'checks':len(report['checks']),'error':report.get('error')},ensure_ascii=False))
if report['status']!='passed':raise SystemExit(1)
