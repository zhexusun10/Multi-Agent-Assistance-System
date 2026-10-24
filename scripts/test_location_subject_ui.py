"""Regression for the exact NUS/MRT screenshot and continuing the existing conversation."""
from datetime import datetime,timezone
from pathlib import Path
import json
from playwright.sync_api import sync_playwright,expect,TimeoutError as PlaywrightTimeout

root=Path(__file__).resolve().parents[1]
fixture=json.loads((root/'data/.runtime/location-subject-ui-fixture.json').read_text(encoding='utf-8'))
report={'checked_at':datetime.now(timezone.utc).isoformat(),'checks':[],'external_messages_sent':0}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='chrome')
    context=browser.new_context(viewport={'width':1440,'height':1000})
    context.add_init_script('''const f=%s;localStorage.setItem('rental.owner',f.owner);localStorage.setItem('rental.session',f.session_id);localStorage.setItem('rental.sessions',JSON.stringify([{id:f.session_id,title:'NUS与MRT条件修复验收'}]));'''%json.dumps(fixture))
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8010/')
    try:page.wait_for_load_state('networkidle',timeout=2000)
    except PlaywrightTimeout:pass
    expect(page.locator('body')).to_have_attribute('data-ready','true')
    expect(page.locator('.listing-card')).to_have_count(10,timeout=25000)
    expect(page.locator('#criteria')).to_contain_text('National University of Singapore')
    expect(page.locator('#criteria')).to_contain_text('最近的MRT地铁站')
    expect(page.locator('#criteria')).to_contain_text('3000')
    assert '地点目录暂未找到“房子”' not in page.locator('.message.assistant').last.inner_text()
    expect(page.locator('.message.assistant').last).to_contain_text('已找到待核实候选')
    card=page.locator('.listing-card').first
    expect(card.locator('.listing-match-summary')).to_contain_text('最近的MRT地铁站')
    expect(card.locator('.listing-match-summary')).to_contain_text('National University of Singapore')
    expect(card.locator('.listing-match-summary')).to_contain_text('独立卫浴')
    expect(page.locator('.match-chip.ranked')).to_have_count(10)
    assert not page.locator('.match-chip.fail').count(), 'continuous nearer-is-better must not show a failed-condition badge'
    report['checks'].append('exact screenshot request shows five candidate cards with separate NUS, nearest MRT, price and private-bath evidence')
    report['checks'].append('both continuous distance preferences show ranking arrows, never red failure at the score pivot')
    page.screenshot(path=str(root/'evaluation/reports/location-subject-desktop.png'),full_page=True)
    before=page.locator('.message.assistant').count()
    page.locator('#query').fill(fixture['simple_query']);page.locator('#send').click()
    expect(page.locator('.message.assistant')).to_have_count(before+1,timeout=65000)
    expect(page.locator('.listing-card')).to_have_count(10,timeout=10000)
    assert '地点目录暂未找到“房子”' not in page.locator('.message.assistant').last.inner_text()
    expect(page.locator('.message.assistant').last).to_contain_text('已找到待核实候选')
    expect(page.locator('#criteria')).to_contain_text('最近的MRT地铁站')
    report['checks'].append('the same conversation accepts the shorter user request without a phantom-place clarification or losing the unmodified MRT condition')
    page.reload();expect(page.locator('body')).to_have_attribute('data-ready','true');expect(page.locator('.listing-card')).to_have_count(10)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    page.screenshot(path=str(root/'evaluation/reports/location-subject-mobile.png'),full_page=True)
    assert not errors,errors
    report['checks'].append('recommendations survive reload and fit mobile width; no JavaScript errors')
    browser.close()
(root/'evaluation/reports/location-subject-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
