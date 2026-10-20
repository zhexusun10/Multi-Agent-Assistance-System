"""Real-browser user journey, using the running app and configured model."""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError

parser=argparse.ArgumentParser()
parser.add_argument('--base-url',default='http://127.0.0.1:8010')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
shots=root/'evaluation/screenshots'
reports=root/'evaluation/reports'
shots.mkdir(parents=True,exist_ok=True)
reports.mkdir(parents=True,exist_ok=True)
checks=[]
errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(channel='chrome',headless=True)
    context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
    page=context.new_page()
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(args.base_url)
    # A permanent SSE connection intentionally prevents networkidle.
    try:page.wait_for_load_state('networkidle',timeout=3000)
    except TimeoutError:pass
    page.locator('body[data-ready="true"]').wait_for()
    page.locator('[data-query]').first.click()
    cards=page.locator('.listing-card:has(.verified-badge:not(.unknown))')
    cards.first.wait_for(timeout=60000)
    assert cards.count()==5
    for card in cards.all():
        price=float(re.search(r'S\$\s*([\d,]+)',card.locator('.listing-price').inner_text()).group(1).replace(',',''))
        assert price<=5000
        assert 'D05' in card.locator('.listing-subtitle').inner_text()
    checks.append('Natural-language search displays actual matching listings')
    cards.first.locator('summary').click()
    cards.first.locator('.evidence-button').first.click()
    page.locator('#details-dialog').wait_for(state='visible')
    assert '来源版本' in page.locator('#details-body').inner_text()
    assert 'historical-csv-main-0c45bf3' in page.locator('#details-body').inner_text()
    page.locator('#details-dialog .close-dialog').click()
    page.screenshot(path=str(shots/'02-matching-listings.png'),full_page=True)
    checks.append('Evidence dialog resolves persisted source facts')
    page.locator('#query').fill('预算改为4500新币')
    page.locator('#query').press('Enter')
    page.locator('#criteria').get_by_text(re.compile('4500')).wait_for()
    cards.first.wait_for(timeout=60000)
    assert '卧室' in page.locator('#criteria').inner_text() and 'D05' in page.locator('#criteria').inner_text()
    checks.append('Budget edit preserves untouched requirements and refreshes results')
    cards.first.get_by_role('button',name='联系经纪人',exact=True).click()
    page.locator('#actions .action-card').first.wait_for(state='attached',timeout=30000)
    page.locator('[data-tab="actions"]').click()
    action=page.locator('#actions .action-card').first
    action.get_by_role('button',name='编辑草稿',exact=True).click()
    page.locator('#recipient-name').fill('演示经纪人')
    page.locator('#recipient-email').fill('demo-salesperson@example.test')
    page.locator('#recipient-verified').check()
    page.get_by_role('button',name='保存当前草稿',exact=True).click()
    page.locator('#action-dialog').wait_for(state='hidden')
    action.get_by_role('button',name='批准此修订',exact=True).click()
    action.get_by_role('button',name='模拟执行',exact=True).wait_for()
    action.get_by_role('button',name='模拟执行',exact=True).click()
    page.locator('#actions .action-status').get_by_text('已模拟执行',exact=True).wait_for()
    assert 'simulated:' in action.inner_text()
    page.screenshot(path=str(shots/'03-action-review.png'),full_page=True)
    checks.append('Contact verification, review and explicit simulated execution work in the UI')
    page.locator('[data-query]').nth(2).click()
    page.locator('#documents .doc-card').first.wait_for(state='attached',timeout=30000)
    page.locator('[data-tab="documents"]').click()
    with page.expect_download() as info:
        page.get_by_role('button',name='下载 Markdown',exact=True).first.click()
    document=reports/'browser-summary.md'
    info.value.save_as(str(document))
    text=document.read_text(encoding='utf-8')
    assert '4500' in text and 'D05' in text and '当前可租' in text
    checks.append('A real document download contains the current requirements and unknowns')
    page.reload()
    page.locator('body[data-ready="true"]').wait_for()
    assert '4500' in page.locator('#criteria').inner_text()
    assert page.locator('#actions .action-card').count()==1
    page.locator('[data-tab="actions"]').click()
    assert '已模拟执行' in page.locator('#actions').inner_text()
    checks.append('Reload restores conversation, actions and artifacts without re-execution')
    page.set_viewport_size({'width':390,'height':844})
    page.locator('[data-tab="recommendations"]').click()
    page.screenshot(path=str(shots/'04-mobile.png'),full_page=True)
    assert page.evaluate('() => document.documentElement.scrollWidth<=window.innerWidth+1')
    assert not errors,errors
    checks.append('Mobile layout fits the viewport; no browser JavaScript exceptions')
    browser.close()
(reports/'browser-flow.json').write_text(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'status':'passed','checks':checks,'page_errors':errors,'screenshots':['02-matching-listings.png','03-action-review.png','04-mobile.png']},ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Browser journey passed: {len(checks)} business checks; screenshots saved.')
