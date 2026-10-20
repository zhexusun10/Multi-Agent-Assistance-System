"""Real browser check for the native recommender inside the existing workbench."""
from datetime import datetime,timezone
from pathlib import Path
import json
import re
import time
from playwright.sync_api import sync_playwright,expect,TimeoutError as BrowserTimeout

ROOT=Path(__file__).resolve().parents[1]
fixture=json.loads((ROOT/'data/.runtime/native-browser-session.json').read_text(encoding='utf-8'))
report={'checked_at':datetime.now(timezone.utc).isoformat(),'checks':[],'external_messages_sent':0}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='chrome')
    context=browser.new_context(viewport={'width':1480,'height':1000})
    context.add_init_script("const f=%s;localStorage.setItem('rental.owner',f.owner);localStorage.setItem('rental.session',f.session_id);localStorage.setItem('rental.sessions',JSON.stringify([{id:f.session_id,title:'原生推荐接入验收'}]));"%json.dumps(fixture))
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8010/')
    try:page.wait_for_load_state('networkidle',timeout=2000)
    except BrowserTimeout:pass  # SSE is intentionally persistent.
    expect(page.locator('body')).to_have_attribute('data-ready','true')
    expect(page.locator('.coverage')).to_contain_text('21431')
    expect(page.locator('.listing-card')).to_have_count(8)
    cards=page.locator('.listing-card');card=cards.first
    expect(card.locator('.listing-details-panel')).to_have_attribute('data-loaded','true',timeout=20000)
    expect(card.locator('.property-facts').first).to_contain_text('卫浴数量')
    card.locator('.listing-claims summary').click()
    expect(card.locator('.native-ranking')).to_contain_text('样例浏览兴趣')
    expect(card.locator('button',has_text='录入浏览样例并重排')).to_be_visible()
    pending=cards.nth(4);pending.locator('.listing-claims summary').click()
    expect(pending.locator('.native-ranking')).to_contain_text('未给此待核实候选打分')
    report['checks'].append('21,431-row coverage, native verified/unverified groups, photos and actual fused/unscored labels render')
    card.locator('.listing-collapse-toggle').click();expect(card).to_have_class(re.compile('collapsed'))
    card.locator('.listing-collapse-toggle').click()
    page.locator('#mode-info').click();expect(page.locator('#details-body')).to_contain_text('江山原生 Recommendation Agent');page.locator('#details-dialog .close-dialog').click()
    report['checks'].append('Card collapse, native provider mode and data presentation remain usable')
    page.screenshot(path=str(ROOT/'evaluation/reports/native-desktop.png'),full_page=True)
    card.evaluate("node=>node.dataset.renderProbe='preserve-this-card'")
    page.locator('#query').fill('请解释第一套为什么推荐，使用当前列表')
    page.locator('#send').click()
    expect(page.locator('.message.assistant').last).to_contain_text('原生融合匹配分',timeout=60000)
    expect(page.locator('.listing-card')).to_have_count(8)
    expect(page.locator('.listing-card').first).to_have_attribute('data-render-probe','preserve-this-card')
    report['checks'].append('Typing explanation in real chat uses the existing result and renders native behavioral score')
    # An explicit sample-duration button click exercises actual frontend -> feedback -> A/B/C.
    old_request=page.evaluate("""async()=>{const r=await fetch('/api/state?session_id='+localStorage.getItem('rental.session'),{headers:{authorization:'Bearer '+localStorage.getItem('rental.owner')}});return (await r.json()).latest_request_id;}""")
    page.once('dialog',lambda d:d.accept('25'))
    page.locator('.listing-card').first.get_by_role('button',name='录入浏览样例并重排').click()
    # Poll resolved JSON, not an async JavaScript predicate whose Promise can be
    # treated as truthy by browser polling before the workflow has completed.
    deadline=time.monotonic()+120
    while True:
        actual=context.request.get('http://127.0.0.1:8010/api/state?session_id='+fixture['session_id'],headers={'authorization':'Bearer '+fixture['owner']}).json()
        new_request=actual.get('latest_request_id')
        if new_request!=old_request and actual.get('recommendation',{}).get('request_id')==new_request and actual.get('recommendation_operation_id')==actual.get('active_operation_id') and actual.get('explanation',{}).get('source_request_id')==new_request:
            break
        assert time.monotonic()<deadline,'Native sample reranking did not complete'
        page.wait_for_timeout(500)
    expect(page.locator('.listing-card').first).to_have_attribute('data-source-request-id',new_request,timeout=15000)
    expect(page.locator('.message.assistant').last).to_contain_text('已评估',timeout=120000)
    expect(page.locator('.listing-card')).to_have_count(8)
    report['checks'].append('Manual SAMPLE duration entry completes feedback reranking through the real chat workflow')
    expect(page.locator('#send')).to_be_enabled(timeout=15000)
    page.locator('.listing-card').first.get_by_role('button',name='我想看这套').click()
    expect(page.locator('[data-tab=actions]')).to_have_class(re.compile('active'),timeout=45000)
    expect(page.locator('#actions')).to_contain_text('草稿')
    report['checks'].append('Native recommendation can start the existing manual viewing follow-up workflow')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.screenshot(path=str(ROOT/'evaluation/reports/native-mobile.png'),full_page=True)
    assert not errors,errors
    report.update(status='passed',browser_errors=errors)
    browser.close()
(ROOT/'evaluation/reports/native-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
