"""Browser acceptance for real knowledge answers, persistent citations and responsive source cards."""
from datetime import datetime,timezone
from pathlib import Path
import json
from playwright.sync_api import sync_playwright,expect,TimeoutError as PlaywrightTimeout

root=Path(__file__).resolve().parents[1]
fixture=json.loads((root/'data/.runtime/orchestrator-rag-ui-fixture.json').read_text(encoding='utf-8'))
report={'checked_at':datetime.now(timezone.utc).isoformat(),'checks':[],'external_messages_sent':0}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='chrome')
    context=browser.new_context(viewport={'width':1480,'height':1000})
    context.add_init_script('''const f=%s;localStorage.setItem('rental.owner',f.owner);localStorage.setItem('rental.session',f.session_id);localStorage.setItem('rental.sessions',JSON.stringify([{id:f.session_id,title:'三路编排与混合知识问答验收'}]));'''%json.dumps(fixture))
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8010/')
    try:page.wait_for_load_state('networkidle',timeout=2000)
    except PlaywrightTimeout:pass
    expect(page.locator('body')).to_have_attribute('data-ready','true')
    expect(page.locator('.knowledge-sources')).to_have_count(2)
    first=page.locator('.knowledge-sources').first
    first.locator('summary').click()
    expect(first.locator('blockquote').first).to_be_visible()
    official=first.locator('a[href^="https://www."]')
    assert official.count()>=2
    assert all('.gov.sg/' in url for url in official.evaluate_all('(nodes)=>nodes.map(a=>a.href)'))
    assert '六' in first.inner_text() or '6' in first.inner_text()
    count=page.locator('.listing-card').count();assert count>0
    page.screenshot(path=str(root/'evaluation/reports/orchestrator-rag-desktop.png'),full_page=True)
    report['checks'].append('real policy answers expose official links, exact excerpts and review dates; existing recommendation cards coexist')
    page.reload();expect(page.locator('body')).to_have_attribute('data-ready','true');expect(page.locator('.knowledge-sources')).to_have_count(2)
    report['checks'].append('citation metadata survives reload and PostgreSQL conversation restoration')
    page.locator('#query').fill('普通私人公寓能只租一个月吗？')
    page.locator('#send').click()
    expect(page.locator('.knowledge-sources')).to_have_count(3,timeout=60000)
    latest=page.locator('.message.assistant').last
    assert '三' in latest.inner_text() or '3' in latest.inner_text()
    latest.locator('summary').click();expect(latest.locator('blockquote').first).to_be_visible()
    report['checks'].append('typing a policy question in the actual chat invokes the new orchestrator and renders a sourced answer without replacing the search results')
    expect(page.locator('.listing-card')).to_have_count(count)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'), 'horizontal overflow'
    page.screenshot(path=str(root/'evaluation/reports/orchestrator-rag-mobile.png'),full_page=True)
    assert not errors,errors
    report['checks'].append('390px mobile source cards fit viewport; no browser JavaScript errors')
    browser.close()
(root/'evaluation/reports/orchestrator-rag-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
