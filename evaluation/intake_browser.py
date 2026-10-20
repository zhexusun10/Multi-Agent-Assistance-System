"""Regression for the reported Clementi/private-bathroom/commute conversation."""
import json
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, expect, TimeoutError

root = Path(__file__).resolve().parents[1]
shots = root / 'evaluation/screenshots'
reports = root / 'evaluation/reports'
errors = []
query = '我要找金文泰附近，预算2500每月的房子，要求有独立卫浴，离新国立大学通勤30分钟以内'
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8010/')
    try:
        page.wait_for_load_state('networkidle', timeout=2500)
    except TimeoutError:
        pass  # The SSE subscription remains open by design.
    page.locator('body[data-ready="true"]').wait_for()
    assistant = page.locator('.message.assistant .message-text')
    def send(text):
        page.locator('#query').fill(text)
        page.locator('#send').click()
        expect(page.locator('#send')).to_be_enabled(timeout=30000)
    send(query)
    expect(assistant.last).to_contain_text('没有已验证匹配', timeout=60000)
    text = assistant.last.inner_text()
    assert 'CLEMENTI' in text and '还需确认' not in text
    assert all(word in page.locator('#criteria').inner_text() for word in ['2500','独立卫浴','30 min','更近优先'])
    assert 'Expected unit' not in text and 'upsert' not in text
    page.screenshot(path=str(shots/'05-nearby-area.png'), full_page=True)
    send('通勤指定方式为公交\n就是Clementi MRT附近3公里')
    expect(page.locator('#criteria')).to_contain_text('3000 m', timeout=60000)
    expect(page.locator('#criteria')).to_contain_text('公共交通', timeout=60000)
    expect(assistant.last).to_contain_text('没有已验证匹配', timeout=60000)
    criteria = page.locator('#criteria').inner_text()
    assert all(word in criteria for word in ['2500','3000','独立卫浴','30','CLEMENTI MRT STATION','公共交通'])
    assert page.locator('.verified-badge.unknown').count() > 0
    assert page.locator('.verified-badge:not(.unknown)').count() == 0
    assert not errors, errors
    page.screenshot(path=str(shots/'06-nearby-mrt-3km.png'), full_page=True)
    browser.close()
(reports/'intake-browser.json').write_text(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'status':'passed','checks':['Nearby Clementi automatically resolves to a real area and ranks with no blocking radius question','Exact MRT 3km and transit follow-up updates location and commute while preserving budget/private-bathroom/30min','Unknown mandatory facts remain unverified; no internal validation errors reach the browser'],'page_errors':errors,'screenshots':['05-nearby-area.png','06-nearby-mrt-3km.png']}, ensure_ascii=False, indent=2), encoding='utf-8')
print('Reported conversation regression passed: 3 browser checks.')
