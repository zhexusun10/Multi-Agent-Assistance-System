"""Browser acceptance with recorded states and intercepted APIs; zero model calls."""
from pathlib import Path
from urllib.parse import urlparse
from copy import deepcopy
import json
import os
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
fixture=json.loads((ROOT/os.getenv('REFINEMENT_UI_FIXTURE','data/.runtime/refinement-ui-fixture.json')).read_text(encoding='utf-8'))
report={'model_calls':0,'query_posts':0,'checks':[]}
errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='chrome')
    def setup(width,height):
        context=browser.new_context(viewport={'width':width,'height':height})
        sid=fixture['before']['session_id']
        context.add_init_script('localStorage.setItem("rental.owner","isolated-refinement-ui-owner");localStorage.setItem("rental.session",'+json.dumps(sid)+');')
        state={'value':deepcopy(fixture['before']),'posts':[]}
        def mock(route):
            req=route.request;path=urlparse(req.url).path
            if path=='/api/config':value={'dataset':fixture['dataset'],'today':'2026-10-22','action_mode':'simulation'}
            elif path=='/api/state':value=state['value']
            elif path=='/api/events':route.fulfill(status=200,content_type='text/event-stream',body=': fixture\n\n');return
            elif path=='/api/behavior/views':value={'accepted':True,'granted':False}
            elif path.endswith('/submit') and path.startswith('/api/refinements/'):
                body=req.post_data_json;state['posts'].append(body)
                assert body['session_id']==sid and body['submission_id']
                assert body['answers']==fixture['answers'],body
                state['value']=deepcopy(fixture['after']);value={'status':'queued','operation_id':fixture['after']['active_operation_id']}
            elif path.endswith('/dismiss') and path.startswith('/api/refinements/'):
                state['posts'].append(req.post_data_json);state['value']['refinement_plans'][0]['status']='DISMISSED';state['value']['last_event_id']+=1;value={'status':'dismissed'}
            else:
                if path=='/api/query':report['query_posts']+=1
                errors.append('Unexpected API '+path);route.abort();return
            route.fulfill(status=200,content_type='application/json',body=json.dumps(value))
        context.route('**/api/**',mock);context.route('https://**/*',lambda route:route.abort())
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8010/');page.wait_for_load_state('networkidle');page.wait_for_selector('body[data-ready=true]')
        expect(page.locator('.requirement-refinement')).to_have_count(1)
        return context,page,state
    context,page,state=setup(1600,1000)
    card=page.locator('.requirement-refinement');before_ids=page.locator('.listing-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)')
    expect(card.get_by_text('1 / 3',exact=True)).to_be_visible()
    card.get_by_role('button',name='设置条件',exact=True).click()
    card.get_by_role('checkbox',name='普通公寓',exact=True).check();card.get_by_label('重要程度').select_option('soft')
    card.get_by_role('button',name='下一项 →').click()
    card.get_by_role('button',name='设置条件',exact=True).click()
    card.get_by_label('面积下限（选填）').fill('40');card.get_by_label('面积上限（选填）').fill('30');card.get_by_label('重要程度').select_option('soft')
    card.get_by_role('button',name='下一项 →').click();expect(card.get_by_role('alert')).to_contain_text('下限不能高于上限')
    assert not state['posts']
    card.get_by_label('面积下限（选填）').fill('30');card.get_by_label('面积上限（选填）').fill('')
    card.get_by_role('button',name='← 上一项').click();expect(card.get_by_role('checkbox',name='普通公寓',exact=True)).to_be_checked()
    card.get_by_role('button',name='下一项 →').click();expect(card.get_by_label('面积下限（选填）')).to_have_value('30')
    page.reload();page.wait_for_load_state('networkidle');page.wait_for_selector('body[data-ready=true]')
    card=page.locator('.requirement-refinement');expect(card.get_by_text('2 / 3',exact=True)).to_be_visible();expect(card.get_by_label('面积下限（选填）')).to_have_value('30')
    page.evaluate('window.dispatchEvent(new Event("pageshow"))');page.wait_for_timeout(200)
    expect(card.get_by_label('面积下限（选填）')).to_have_value('30')
    card.get_by_role('button',name='下一项 →').click();card.get_by_role('button',name='填写理想月租').click();card.get_by_label('理想月租上限（新币）').fill('2000')
    assert page.locator('.listing-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)')==before_ids
    assert not state['posts'];card.scroll_into_view_if_needed();page.screenshot(path=str(ROOT/'evaluation/reports/requirement-refinement-desktop.png'))
    card.get_by_role('button',name='补充好了，重新推荐').click()
    expect(card.get_by_text('已记录您的选择',exact=True)).to_be_visible();expect(page.locator('#messages')).to_contain_text('已根据您补充的房屋类型、面积和理想月租更新推荐')
    assert len(state['posts'])==1;expect(page.locator('.requirement-refinement')).to_have_count(1)
    assert not card.locator('input,select').count()
    report['checks'].extend(['three-page form in message history','explicit hard/soft selection','invalid range blocks submission','back/forward preserves choices','refresh restores local draft','state polling preserves inputs','no reranking before submit','one structured submit and new explanation','read-only historical card and no repeated questions'])
    context.close()
    mobile,small,state=setup(390,844);card=small.locator('.requirement-refinement');card.scroll_into_view_if_needed()
    assert small.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    small.screenshot(path=str(ROOT/'evaluation/reports/requirement-refinement-mobile.png'))
    card.get_by_role('button',name='暂不补充',exact=True).click();expect(card.locator('.refinement-status')).to_have_text('暂不补充');assert len(state['posts'])==1;assert not card.locator('button').count()
    report['checks'].append('mobile layout and dismiss without recommendation')
    mobile.close()
    context,page,state=setup(1400,900);card=page.locator('.requirement-refinement')
    state['value']['active_operation_id']='00000000-0000-4000-8000-000000000000'
    state['value']['revision']+=1
    page.evaluate('window.dispatchEvent(new Event("pageshow"))')
    expect(card.get_by_text('找房条件已更新，这张卡片已结束',exact=True)).to_be_visible();assert not card.locator('button').count();assert not state['posts']
    report['checks'].append('natural-language search version changes disable old cards')
    context.close();browser.close()
assert not errors,errors
assert report['query_posts']==0
report['status']='passed'
(ROOT/'evaluation/reports/requirement-refinement-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
