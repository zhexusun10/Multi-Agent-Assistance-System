"""Verify the two-action date picker against an isolated, existing local test conversation."""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / 'data/.runtime/listing-presentation-ui-fixture.json').read_text(encoding='utf-8'))
report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'messages_sent_externally': 0, 'checks': []}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    context.add_init_script('''const f=%s;localStorage.setItem('rental.owner',f.owner);localStorage.setItem('rental.session',f.session_id);localStorage.setItem('rental.sessions',JSON.stringify([{id:f.session_id,title:'日期选择器验收'}]));''' % json.dumps(fixture))
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8010/')
    try:
        page.wait_for_load_state('networkidle', timeout=2000)
    except PlaywrightTimeout:
        pass  # Persistent SSE; use the rendered readiness marker instead.
    expect(page.locator('body')).to_have_attribute('data-ready', 'true')
    today = page.evaluate("async()=> (await (await fetch('/api/config')).json()).today")
    tomorrow = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
    later = (date.fromisoformat(today) + timedelta(days=2)).isoformat()
    page.get_by_role('tab', name='跟进工作台', exact=False).click()
    card = page.locator('#actions .action-card').first
    action_id = card.get_attribute('data-action-id')
    original_revision = page.evaluate('''async f=>{const s=await (await fetch('/api/state?session_id='+f.session_id,{headers:{Authorization:'Bearer '+f.owner}})).json();return s.actions.find(a=>a.id===f.action_id).revision;}''', {**fixture, 'action_id': action_id})
    card.get_by_role('button', name='编辑草稿', exact=True).click()
    expect(page.locator('#action-dialog')).to_be_visible()
    # Reproduce today -> tomorrow without allowing a second highlighted selection or premature write.
    reminder = page.locator('#followup-at')
    reminder.fill(today+'T13:00')
    reminder.click()
    popup = page.locator('.date-time-popover')
    expect(popup).to_be_visible()
    expect(popup.locator('.date-time-footer button')).to_have_count(2)
    expect(popup.get_by_role('button', name='今天', exact=True)).to_have_count(0)
    popup.locator('[data-date="%s"]' % tomorrow).click()
    expect(popup.locator('[aria-selected="true"]')).to_have_count(1)
    expect(popup.locator('[data-date="%s"]' % tomorrow)).to_have_attribute('aria-selected', 'true')
    expect(popup.locator('[data-date="%s"]' % today)).to_have_attribute('aria-selected', 'false')
    backgrounds = popup.evaluate('''(e,ds)=>ds.map(d=>getComputedStyle(e.querySelector('[data-date="'+d+'"]')).backgroundColor)''', [today, tomorrow])
    assert backgrounds[0] != backgrounds[1]
    expect(reminder).to_have_value(today+'T13:00')
    popup.get_by_label('小时', exact=True).select_option('13')
    popup.get_by_label('分钟', exact=True).select_option('0')
    page.screenshot(path=str(root / 'evaluation/reports/date-time-picker-desktop.png'), full_page=True)
    popup.get_by_role('button', name='确认保存', exact=True).click()
    expect(popup).to_have_count(0)
    expect(reminder).to_have_value(tomorrow+'T13:00')
    report['checks'].append('today-to-tomorrow has exactly one selected cell; today is only a dot; no field write before explicit confirm; footer contains only Clear and Confirm Save')
    reminder.click()
    popup.locator('[data-date="%s"]' % later).click()
    page.keyboard.press('Escape')
    expect(reminder).to_have_value(tomorrow+'T13:00')
    expect(page.locator('#action-dialog')).to_be_visible()
    reminder.click()
    popup.get_by_role('button', name='清除', exact=True).click()
    expect(reminder).to_have_value('')
    expect(popup).to_have_count(0)
    reminder.click()
    expect(popup.locator('[aria-selected="true"]')).to_have_count(0)
    expect(popup.get_by_role('button', name='确认保存', exact=True)).to_be_disabled()
    popup.locator('[data-date="%s"]' % tomorrow).click()
    popup.get_by_label('小时', exact=True).select_option('13')
    popup.get_by_label('分钟', exact=True).select_option('0')
    popup.get_by_role('button', name='确认保存', exact=True).click()
    report['checks'].append('Escape discards staged changes and keeps the parent dialog; Clear empties only the current field; empty selection cannot accidentally confirm today')
    # Start/end use exactly the same component and continue generating Singapore-local messages.
    for field, hour in [('starts-at', '15'), ('ends-at', '16')]:
        page.locator('#'+field).click()
        popup.locator('[data-date="%s"]' % tomorrow).click()
        popup.get_by_label('小时', exact=True).select_option(hour)
        popup.get_by_label('分钟', exact=True).select_option('5')
        popup.get_by_role('button', name='确认保存', exact=True).click()
        expect(page.locator('#'+field)).to_have_value(tomorrow+'T'+hour+':05')
    assert '3:05 PM–4:05 PM' in page.locator('#wire-preview').text_content()
    page.get_by_role('button', name='保存当前草稿', exact=True).click()
    expect(page.locator('#action-dialog')).not_to_be_visible()
    saved = page.evaluate('''async f=>{const s=await (await fetch('/api/state?session_id='+f.session_id,{headers:{Authorization:'Bearer '+f.owner}})).json();return s.actions.find(a=>a.id===f.action_id);}''', {**fixture, 'action_id': action_id})
    assert saved['revision'] == original_revision+1
    assert datetime.fromisoformat(saved['slots']['starts_at'].replace('Z', '+00:00')).astimezone(timezone.utc).hour == 7
    assert datetime.fromisoformat(saved['slots']['ends_at'].replace('Z', '+00:00')).astimezone(timezone.utc).hour == 8
    assert saved['status'] == 'PENDING_REVIEW' and not saved.get('manual')
    report['checks'].append('viewing start/end and reminder use the shared picker; explicit draft save persists one revision with correct SG15:05→UTC07:05 and SG16:05→UTC08:05; no send')
    # Mobile popup remains inside the viewport and retains the same two footer actions.
    page.locator('[data-action-id="%s"]' % action_id).get_by_role('button', name='编辑草稿', exact=True).click()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#followup-at').click()
    expect(popup).to_be_visible()
    bounds = popup.bounding_box()
    assert bounds['x'] >= 0 and bounds['x']+bounds['width'] <= 390 and bounds['y'] >= 0 and bounds['y']+bounds['height'] <= 844, bounds
    expect(popup.locator('.date-time-footer button')).to_have_count(2)
    page.screenshot(path=str(root / 'evaluation/reports/date-time-picker-mobile.png'))
    page.keyboard.press('Escape')
    assert not errors, errors
    report['checks'].append('mobile popup stays within390×844, keyboard dismissal works, and there are no browser JavaScript errors')
    report['status'] = 'passed'
    (root / 'evaluation/reports/date-time-picker-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    browser.close()
print(json.dumps(report, ensure_ascii=False))
