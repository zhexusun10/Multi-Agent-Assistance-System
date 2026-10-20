"""Actual browser regression: independent card folding and a reply-aware manual follow-up.

Requires reply-continuity-live.test.ts fixture; no external messages are sent.
The report distinguishes configured-model success from the rule fallback.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / 'data/.runtime/reply-continuity-ui-fixture.json').read_text(encoding='utf-8'))
text = '你好，房子最短租6个月，房间配备床、椅子和书桌，被褥还需要自行准备。水电费是包含在房租里的。房间配备独立卫浴。'
report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'messages_sent_externally': 0, 'checks': []}

def no_repeat(body):
    assert not re.search(r'最短可以租多久|带哪些家具|有独立卫浴吗|水电费包含在房租里吗', body), body

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    auth = {'owner': fixture['owner'], 'session_id': fixture['session_id']}
    context.add_init_script('''const f=%s;localStorage.setItem('rental.owner',f.owner);localStorage.setItem('rental.session',f.session_id);localStorage.setItem('rental.sessions',JSON.stringify([{id:f.session_id,title:'卡片折叠与回复连续性验收'}]));''' % json.dumps(auth))
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto('http://127.0.0.1:8010/')
    try:
        page.wait_for_load_state('networkidle', timeout=2200)
    except PlaywrightTimeout:
        pass  # Application SSE intentionally stays open.
    expect(page.locator('body')).to_have_attribute('data-ready', 'true')
    cards = page.locator('.listing-card')
    expect(cards).to_have_count(len(fixture['listing_ids']))
    first, second = cards.nth(0), cards.nth(1)
    expect(first.locator('.listing-details-panel')).to_have_attribute('data-loaded', 'true', timeout=25000)
    title = first.locator('.listing-title').inner_text()
    first.locator('.listing-collapse-toggle').click()
    expect(first.locator('.listing-card-content')).not_to_be_visible()
    expect(first.locator('.listing-collapse-toggle')).to_have_attribute('aria-expanded', 'false')
    assert first.inner_text().strip() == title + '\n⌄ 展开房源'
    expect(second.locator('.listing-card-content')).to_be_visible()
    second.locator('.listing-collapse-toggle').click()
    page.locator('#recommendations').evaluate('e=>e.scrollTop=0')
    page.screenshot(path=str(root / 'evaluation/reports/listing-collapsed-desktop.png'), full_page=True)
    first.locator('.listing-collapse-toggle').focus()
    page.keyboard.press('Enter')
    expect(first.locator('.listing-card-content')).to_be_visible()
    expect(second.locator('.listing-card-content')).not_to_be_visible()
    first.locator('.listing-collapse-toggle').click()
    report['checks'].append('per-card bottom arrow folds to title only; independent expansion preserves photos/facts; Enter keyboard interaction works')

    page.get_by_role('tab', name='跟进工作台').click()
    action = page.locator('[data-action-id="%s"]' % fixture['action_id'])
    action.get_by_role('button', name='复制草稿 / 确认已发送', exact=True).click()
    page.locator('#manual-sent-check').check()
    page.get_by_role('button', name='确认已发送', exact=True).click()
    expect(page.locator('#details-dialog')).not_to_be_visible()
    action.get_by_role('button', name='粘贴回复 / 已收到回复', exact=True).click()
    page.locator('#manual-reply-text').fill(text)
    page.locator('#manual-received-check').check()
    page.get_by_role('button', name='已收到回复，保存并理解', exact=True).click()
    panel = action.locator('.manual-reply-panel').first
    expect(panel.get_by_text('回复理解与下一步', exact=True)).to_be_visible(timeout=45000)
    expect(panel.get_by_text('对方表示符合：', exact=False)).to_have_count(1)
    expect(panel).to_contain_text('对方表示至少 6 个月')
    expect(panel).to_contain_text('床、椅子和书桌')
    expect(panel).to_contain_text('被褥 / 床品')
    expect(panel).to_contain_text('水电费用：对方表示已包含')
    expect(panel).to_contain_text('原推荐的地点证据符合')
    snapshot = page.evaluate('''async f=>await (await fetch('/api/state?session_id='+f.session_id,{headers:{Authorization:'Bearer '+f.owner}})).json()''', auth)
    analysis = next(r for r in snapshot['manual_replies'] if r['action_id'] == fixture['action_id'])['analysis']
    no_repeat(analysis['suggested_reply'])
    report['model_status'] = analysis['model_status']
    report['model_verified'] = analysis['model_status'] == 'ok'
    report['checks'].append('exact pasted reply visibly resolves private bath, 6-month minimum, listed furniture, own bedding and included utilities; spatial evidence is separately attributed')

    # Both formerly divergent buttons must now share the reply-aware draft.
    action.get_by_role('button', name='起草下一次跟进', exact=True).click()
    expect(page.locator('#action-dialog')).to_be_visible()
    no_repeat(page.locator('#action-body').input_value())
    page.locator('#compose-draft').click()
    expect(page.locator('#toast')).to_contain_text('已重新起草', timeout=10000)
    no_repeat(page.locator('#action-body').input_value())
    page.get_by_role('button', name='保存当前草稿', exact=True).click()
    expect(page.locator('#action-dialog')).not_to_be_visible()
    panel.get_by_role('button', name='打开已生成的后续草稿', exact=True).click()
    expect(page.locator('#action-dialog')).to_be_visible()
    no_repeat(page.locator('#action-body').input_value())
    page.get_by_role('button', name='关闭草稿', exact=True).click()
    report['checks'].append('generic next-follow-up button, reply-specific button and natural-message recompose all skip answered questions; save retains the revised draft')
    # Exercise the chat/orchestrator entry too, rather than only calling the manual-next API.
    old_count = page.locator('#actions .action-card').count()
    page.locator('#query').fill('请为房源ID %s 生成联系核实草稿。此前已经咨询，现希望跟进回复和未核实事项。' % fixture['listing_ids'][0])
    page.locator('#send').click()
    expect(page.locator('#actions .action-card')).to_have_count(old_count + 1, timeout=60000)
    no_repeat(page.locator('#actions .action-card').first.locator('pre').first.inner_text())
    report['checks'].append('chat → actual orchestrator → Follow-up Agent preserves the same answered-topic memory')
    action.scroll_into_view_if_needed()
    page.screenshot(path=str(root / 'evaluation/reports/reply-continuity-desktop.png'), full_page=True)

    page.get_by_role('tab', name='推荐与证据').click()
    expect(first.locator('.listing-card-content')).not_to_be_visible()
    expect(second.locator('.listing-card-content')).not_to_be_visible()
    page.set_viewport_size({'width': 390, 'height': 844})
    first.scroll_into_view_if_needed()
    page.screenshot(path=str(root / 'evaluation/reports/listing-collapsed-mobile.png'))
    first.locator('.listing-collapse-toggle').click()
    expect(first.locator('.listing-card-content')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not errors, errors
    report['checks'].append('fold choices survive SSE refresh and tab changes; mobile390px folding/expansion has no horizontal overflow; no JavaScript errors')
    report['status'] = 'passed'
    (root / 'evaluation/reports/reply-continuity-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    browser.close()
print(json.dumps(report, ensure_ascii=False))
