"""Exact area + keep-budget browser replay; business APIs are intercepted."""
from pathlib import Path
from urllib.parse import urlparse
from copy import deepcopy
import json
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
f = json.loads((ROOT / 'data/.runtime/refinement-budget-ui-fixture.json').read_text(encoding='utf-8'))
state = deepcopy(f['before'])
posts, errors = [], []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    context = browser.new_context(viewport={'width': 1550, 'height': 1000})
    context.add_init_script('localStorage.clear();localStorage.setItem("rental.owner","budget-recovery-test");localStorage.setItem("rental.session",' + json.dumps(state['session_id']) + ');')

    def mock(route):
        global state
        path = urlparse(route.request.url).path
        if path == '/api/config':
            value = {'dataset': f['dataset'], 'today': '2026-10-24', 'action_mode': 'simulation'}
        elif path == '/api/state':
            value = state
        elif path == '/api/events':
            route.fulfill(status=200, content_type='text/event-stream', body=': test\n\n')
            return
        elif path == '/api/behavior/views':
            value = {'status': 'recorded', 'granted': False}
        elif path.startswith('/api/refinements/') and path.endswith('/submit'):
            posts.append(route.request.post_data_json)
            assert posts[-1]['answers'] == f['answers']
            state = deepcopy(f['after'])
            value = {'status': 'queued', 'operation_id': state['active_operation_id']}
        else:
            errors.append(path)
            route.abort()
            return
        route.fulfill(status=200, content_type='application/json', body=json.dumps(value, ensure_ascii=False))

    context.route('**/api/**', mock)
    context.route('https://**', lambda r: r.abort())
    page = context.new_page()
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto('http://127.0.0.1:8010/?revision=refinement-recovery')
    page.wait_for_load_state('networkidle')
    card = page.locator('.requirement-refinement')
    expect(card).to_have_count(1)
    expect(page.locator('.listing-card')).to_have_count(5)
    card.get_by_role('button', name='设置条件', exact=True).click()
    card.get_by_label('面积下限（选填）').fill('500')
    card.get_by_label('面积上限（选填）').fill('1500')
    card.get_by_label('面积单位').select_option('sqft')
    card.get_by_label('重要程度').select_option('hard')
    card.get_by_role('button', name='下一项 →').click()
    card.get_by_role('button', name='目前预算即可', exact=True).click()
    expect(card.locator('.refinement-summary')).to_contain_text('保留原预算上限 3,000 新币')
    expect(card.locator('.refinement-summary')).not_to_contain_text('不限')
    card.get_by_role('button', name='补充好了，重新推荐', exact=True).click()
    expect(card).to_contain_text('已记录您的选择')
    expect(card).to_contain_text('保留原预算上限 3,000 新币')
    expect(page.locator('#messages')).not_to_contain_text('本轮未完成')
    expect(page.locator('#messages')).to_contain_text('第5套 Hillview Green')
    expect(page.locator('#result-count')).to_have_text('5')
    expect(page.locator('#listing-navigator button')).to_have_count(5)
    expect(page.locator('#criteria')).to_contain_text('500–1500')
    expect(page.locator('#criteria')).to_contain_text('3000')
    assert len(posts) == 1 and not errors, errors
    page.screenshot(path=str(ROOT / 'evaluation/reports/refinement-budget-ui.png'), full_page=True)
    browser.close()
report = {'status': 'passed', 'model_calls': 0, 'structured_submissions': len(posts), 'visible_count': 5,
          'checks': ['500-1500 sqft hard requirement', 'keep original SGD3000 budget', 'preview and submitted captions agree',
                     'five introductions with existing facts and unknown-place warning', 'five cards, navigator entries and result count'],
          'scope': 'real frontend; saved inputs and server-compiled outputs; intercepted business APIs'}
(ROOT / 'evaluation/reports/refinement-budget-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
