"""One submission must update the UI, with real LLM output and lost SSE recovery."""
from pathlib import Path
from datetime import datetime,timezone
import json,time,hashlib
import httpx
from playwright.sync_api import sync_playwright,TimeoutError

ROOT=Path(__file__).resolve().parents[1]
fixture=json.loads((ROOT/'data/.runtime/requirement-update-session.json').read_text(encoding='utf-8'))
client=httpx.Client(base_url='http://127.0.0.1:8010/api/',headers={'Authorization':'Bearer '+fixture['owner']},timeout=30)
def state():
    r=client.get('state',params={'session_id':fixture['session_id']});r.raise_for_status()
    assert r.headers['cache-control']=='no-store'
    return r.json()
paths=['frontend/app.js','frontend/session-sync.js','api/main.py','src/stores/domain.ts']
source_hashes={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in paths}
report={'cases':[],'external_messages_sent':0,'source_hashes':source_hashes}
errors=[]
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,channel='chrome')
        for mode in ['normal','lost-completion','transient-state-failure']:
            context=browser.new_context(viewport={'width':1600,'height':1000})
            context.add_init_script('const f='+json.dumps(fixture)+';localStorage.setItem("rental.owner",f.owner);localStorage.setItem("rental.session",f.session_id);')
            if mode=='lost-completion':
                # Leave the connection open and heartbeats flowing, but withhold
                # every completion. Reconnecting alone cannot rescue this case.
                context.add_init_script('''const realFetch=window.fetch.bind(window);
                  window.fetch=(input,options)=>{
                    if(String(input).startsWith('/api/events?')){
                      const encoder=new TextEncoder();let timer;
                      const body=new ReadableStream({start(c){c.enqueue(encoder.encode(': heartbeat\\n\\n'));timer=setInterval(()=>c.enqueue(encoder.encode(': heartbeat\\n\\n')),500);},cancel(){clearInterval(timer);}});
                      return Promise.resolve(new Response(body,{headers:{'content-type':'text/event-stream'}}));
                    }return realFetch(input,options);
                  };''')
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            posts=[];state_reads=[];failed={'count':0,'armed':False}
            page.on('request',lambda r:posts.append(r.url) if r.url.endswith('/api/query') and r.method=='POST' else None)
            page.on('request',lambda r:state_reads.append(time.monotonic()) if '/api/state?' in r.url else None)
            if mode=='transient-state-failure':
                def route_state(route):
                    response=route.fetch();body=response.json()
                    if failed['armed'] and not body.get('pending_operations') and failed['count']==0:
                        failed['count']+=1;route.fulfill(status=503,json={'detail':'Injected transient state read failure'})
                    else:route.fulfill(response=response)
                page.route('**/api/state?*',route_state)
            page.goto('http://127.0.0.1:8010/')
            try:page.wait_for_load_state('networkidle',timeout=2000)
            except TimeoutError:pass
            page.wait_for_selector('body[data-ready=true]')
            before=state();assistant_count=page.locator('#messages .assistant').count()
            page.evaluate('window.__oldMessage=document.querySelector("#messages .message")')
            text='解释一下为什么做出这样的推荐？'
            page.locator('#messages').evaluate('(e)=>e.scrollTop=e.scrollHeight')
            started=time.monotonic();failed['armed']=True
            page.locator('#query').fill(text);page.locator('#send').click()
            deadline=time.monotonic()+130;finished=None;current=None
            while time.monotonic()<deadline:
                current=state()
                if len(current['messages'])>=len(before['messages'])+2 and current['messages'][-1]['role']=='assistant' and not current.get('pending_operations'):
                    assert not current['messages'][-1]['text'].startswith('本轮未完成'),current['messages'][-1]['text']
                    finished=current['messages'][-1];break
                page.wait_for_timeout(150)
            assert finished,'Backend explanation did not complete'
            page.wait_for_function('(n)=>document.querySelectorAll("#messages .assistant").length>n',arg=assistant_count,timeout=15000)
            answer=page.locator('#messages .assistant .message-text').last.inner_text()
            assert answer==finished['text']
            lag=(datetime.now(timezone.utc)-datetime.fromisoformat(finished['at'].replace('Z','+00:00'))).total_seconds()
            assert lag<8,('Completed answer stayed invisible too long',lag)
            assert len(posts)==1,'Recovery must not submit another user query'
            assert current['recommendation']==before['recommendation']
            assert current['requirements']==before['requirements']
            assert current['explanation']['generation']['mode']=='llm_grounded'
            assert all(label in answer for label in ['您的要求：','符合的部分：','还需要了解的部分：','注意：'])
            assert not any(label in answer for label in ['待确认选项','没有可比较的排序依据','未评分'])
            scroll=page.locator('#messages').evaluate('(e)=>{const last=e.lastElementChild,box=e.getBoundingClientRect(),r=last.getBoundingClientRect();return {top:e.scrollTop,height:e.scrollHeight,client:e.clientHeight,answer_top:r.top-box.top,answer_bottom:r.bottom-box.top}}')
            report['scroll_debug']=scroll
            page.screenshot(path=str(ROOT/'evaluation/reports/explanation-delivery-debug.png'))
            assert -1<=scroll['answer_top']<scroll['client'],('The beginning of the new answer should be visible',scroll)
            assert page.evaluate('window.__oldMessage.isConnected'),'Existing messages must not be remounted'
            # Reading earlier messages should survive reconnect/state refresh.
            position=page.locator('#messages').evaluate('(e)=>{e.scrollTo({top:Math.max(0,e.scrollTop-180),behavior:"instant"});return e.scrollTop;}')
            page.evaluate('window.dispatchEvent(new Event("online"))');page.wait_for_timeout(800)
            assert abs(page.locator('#messages').evaluate('(e)=>e.scrollTop')-position)<2,'A background refresh moved the reading position'
            page.locator('#messages .assistant').last.evaluate('(e)=>{const box=e.parentElement;box.scrollTo({top:box.scrollTop+e.getBoundingClientRect().top-box.getBoundingClientRect().top,behavior:"instant"});}')
            if mode=='transient-state-failure':assert failed['count']==1
            report['cases'].append({'mode':mode,'query_posts':len(posts),'state_reads':len(state_reads),'time_to_display_s':round(time.monotonic()-started,2),'completion_to_display_s':round(lag,2),'answer':answer,'generation':current['explanation']['generation'],'transient_failures':failed['count']})
            if mode=='normal':
                page.screenshot(path=str(ROOT/'evaluation/reports/explanation-layout-desktop.png'))
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.locator('#messages .assistant').last.scroll_into_view_if_needed()
                page.screenshot(path=str(ROOT/'evaluation/reports/explanation-layout-mobile.png'))
            print(mode+' passed',flush=True);context.close()
        assert not errors,errors
        browser.close()
    assert source_hashes=={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in paths},'Sources changed during delivery tests'
    report.update(status='passed',browser_errors=errors)
except Exception as e:
    report.update(status='failed',error=str(e),browser_errors=errors)
    raise
finally:
    (ROOT/'evaluation/reports/explanation-delivery-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    client.close()
