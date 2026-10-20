"""Browser verification of the deployed full snapshot, cards and old-session guard."""
from datetime import datetime, timezone
from pathlib import Path
import json
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout

root=Path(__file__).resolve().parents[1]
fixture=json.loads((root/'data/.runtime/full-data-ui-fixture.json').read_text(encoding='utf8'))
report={'checked_at':datetime.now(timezone.utc).isoformat(),'checks':[]}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='chrome')
    context=browser.new_context(viewport={'width':1440,'height':1000})
    context.add_init_script("""const f=%s;
        localStorage.setItem('rental.owner',f.owner);
        localStorage.setItem('rental.session',f.session_id);
        localStorage.setItem('rental.sessions',JSON.stringify([{id:f.session_id,title:'完整快照验收'}]));
    """%json.dumps(fixture))
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8010/')
    try:page.wait_for_load_state('networkidle',timeout=2000)
    except PlaywrightTimeout:pass  # The application intentionally keeps SSE open.
    expect(page.locator('body')).to_have_attribute('data-ready','true')
    expect(page.locator('#dataset-note')).to_contain_text('21,431')
    expect(page.locator('#dataset-kind')).to_have_text('完整历史快照')
    expect(page.locator('.listing-card')).to_have_count(5)
    expect(page.locator('.coverage')).to_contain_text('21431')
    card=page.locator('.listing-card').first
    expect(card.locator('.listing-details-panel')).to_have_attribute('data-loaded','true',timeout=20000)
    expect(card.locator('.property-facts').first).to_contain_text('卫浴数量')
    assert card.locator('.gallery-count').inner_text()!='0 / 0'
    report['checks'].append('Full snapshot badge, all-record coverage, five candidates and SQL/photo/Neo4j details render')
    page.screenshot(path=str(root/'evaluation/reports/full-data-desktop.png'),full_page=True)
    # Exercise existing collapse interaction after enriching the larger dataset.
    card.locator('.listing-collapse-toggle').click()
    expect(card).to_have_class(__import__('re').compile('collapsed'))
    card.locator('.listing-collapse-toggle').click()
    report['checks'].append('Property card collapse remains usable after switching data')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.screenshot(path=str(root/'evaluation/reports/full-data-mobile.png'),full_page=True)
    report['checks'].append('390px mobile view has no horizontal page overflow')
    # Inspect a real preserved sample conversation without changing its history.
    old=json.loads((root/'data/.runtime/location-subject-ui-fixture.json').read_text(encoding='utf8'))
    other=browser.new_context(viewport={'width':1440,'height':1000})
    other.add_init_script("const f=%s;localStorage.setItem('rental.owner',f.owner);localStorage.setItem('rental.session',f.session_id);"%json.dumps(old))
    stale=other.new_page();stale.goto('http://127.0.0.1:8010/')
    expect(stale.locator('body')).to_have_attribute('data-ready','true')
    expect(stale.locator('#recommendations')).to_contain_text('房源数据已更新为完整快照')
    expect(stale.locator('#recommendations').get_by_role('button',name='按当前需求重新搜索')).to_be_visible()
    expect(stale.locator('.listing-card')).to_have_count(0)
    report['checks'].append('Old sample conversation is preserved; its stale cards cannot masquerade as full results')
    assert not errors,errors
    report.update(status='passed',browser_errors=errors)
    browser.close()
(root/'evaluation/reports/full-data-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(report,ensure_ascii=False))
