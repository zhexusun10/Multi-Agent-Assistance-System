"""Exercise real frontend assets against a saved nine-home result. Zero model calls."""
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
fixture = json.loads((ROOT / 'data/.runtime/refinement-ui-fixture.json').read_text(encoding='utf-8'))
state = deepcopy(fixture['before'])
sid = state['session_id']
rec = state['recommendation']
templates = rec['recommendations'] + rec['unverified_candidates']
assert templates
rows = []
for i in range(9):
    row = deepcopy(templates[i % len(templates)])
    row['rank'] = i + 1 if i < 5 else i - 4
    row['listing'].update(listing_id=str(86000001 + i), title=f'测试房源 {i + 1}')
    rows.append(row)
rec.update(recommendations=rows[:5], unverified_candidates=rows[5:])
state.update(messages=[], actions=[], artifacts=[], refinement_plans=[], pending_operations=[])
errors, queries, presentations, feedback = [], [], [], []
checks = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    context = browser.new_context(viewport={'width': 1550, 'height': 1000})
    context.add_init_script('localStorage.setItem("rental.owner","isolated-top5-display");localStorage.setItem("rental.session",' + json.dumps(sid) + ');')

    def mock(route):
        req = route.request
        path = urlparse(req.url).path
        if path == '/api/config':
            value = {'dataset': fixture['dataset'], 'today': '2026-10-24', 'action_mode': 'simulation'}
        elif path == '/api/state':
            value = deepcopy(state)
            value['recommendation']['unverified_candidates'] = []
        elif path == '/api/events':
            route.fulfill(status=200, content_type='text/event-stream', body=': offline UI test\n\n')
            return
        elif path == '/api/behavior/views':
            value = {'status': 'recorded', 'granted': True}
        elif path == '/api/feedback':
            feedback.append(req.post_data_json)
            value = {'status': 'recorded'}
        elif path == '/api/query':
            queries.append(req.post_data_json)
            route.fulfill(status=200, content_type='text/event-stream', body='event: done\ndata: {}\n\n')
            return
        elif path.startswith('/api/listings/') and path.endswith('/presentation'):
            listing_id = path.split('/')[3]
            presentations.append(listing_id)
            c = next(c for c in rows if c['listing']['listing_id'] == listing_id)
            value = {'listing_id': listing_id, 'source_request_id': state['latest_request_id'], 'dataset': fixture['dataset'],
                     'details': c['listing'], 'photos': [], 'nearby': [], 'warnings': [],
                     'sections': {'details': 'available', 'photos': 'available', 'nearby': 'available'}, 'photos_has_more': False}
        else:
            errors.append(req.method + ' ' + path)
            route.abort()
            return
        route.fulfill(status=200, content_type='application/json', body=json.dumps(value, ensure_ascii=False))

    context.route('**/api/**', mock)
    context.route('https://**', lambda route: route.abort())
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8010/')
    page.wait_for_load_state('networkidle')
    page.wait_for_function('()=>document.body.dataset.ready==="true"')
    cards = page.locator('.listing-card')
    nav = page.locator('#listing-navigator button')
    expect(cards).to_have_count(5)
    expect(nav).to_have_count(5)
    expect(page.locator('#result-count')).to_have_text('5')
    assert cards.evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)') == [r['listing']['listing_id'] for r in rows[:5]]
    assert nav.all_text_contents() == ['1', '2', '3', '4', '5']
    checks.append('saved 5+4 response renders five cards, five navigation items and count five')

    nav.nth(4).click()
    expect(nav.nth(4)).to_have_attribute('aria-current', 'true')
    position = page.locator('#recommendations').evaluate('(e)=>e.scrollTop')
    assert position > 0
    cards.nth(4).get_by_role('button', name='展开详情', exact=False).click()
    expect(page.locator('#property-detail h2')).to_have_text('测试房源 5')
    expect(page.locator('.detail-complete-facts')).not_to_contain_text('正在读取')
    page.get_by_role('button', name='← 返回推荐列表', exact=True).click()
    assert abs(page.locator('#recommendations').evaluate('(e)=>e.scrollTop') - position) < 2
    assert presentations == [rows[4]['listing']['listing_id']]
    checks.append('fifth navigation, detail identity and return scroll position agree')

    cards.nth(4).get_by_role('button', name='收藏', exact=True).click()
    page.wait_for_function('()=>document.querySelector("#toast").textContent.includes("已收藏")')
    assert feedback[-1]['event']['listing_id'] == rows[4]['listing']['listing_id']
    cards.nth(4).get_by_role('button', name='联系经纪人', exact=True).click()
    page.wait_for_function('()=>!document.querySelector("#send").disabled')
    assert len(queries) == 1 and rows[4]['listing']['listing_id'] in queries[0]['query']
    checks.append('save and contact retain the fifth listing ID without triggering a real model request')

    page.reload()
    page.wait_for_load_state('networkidle')
    expect(page.locator('.listing-card')).to_have_count(5)
    expect(page.locator('#listing-navigator button')).to_have_count(5)
    checks.append('refresh of the unchanged nine-home saved response still shows five')
    page.screenshot(path=str(ROOT / 'evaluation/reports/top5-display-ui.png'), full_page=True)
    assert len(rec['recommendations']) == 5 and len(rec['unverified_candidates']) == 4
    assert not errors, errors
    browser.close()

report = {'status': 'passed', 'model_calls': 0, 'mocked_query_posts': len(queries), 'raw_count': 9,
          'visible_count': 5, 'checks': checks, 'scope': 'real UI and shared compiled selector; intercepted business APIs'}
(ROOT / 'evaluation/reports/top5-display-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
