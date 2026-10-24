"""English layout/interaction replay using real saved state and SQL/KG details.

No model requests or external messages. The separate real refinement acceptance
tests live HTTP/SSE; this replay isolates desktop/mobile presentation checks.
"""
import json
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root/'data/.runtime/english-ui-fixture.json').read_text(encoding='utf-8'))
state, presentations = fixture['state'], fixture['presentation']
sid = state['session_id']
config = json.load(urlopen('http://127.0.0.1:8020/api/config'))
out = root/'data/.runtime/english-ui'
out.mkdir(exist_ok=True)
errors, unexpected, behavior, queries = [], [], [], []
report = {'model_calls': 0, 'api_mode': 'saved real-state replay; real SQL/KG presentation records', 'checks': []}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    def ready(page):
        try:
            page.wait_for_load_state('networkidle', timeout=2500)
        except PlaywrightTimeout:
            pass
        page.wait_for_selector('body[data-ready=true]')
    # Also verify the actual application serves a usable fresh English session.
    live = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = live.new_page()
    page.goto('http://127.0.0.1:8020/')
    ready(page)
    expect(page.locator('html')).to_have_attribute('lang', 'en-SG')
    expect(page.get_by_role('heading', name='Find a home that fits your life.')).to_be_visible()
    owner, created = page.evaluate('[localStorage.getItem("rental.owner"),localStorage.getItem("rental.session")]')
    response = live.request.delete(f'http://127.0.0.1:8020/api/sessions/{created}', headers={'Authorization': 'Bearer '+owner})
    assert response.ok
    live.close()
    report['checks'].append('live English application bootstrap and independent session deletion')

    def mock(route):
        path = urlparse(route.request.url).path
        if path == '/api/config':
            value = config
        elif path == '/api/state':
            value = state
        elif path == '/api/events':
            route.fulfill(status=200, content_type='text/event-stream', body=': layout replay\n\n')
            return
        elif path.startswith('/api/listings/') and path.endswith('/presentation'):
            value = presentations[path.split('/')[3]]
        elif path == '/api/behavior/views':
            behavior.append(route.request.post_data_json)
            value = {'status': 'recorded', 'granted': True}
        elif path == '/api/feedback':
            value = {'status': 'recorded'}
        elif path == '/api/query':
            queries.append(route.request.post_data_json)
            raise AssertionError('Layout tests must not ask a model')
        else:
            unexpected.append(path)
            value = {}
        route.fulfill(status=200, json=value)

    for width, height, label in [(1600, 1000, 'desktop'), (1024, 900, 'tablet'), (390, 844, 'mobile')]:
        context = browser.new_context(viewport={'width': width, 'height': height})
        context.add_init_script('localStorage.setItem("rental.owner","english-layout-replay");localStorage.setItem("rental.session",'+json.dumps(sid)+');')
        context.route('**/api/**', mock)
        page = context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto('http://127.0.0.1:8020/')
        ready(page)
        expect(page.locator('.listing-card')).to_have_count(5)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), label
        if label == 'mobile':
            page.locator('.listing-card').first.scroll_into_view_if_needed()
        page.screenshot(path=str(out/(label+'-list.png')))
        page.locator('.listing-card').first.get_by_role('button', name='View details', exact=False).click()
        expect(page.locator('.detail-complete-facts')).to_have_attribute('data-loaded', 'true')
        expect(page.get_by_role('heading', name='Property details', exact=True)).to_be_visible()
        expect(page.locator('.property-facts').get_by_text('Number of bathrooms', exact=True)).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), label+' details'
        try:
            page.wait_for_function("()=>{const i=document.querySelector('.gallery-frame img');return !i||i.complete}", timeout=12000)
        except PlaywrightTimeout:
            pass  # Remote snapshot image availability does not govern local facts.
        page.locator('.property-detail-body').evaluate('e=>e.scrollTop=0')
        page.screenshot(path=str(out/(label+'-details.png')))
        page.locator('.property-detail-body').click(position={'x': 30, 'y': 100})
        expect(page.locator('#property-detail')).to_have_attribute('data-reading-state', 'active', timeout=6000)
        page.get_by_role('button', name='View all', exact=False).click()
        expect(page.locator('#details-dialog')).to_be_visible()
        assert page.locator('#details-dialog').get_attribute('data-reading-listing-id') == state['recommendation']['recommendations'][0]['listing']['listing_id']
        page.locator('#details-dialog .close-dialog').click()
        page.locator('.property-detail-header').get_by_role('button', name='Back to recommendations', exact=False).click()
        # Use the real English date-picker module, without submitting any draft.
        page.evaluate('''async()=>{
          const {DateTimePicker}=await import('/assets/date-time-picker.js');
          const label=document.createElement('label');label.textContent='Viewing date';label.style.cssText='position:fixed;top:20px;left:20px;z-index:10000;background:white';
          const input=document.createElement('input');input.type='date';input.id='english-date-check';label.append(input);
          document.body.append(label);const picker=new DateTimePicker();picker.attach(input);picker.open(input);
        }''')
        popup = page.locator('.date-time-popover')
        expect(popup.get_by_role('columnheader').first).to_have_text('Mon')
        assert popup.get_by_label('Month', exact=True).locator('option').first.inner_text() == 'January'
        page.screenshot(path=str(out/(label+'-date-picker.png')))
        popup.get_by_role('gridcell').filter(has_text='15').first.click()
        expect(page.locator('#english-date-check')).to_have_value('')
        popup.get_by_role('button', name='Confirm', exact=True).click()
        assert page.locator('#english-date-check').input_value()
        context.close()
        report['checks'].append(label+': Top5, no horizontal overflow, full details, photo dialog reading identity, active-view status and English staged date selection')
    browser.close()

assert not errors, errors
assert not unexpected, unexpected
assert not queries, queries
assert behavior
report.update(status='passed', page_errors=errors, unexpected_routes=unexpected)
(out/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
