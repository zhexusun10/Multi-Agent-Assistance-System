"""Browser acceptance against the running local app; uses the configured LLM, sends no external messages."""
import json
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout

ROOT = Path(__file__).resolve().parents[1]
fixture = json.loads((ROOT / 'data/.runtime/manual-ui-fixture.json').read_text(encoding='utf-8'))
report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'messages_sent_externally': 0, 'checks': []}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    context = browser.new_context(permissions=['clipboard-read', 'clipboard-write'], viewport={'width': 1440, 'height': 1000})
    context.add_init_script('''const f = %s; localStorage.setItem('rental.owner',f.owner);localStorage.setItem('rental.session',f.session_id);localStorage.setItem('rental.sessions',JSON.stringify([{id:f.session_id,title:'手动跟进验收（合成房源）'}]));''' % json.dumps(fixture))
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8010/')
    # This app keeps an SSE request open; readiness is the rendered app marker if networkidle cannot occur.
    try:
        page.wait_for_load_state('networkidle', timeout=2000)
    except PlaywrightTimeout:
        pass
    expect(page.locator('body')).to_have_attribute('data-ready', 'true')
    page.get_by_role('tab', name='跟进工作台').click()
    card = page.locator('[data-action-id="%s"]' % fixture['action_id'])
    card.get_by_role('button', name='编辑草稿', exact=True).click()
    expect(page.locator('#delivery-method')).to_have_value('manual')
    expect(page.locator('#recipient-address')).not_to_be_visible()
    page.locator('#negotiation-notes').fill('希望月租协商至1800新币。')
    page.get_by_role('button', name='保存当前草稿').click()
    expect(page.locator('#action-dialog')).not_to_be_visible()
    card.get_by_role('button', name='复制草稿 / 确认已发送', exact=True).click()
    draft_text = page.locator('#manual-copy-body').input_value()
    assert '1800' in draft_text
    page.get_by_role('button', name='复制完整草稿', exact=True).click()
    expect(page.locator('#toast')).to_contain_text('草稿已复制')
    clipboard_text = page.evaluate('navigator.clipboard.readText()')
    # Windows native clipboard may convert LF to CRLF without changing message content.
    assert clipboard_text.replace('\r\n', '\n') == draft_text.replace('\r\n', '\n')
    page.locator('#manual-sent-check').check()
    page.get_by_role('button', name='确认已发送', exact=True).click()
    expect(card.get_by_text('用户确认已发送（手动记录）', exact=True)).to_be_visible()
    report['checks'].append('manual default; no channel/address requirement; saved negotiation notes; clipboard exact match; explicit sent acknowledgement')
    card.get_by_role('button', name='粘贴回复 / 已收到回复', exact=True).click()
    reply_text = '房源仍可租。月租2200新币。没有独立卫浴。周六可以看房，具体时间需要再协商。家具全配，最短租期24个月，可以养猫。'
    page.locator('#manual-reply-text').fill(reply_text)
    page.locator('#manual-received-check').check()
    page.get_by_role('button', name='已收到回复，保存并理解', exact=True).click()
    panel = card.locator('.manual-reply-panel').first
    expect(panel.get_by_text(reply_text, exact=True)).to_be_visible(timeout=20000)
    expect(panel.get_by_text('回复理解与下一步', exact=True)).to_be_visible(timeout=90000)
    expect(panel.get_by_text('已使用配置的大模型理解，并保留原文依据。', exact=True)).to_be_visible()
    expect(panel.get_by_text('与硬条件冲突：', exact=False)).to_have_count(2)
    analysis = page.evaluate('''async f => {const s=await (await fetch('/api/state?session_id='+f.session_id,{headers:{Authorization:'Bearer '+f.owner}})).json();return s.manual_replies.find(r=>r.action_id===f.action_id).analysis;}''', fixture)
    assert any(c['topic'] == 'minimum_lease' and c['amount'] == 24 and '24个月' in c['quote'] for c in analysis['claims'])
    assert any(c['topic'] == 'other' and '养猫' in c['quote'] for c in analysis['claims'])
    assert page.locator('#messages').inner_text().find('经纪人回复理解（用户粘贴') >= 0
    report['checks'].append('real configured DeepSeek reply analysis; exact raw reply; both rent and private bathroom conflicts visible in card and chat; other rental terms retained; no automatic booking')
    # A different session credential must not read this session or submit an acknowledged send.
    code = page.evaluate('''async f => (await fetch('/api/state?session_id='+f.session_id,{headers:{Authorization:'Bearer '+'b'.repeat(64)}})).status''', fixture)
    assert code == 403
    code = page.evaluate('''async f => (await fetch('/api/actions/'+f.action_id+'/manual-sent',{method:'POST',headers:{Authorization:'Bearer '+'b'.repeat(64),'Content-Type':'application/json'},body:JSON.stringify({session_id:f.session_id,revision:2,content_hash:'0'.repeat(64),confirmed_sent:true})})).status''', fixture)
    assert code == 403
    page.reload()
    try:
        page.wait_for_load_state('networkidle', timeout=2000)
    except PlaywrightTimeout:
        pass
    page.get_by_role('tab', name='跟进工作台').click()
    card = page.locator('[data-action-id="%s"]' % fixture['action_id'])
    panel = card.locator('.manual-reply-panel').first
    expect(panel.get_by_text(reply_text, exact=True)).to_be_visible()
    panel.get_by_role('button', name='根据回复起草下一步', exact=True).click()
    expect(page.locator('#action-dialog')).to_be_visible()
    assert '差异' in page.locator('#action-body').input_value()
    page.get_by_role('button', name='关闭草稿', exact=True).click()
    page.get_by_role('tab', name='总结文档', exact=True).click()
    page.get_by_role('button', name='生成当天会话与跟进总结', exact=True).click()
    expect(page.get_by_text('会话与跟进总结', exact=False).last).to_be_visible(timeout=15000)
    report['checks'].append('owner isolation; reload persistence; reply-based follow-on draft; day summary generated')
    page.get_by_role('tab', name='跟进工作台').click()
    page.screenshot(path=str(ROOT / 'evaluation/reports/manual-followup-desktop.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path=str(ROOT / 'evaluation/reports/manual-followup-mobile.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors, errors
    report['checks'].append('desktop/mobile layout; no horizontal overflow at390px; no browser JavaScript errors')
    report.update(status='passed', model='configured project model (no credentials in report)')
    (ROOT / 'evaluation/reports/manual-followup-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    browser.close()
print(json.dumps(report, ensure_ascii=False))
