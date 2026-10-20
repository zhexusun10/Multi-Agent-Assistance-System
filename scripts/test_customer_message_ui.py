"""Exercise legacy rewriting and exact browser preview/copy parity, without sending a message."""
import json
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / 'data/.runtime/customer-message-ui-fixture.json').read_text(encoding='utf-8'))
report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'messages_sent_externally': 0, 'checks': []}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    context = browser.new_context(permissions=['clipboard-read', 'clipboard-write'], viewport={'width': 1400, 'height': 1000})
    context.add_init_script('''const f=%s;localStorage.setItem('rental.owner',f.owner);localStorage.setItem('rental.session',f.session_id);localStorage.setItem('rental.sessions',JSON.stringify([{id:f.session_id,title:'看房消息格式验收'}]));''' % json.dumps(fixture))
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8010/')
    try:
        page.wait_for_load_state('networkidle', timeout=2000)
    except PlaywrightTimeout:
        pass  # SSE keeps a request open; use the application's own rendered readiness marker.
    expect(page.locator('body')).to_have_attribute('data-ready', 'true')
    page.get_by_role('tab', name='跟进工作台').click()
    card = page.locator('[data-action-id="%s"]' % fixture['legacy_id'])
    # Opening the copy action on an old generated draft must lead to an editable rewrite, not export it.
    card.get_by_role('button', name='复制草稿 / 确认已发送', exact=True).click()
    expect(page.locator('#action-dialog')).to_be_visible()
    text = page.locator('#action-body').input_value()
    assert 'Gold Coast Condo' in text and '房间有独立卫浴吗' in text
    assert not page.locator('#negotiation-notes').input_value()
    assert '快照' not in text and '生成看房询问草稿' not in text
    page.get_by_role('button', name='重新起草自然消息', exact=True).click()
    expect(page.locator('#toast')).to_contain_text('已重新起草')
    page.locator('#starts-at').fill('2026-10-21T14:00')
    page.locator('#ends-at').fill('2026-10-21T15:00')
    page.locator('#negotiation-notes').fill('希望月租协商到1500新币。')
    preview = page.locator('#wire-preview').text_content()
    expected_time = '2026年10月21日（周三） 2:00 PM–3:00 PM（新加坡时间）'
    assert expected_time in preview
    assert preview.count('1500') == 1 and preview.endswith('谢谢！')
    page.get_by_role('button', name='保存当前草稿', exact=True).click()
    expect(page.locator('#action-dialog')).not_to_be_visible()
    card.get_by_role('button', name='复制草稿 / 确认已发送', exact=True).click()
    expect(page.locator('#manual-copy-body')).to_be_visible()
    copy = page.locator('#manual-copy-body').input_value()
    assert copy == preview, 'Browser preview differs from server copy payload'
    for bad in ['生成看房询问草稿', '逐项询问', '需双方', '不代表预约', '待双方协商', '历史快照', '2026-10-21T', '必须满足', '尚无证据']:
        assert bad not in copy, bad
    page.get_by_role('button', name='复制完整草稿', exact=True).click()
    expect(page.locator('#toast')).to_contain_text('草稿已复制')
    assert page.evaluate('navigator.clipboard.readText()').replace('\r\n', '\n') == copy
    page.screenshot(path=str(root / 'evaluation/reports/customer-message-desktop.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path=str(root / 'evaluation/reports/customer-message-mobile.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors, errors
    report.update(status='passed', example_message=copy, checks=[
        'old generated draft rewrites before a new copy; empty router command does not become negotiation',
        'real negotiation survives exactly once; uncertainty stays in the workbench',
        'browser preview equals server copy payload and Windows clipboard content',
        'UTC06–07 becomes SG14–15 with a readable date and AM/PM',
        'desktop and mobile dialog; no JavaScript errors; no send acknowledgement performed',
    ])
    (root / 'evaluation/reports/customer-message-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    browser.close()
print(json.dumps(report, ensure_ascii=False))
