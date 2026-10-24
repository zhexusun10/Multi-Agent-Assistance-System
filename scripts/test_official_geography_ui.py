"""Real UI + locally generated real-data result; no model/business requests."""
import json
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
result = json.loads((ROOT / 'data/.runtime/official-geo-ui-result.json').read_text(encoding='utf-8'))
state = deepcopy(json.loads((ROOT / 'data/.runtime/refinement-budget-ui-fixture.json').read_text(encoding='utf-8'))['after'])
rec = result['recommendation']
state.update(requirements=result['requirements'], requirements_version=rec['requirements_version'], recommendation=rec,
             latest_request_id=rec['request_id'], refinement_plans=[], messages=[{'role': 'assistant', 'text': result['summary']}],
             actions=[], artifacts=[], pending_operations=[])
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    context = browser.new_context(viewport={'width': 1550, 'height': 1000})
    context.add_init_script('localStorage.clear();localStorage.setItem("rental.owner","official-geo-ui-test");localStorage.setItem("rental.session",' + json.dumps(state['session_id']) + ');')

    def mock(route):
        path = urlparse(route.request.url).path
        if path == '/api/config':
            value = {'dataset': '2026-10-09', 'today': '2026-10-24', 'action_mode': 'simulation'}
        elif path == '/api/state':
            value = state
        elif path == '/api/events':
            route.fulfill(status=200, content_type='text/event-stream', body=': local test\n\n')
            return
        elif path == '/api/behavior/views':
            value = {'status': 'recorded', 'granted': False}
        elif path.startswith('/api/listings/') and path.endswith('/presentation'):
            id = path.split('/')[3]
            listing = next(c['listing'] for c in rec['recommendations'] if c['listing']['listing_id'] == id)
            value = {'listing_id': id, 'source_request_id': rec['request_id'], 'dataset': '2026-10-09', 'details': listing,
                     'photos': [], 'nearby': [], 'warnings': [], 'photos_has_more': False,
                     'sections': {'details': 'available', 'photos': 'available', 'nearby': 'available'}}
        else:
            errors.append(path)
            route.abort()
            return
        route.fulfill(status=200, content_type='application/json', body=json.dumps(value, ensure_ascii=False))

    context.route('**/api/**', mock)
    context.route('https://**', lambda r: r.abort())
    page = context.new_page()
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto('http://127.0.0.1:8010/?v=official-geo')
    page.wait_for_load_state('networkidle')
    cards = page.locator('.listing-card')
    expect(cards).to_have_count(5)
    expect(page.locator('#listing-navigator button')).to_have_count(5)
    for index, c in enumerate(rec['recommendations']):
        distance = next(r['observed_value'] for r in c['criterion_results'] if r['criterion_id'].startswith('place_proximity:URA:'))
        expect(cards.nth(index).locator('.match-chips')).to_contain_text(f'金文泰区域边界 {round(distance):,} 米')
        expect(cards.nth(index).locator('.pending-summary')).not_to_contain_text('金文泰')
    cards.first.get_by_role('button', name='展开详情', exact=False).click()
    expect(page.locator('#property-detail .detail-match')).to_contain_text('金文泰区域边界 6,300 米')
    expect(page.locator('#messages')).to_contain_text('距金文泰区域边界直线约6,300米')
    page.get_by_role('button', name='← 返回推荐列表', exact=True).click()
    page.screenshot(path=str(ROOT / 'evaluation/reports/official-geography-ui.png'), full_page=True)
    assert not errors, errors
    browser.close()
report = {'status': 'passed', 'model_calls': 0, 'visible_count': 5,
          'checks': ['real computed boundary distance on each of five cards', 'known distance removed from pending items',
                     'detail and narrative preserve the same distance', 'soft preference remains neutral and truthful about long distance'],
          'scope': 'real frontend with saved real SQL/KG/native results; intercepted business APIs'}
(ROOT / 'evaluation/reports/official-geography-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
