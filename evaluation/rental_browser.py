"""Real UI checks for layout vs private use, missing evidence and negative follow-up."""
import json
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, expect, TimeoutError
root = Path(__file__).resolve().parents[1]
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda e: errors.append(str(e)))
    page.goto('http://127.0.0.1:8010/')
    try:
        page.wait_for_load_state('networkidle',timeout=2000)
    except TimeoutError:
        pass  # Durable SSE remains open.
    page.locator('body[data-ready="true"]').wait_for()
    def send(text):
        page.locator('#query').fill(text)
        page.locator('#send').click()
        expect(page.locator('#send')).to_be_enabled(timeout=60000)
    send('找个1B1B，预算2500')
    expect(page.locator('#criteria')).to_contain_text('卧室 ＝ 1',timeout=60000)
    expect(page.locator('#criteria')).to_contain_text('卫浴 ＝ 1')
    assert '独立卫浴' not in page.locator('#criteria').inner_text()
    expect(page.locator('.verified-badge:not(.unknown)').first).to_be_visible(timeout=60000)
    page.screenshot(path=str(root/'evaluation/screenshots/07-layout-1b1b.png'),full_page=True)
    send('还要独立卫浴')
    expect(page.locator('#criteria')).to_contain_text('独立卫浴',timeout=60000)
    expect(page.locator('.verified-badge.unknown').first).to_be_visible(timeout=60000)
    assert page.locator('.verified-badge:not(.unknown)').count()==0
    page.locator('.listing-claims summary').first.click()
    expect(page.locator('.listing-claims').first).to_contain_text('数据未收录',timeout=60000)
    expect(page.locator('.listing-claims').first).to_contain_text('专用使用权')
    assert 'amenities 等于' not in page.locator('#recommendations').inner_text()
    page.screenshot(path=str(root/'evaluation/screenshots/08-private-bathroom-unavailable.png'),full_page=True)
    send('不要求独立卫浴了，其他保持')
    expect(page.locator('.verified-badge:not(.unknown)').first).to_be_visible(timeout=60000)
    criteria=page.locator('#criteria').inner_text()
    assert '独立卫浴' not in criteria and '卫浴 ＝ 1' in criteria and '2500' in criteria
    assert not errors,errors
    browser.close()
report={'checked_at':datetime.now(timezone.utc).isoformat(),'status':'passed','checks':['1B1B produces exact bedroom/bathroom counts without inventing private use or rental scope','Existing historical matches are actually displayed','Adding private bathroom separates candidates and explicitly explains absent private-use evidence','The page uses user-facing terms rather than amenities equality','Removing private bathroom preserves bathroom count and budget and restores verified historical matches'],'page_errors':errors,'screenshots':['07-layout-1b1b.png','08-private-bathroom-unavailable.png']}
(root/'evaluation/reports/rental-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print('Rental language browser regression passed: 5 checks.')
