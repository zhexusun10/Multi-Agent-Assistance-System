"""Submit an explanation through the real chat UI, then inspect desktop/mobile."""
from pathlib import Path
import json,time,re,sys
import httpx
from playwright.sync_api import sync_playwright,TimeoutError
ROOT=Path(__file__).resolve().parents[1]
fixture=json.loads((ROOT/'data/.runtime/explanation-model-session.json').read_text(encoding='utf-8'))
client=httpx.Client(base_url='http://127.0.0.1:8010/api/',headers={'Authorization':'Bearer '+fixture['owner']},timeout=30)
def state():
    r=client.get('state',params={'session_id':fixture['session_id']});r.raise_for_status();return r.json()
before=state();query='给我解释一下为什么推荐这几套房子？'
read_only='--read-only' in sys.argv
report={'checks':[],'external_messages_sent':0,'read_only':read_only}
if read_only:
    workflow=json.loads((ROOT/'evaluation/reports/explanation-model-workflow.json').read_text(encoding='utf-8'))
    report['answer']=next(row['answer'] for row in workflow['checks'] if row['query']==query and row['operation']['status']=='completed')
    report['characters']=len(report['answer'])
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='chrome');errors=[]
    for name,options in [('desktop',{'viewport':{'width':1600,'height':1000}}),('mobile',{'viewport':{'width':390,'height':844},'is_mobile':True,'has_touch':True})]:
        context=browser.new_context(**options)
        context.add_init_script('const f='+json.dumps(fixture)+';localStorage.setItem("rental.owner",f.owner);localStorage.setItem("rental.session",f.session_id);')
        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)));page.goto('http://127.0.0.1:8010/')
        try:page.wait_for_load_state('networkidle',timeout=2000)
        except TimeoutError:pass
        page.wait_for_selector('body[data-ready=true]')
        if name=='desktop' and not read_only:
            page.locator('#query').fill(query);page.locator('#send').click()
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                current=state()
                if len(current['messages'])>=len(before['messages'])+2 and current['messages'][-1]['role']=='assistant':break
                page.wait_for_timeout(300)
            else:raise AssertionError('UI explanation timed out')
            assert current['explanation']['generation']['mode']=='llm_grounded'
            assert current['recommendation']==before['recommendation']
            assert current['latest_request_id']==before['latest_request_id']
            report['answer']=current['messages'][-1]['text'];report['characters']=len(report['answer'])
        answer=report['answer']
        page.wait_for_function('(answer)=>[...document.querySelectorAll("#messages .assistant .message-text")].some(x=>x.textContent===answer)',arg=answer)
        displayed=page.locator('#messages .assistant .message-text').filter(has_text=answer).last
        displayed.scroll_into_view_if_needed()
        assert not re.search(r'native:|原生|融合匹配分|显式|隐式|权重|快照|已评估|Exact criterion|21431|21,431',answer)
        assert len(answer)<=400 and ('直线' in answer or 'straight-line' in answer)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.screenshot(path=str(ROOT/f'evaluation/reports/explanation-model-{name}.png'))
        report['checks'].append(name+': readable stored LLM explanation, no diagnostics/overflow'+('; read only, no new model request' if read_only else '; chat submission and unchanged recommendations verified'));context.close()
    assert not errors,errors;browser.close();report.update(status='passed',browser_errors=errors)
(ROOT/'evaluation/reports/explanation-model-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
client.close();print(json.dumps({'status':report['status'],'checks':len(report['checks']),'characters':report['characters']}))
