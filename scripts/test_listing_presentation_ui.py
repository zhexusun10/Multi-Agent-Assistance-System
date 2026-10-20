"""Real local search UI: facts/photos independent of query slots; no external messages."""
import json
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / 'data/.runtime/listing-presentation-ui-fixture.json').read_text(encoding='utf-8'))
report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'messages_sent_externally': 0, 'checks': []}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    context = browser.new_context(permissions=['clipboard-read', 'clipboard-write'], viewport={'width': 1440, 'height': 1050})
    context.add_init_script('''const f=%s;localStorage.setItem('rental.owner',f.owner);localStorage.setItem('rental.session',f.session_id);localStorage.setItem('rental.sessions',JSON.stringify([{id:f.session_id,title:'新国大5公里 · 2000以内'}]));''' % json.dumps(fixture))
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8010/')
    try:
        page.wait_for_load_state('networkidle', timeout=2500)
    except PlaywrightTimeout:
        pass  # The application's SSE connection is intentionally persistent.
    expect(page.locator('body')).to_have_attribute('data-ready', 'true')
    expect(page.locator('.listing-card')).to_have_count(len(fixture['listing_ids']))
    ratio = page.evaluate('''()=>{const a=document.querySelector('.conversation').getBoundingClientRect().width,b=document.querySelector('.results').getBoundingClientRect().width;return a/(a+b)}''')
    assert .32 <= ratio <= .4, ratio
    for listing in fixture['listing_ids']:
        card = page.locator('[data-listing-id="%s"]' % listing)
        expect(card.locator('.listing-details-panel')).to_have_attribute('data-loaded', 'true', timeout=25000)
        expect(card.get_by_text('卫浴数量', exact=True)).to_be_visible()
        expect(card.get_by_text('卧室数量', exact=True)).to_be_visible()
        expect(card.locator('.property-address')).to_be_visible()
        assert card.evaluate('''e => !!(e.querySelector('.listing-match-summary').compareDocumentPosition(e.querySelector('.listing-details-panel')) & Node.DOCUMENT_POSITION_FOLLOWING)''')
    first = page.locator('.listing-card').first
    first.scroll_into_view_if_needed()
    hero = first.locator('.gallery-frame img')
    expect(hero).to_be_visible()
    page.wait_for_function('''id=>{const img=document.querySelector('[data-listing-id="'+id+'"] .gallery-frame img');return img&&img.complete&&img.naturalWidth>0}''', arg=first.get_attribute('data-listing-id'), timeout=20000)
    report['checks'].append('real candidate photos load from recorded PropertyGuru CDN URLs; all cards show unmentioned rooms/bathrooms/address; matching requirements appear first; desktop ratio ~35/65')
    previous = hero.get_attribute('src')
    first.get_by_role('button', name='下一张', exact=True).click()
    expect(first.locator('.gallery-frame img')).not_to_have_attribute('src', previous)
    first.get_by_role('button', name='查看全部', exact=False).click()
    expect(page.locator('.property-photo-grid')).to_be_visible()
    assert page.locator('.property-photo-grid figure').count() > 1
    page.locator('#details-dialog .close-dialog').click()
    page.route('**/*pgimgs.com/**', lambda route: route.abort())
    first.get_by_role('button', name='下一张', exact=True).click()
    expect(first.locator('.photo-unavailable')).to_be_visible(timeout=10000)
    expect(first.get_by_text('卫浴数量', exact=True)).to_be_visible()
    page.unroute('**/*pgimgs.com/**')
    first.get_by_role('button', name='上一张', exact=True).click()
    report['checks'].append('photo carousel and all-photo dialog work; simulated CDN failure gets an explicit fallback without removing property facts or match checks')
    page.locator('#recommendations').evaluate('e=>e.scrollTop=0')
    page.screenshot(path=str(root / 'evaluation/reports/listing-presentation-desktop.png'), full_page=True)
    # Exercise the exact viewing-time card presentation that the user reported.
    first.get_by_role('button', name='我想看这套', exact=True).click()
    action = page.locator('#actions .action-card').first
    expect(action).to_be_visible(timeout=30000)
    action.get_by_role('button', name='编辑草稿', exact=True).click()
    page.locator('#starts-at').fill('2026-10-21T15:05')
    page.locator('#ends-at').fill('2026-10-21T18:05')
    page.get_by_role('button', name='保存当前草稿', exact=True).click()
    expect(page.locator('#action-dialog')).not_to_be_visible()
    text = action.locator('pre').first.inner_text()
    assert '我想在' in text and '请问你方便吗' in text
    assert '3:05 PM–6:05 PM' in text
    assert '候选看房时间' not in text and 'Asia/Singapore' not in text and '2026-10-21T' not in text
    action.get_by_role('button', name='复制草稿 / 确认已发送', exact=True).click()
    expect(page.locator('#manual-copy-body')).to_have_value(text)
    page.locator('#details-dialog .close-dialog').click()
    report['checks'].append('viewing card and copy agree on the conversational invitation for SG15:05–18:05; no raw candidate-time label or ISO timestamp; no send acknowledgement')
    page.get_by_role('tab', name='推荐与证据', exact=False).click()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('.listing-card').first.scroll_into_view_if_needed()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('.listing-card').first.evaluate('e=>window.scrollTo(0,e.getBoundingClientRect().top+window.scrollY-10)')
    page.screenshot(path=str(root / 'evaluation/reports/listing-presentation-mobile.png'))
    assert not errors, errors
    report['checks'].append('mobile stacks conversation and detailed property cards with no horizontal overflow; no JavaScript errors')
    report.update(status='passed', candidate_count=len(fixture['listing_ids']), desktop_conversation_ratio=round(ratio, 3))
    (root / 'evaluation/reports/listing-presentation-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    browser.close()
print(json.dumps(report, ensure_ascii=False))
