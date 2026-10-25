"""Verify both editions' real frontend assets with intercepted business APIs; no paid models."""
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
EN = ROOT.name.endswith('-English')
URL = 'http://127.0.0.1:' + ('8020' if EN else '8010')
fixture = json.loads((ROOT / 'data/.runtime/refinement-ui-fixture.json').read_text(encoding='utf-8'))
state = deepcopy(fixture['before'])
rec = state['recommendation']
sid = state['session_id']
base = rec['recommendations'][0]
rows = []
for i in range(10):
    c = deepcopy(base)
    c['rank'] = i + 1
    c['listing'].update(listing_id=str(88000001+i), title=f'Batch Home {i+1}')
    rows.append(c)
rec.update(recommendations=rows, unverified_candidates=[], presentation={'limit': 5, 'batch_size': 5, 'loaded_limit': 10})
state.update(messages=[], actions=[], artifacts=[], refinement_plans=[], pending_operations=[])
errors, queries, details, feedback = [], [], [], []
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    context = browser.new_context(viewport={'width': 1550, 'height': 1000})
    context.add_init_script('localStorage.setItem("rental.owner","isolated-more-ui");localStorage.setItem("rental.session",' + json.dumps(sid) + ');')

    def mock(route):
        req, path = route.request, urlparse(route.request.url).path
        if path == '/api/config':
            value = {'dataset': fixture['dataset'], 'today': '2026-10-24', 'action_mode': 'simulation'}
        elif path == '/api/state':
            value = deepcopy(state)
            value['recommendation']['recommendations'] = value['recommendation']['recommendations'][:rec['presentation']['limit']]
            # An older display module remains available in this browser test;
            # the new UI must never delegate its row count back to that module.
            value['recommendation'].pop('presentation', None)
        elif path == '/api/events':
            route.fulfill(status=200, content_type='text/event-stream', body=': isolated UI test\n\n')
            return
        elif path == '/api/behavior/views':
            value = {'status': 'recorded', 'granted': True}
        elif path == '/api/feedback':
            feedback.append(req.post_data_json)
            value = {'status': 'recorded'}
        elif path == '/api/query':
            body = req.post_data_json
            queries.append(body)
            if len(queries) == 1:
                rec['presentation']['limit'] = 10
                state['messages'].append({'role': 'assistant', 'text': 'Here are five more homes.' if EN else '这次为您补充五套房源。', 'at': '2026-10-24T10:00:00Z'})
            elif 'reset' in body['query']:
                rec['presentation']['limit'] = 5
                state['latest_request_id'] = rec['request_id'] = str(uuid4())
                state['active_operation_id'] = state['recommendation_operation_id'] = str(uuid4())
            state['revision'] = state.get('revision', 0) + 1
            state['last_event_id'] = state.get('last_event_id', 0) + 1
            route.fulfill(status=200, content_type='text/event-stream', body='event: done\ndata: {}\n\n')
            return
        elif path.startswith('/api/listings/') and path.endswith('/presentation'):
            listing_id = path.split('/')[3]
            details.append(listing_id)
            c = next(c for c in rows if c['listing']['listing_id'] == listing_id)
            value = {'listing_id': listing_id, 'source_request_id': state['latest_request_id'], 'dataset': fixture['dataset'],
                     'details': c['listing'], 'photos': [], 'nearby': [], 'warnings': [], 'sections': {'details': 'available', 'photos': 'available', 'nearby': 'available'}, 'photos_has_more': False}
        else:
            errors.append(req.method + ' ' + path)
            route.abort()
            return
        route.fulfill(status=200, content_type='application/json', body=json.dumps(value, ensure_ascii=False))

    context.route('**/api/**', mock)
    context.route('https://**', lambda route: route.abort())
    legacy_reads = []
    def old_selector(route):
        legacy_reads.append(route.request.url)
        route.fulfill(content_type='application/javascript', body='export const displayedCandidates = rec => [...(rec?.recommendations || []), ...(rec?.unverified_candidates || [])].slice(0,5);')
    context.route('**/assets/recommendation-display.js*', old_selector)
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(URL)
    page.wait_for_load_state('networkidle')
    page.wait_for_function('()=>document.body.dataset.ready==="true"')
    cards = page.locator('.listing-card')
    nav = page.locator('#listing-navigator button')
    expect(cards).to_have_count(5)
    first = cards.evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)')
    checks.append('initial five visible with ten reserved')
    original_nodes = cards.element_handles()
    nav.nth(2).click()
    old_scroll = page.locator('#recommendations').evaluate('(e)=>e.scrollTop')
    page.locator('#query').fill('Show me five more homes' if EN else '再帮我多推荐一些')
    page.locator('#send').click()
    expect(cards).to_have_count(10)
    expect(nav).to_have_count(10)
    expect(page.locator('#result-count')).to_have_text('10')
    ids = cards.evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)')
    assert ids[:5] == first and len(set(ids)) == 10
    assert nav.all_text_contents() == [str(i) for i in range(1, 11)]
    assert all(node.evaluate('(e)=>e.isConnected') for node in original_nodes)
    assert abs(page.locator('#recommendations').evaluate('(e)=>e.scrollTop') - old_scroll) < 2
    expect(nav.nth(2)).to_have_attribute('aria-current', 'true')
    checks.append('same-source append adds five cards without replacing the original five or resetting scroll; navigation grows to 1-10')
    page.reload()
    page.wait_for_load_state('networkidle')
    expect(cards).to_have_count(10)
    checks.append('page reload retains ten')
    nav.nth(9).click()
    expect(nav.nth(9)).to_have_attribute('aria-current', 'true')
    cards.nth(9).get_by_role('button', name='View details' if EN else '展开详情', exact=False).click()
    expect(page.locator('#property-detail h2')).to_have_text('Batch Home 10')
    assert details == [rows[9]['listing']['listing_id']]
    page.locator('#property-detail button').first.click()
    cards.nth(9).get_by_role('button', name='Save' if EN else '收藏', exact=True).click()
    page.wait_for_function('()=>document.querySelector("#toast").textContent.length>0')
    assert feedback[-1]['event']['listing_id'] == rows[9]['listing']['listing_id']
    cards.nth(9).get_by_role('button', name='Contact agent' if EN else '联系经纪人', exact=True).click()
    page.wait_for_function('()=>!document.querySelector("#send").disabled')
    assert len(queries) == 2 and rows[9]['listing']['listing_id'] in queries[-1]['query']
    checks.append('tenth home navigation, detail, save and contact use its correct ID')
    page.locator('#query').fill('reset: Change the budget' if EN else 'reset：修改预算')
    page.locator('#send').click()
    expect(cards).to_have_count(5)
    expect(nav).to_have_count(5)
    checks.append('a new recommendation source resets the view to five')
    assert not legacy_reads, legacy_reads
    checks.append('no browser-side five-row clipping, even with a stale legacy selector and no optional presentation metadata')
    assert not errors, errors
    browser.close()
report = {'edition': 'English' if EN else 'Chinese', 'status': 'passed', 'paid_model_calls': 0, 'scope': 'Real frontend assets, intercepted business APIs; database lifecycle tested separately', 'checks': checks}
(ROOT / 'data/.runtime/more-recommendations-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
