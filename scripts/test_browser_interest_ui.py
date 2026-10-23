"""Real timed browser acceptance. No manual duration samples or edited browser clocks."""
from pathlib import Path
from datetime import datetime, timezone
import json, math, time, uuid, sys
import httpx
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from playwright.sync_api import sync_playwright, expect, TimeoutError as BrowserTimeout

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8010'
owner = str(uuid.uuid4()) + str(uuid.uuid4())
resume = json.loads((ROOT / 'data/.runtime/browser-interest-session.json').read_text(encoding='utf-8')) if '--resume' in sys.argv else None
if resume: owner = resume['owner']
headers = {'Authorization': 'Bearer ' + owner}
client = httpx.Client(base_url=BASE, headers=headers, timeout=180)
report = {'started_at': datetime.now(timezone.utc).isoformat(), 'checks': [], 'external_messages_sent': 0}

def api(path, body=None):
    r = client.get('/api/' + path) if body is None else client.post('/api/' + path, json=body)
    r.raise_for_status()
    return r.json()

def state():
    return api('state?session_id=' + sid)

def wait_result(previous=None):
    deadline = time.monotonic() + 160
    while time.monotonic() < deadline:
        s = state()
        if s.get('recommendation') and s.get('latest_request_id') != previous and (s.get('explanation') or {}).get('source_request_id') == s['latest_request_id']:
            return s
        if s['messages'] and s['messages'][-1]['text'].startswith('本轮未完成'):
            raise AssertionError(s['messages'][-1]['text'])
        time.sleep(1)
    raise AssertionError('Recommendation did not complete within acceptance deadline')

def query(message):
    s = state()
    r = client.post('/api/query', json={'session_id': sid, 'query': message, 'request_id': str(uuid.uuid4()), **({'source_request_id': s['latest_request_id']} if s.get('latest_request_id') else {})})
    r.raise_for_status()

def exchange(s):
    return api('evidence?session_id=' + sid + '&ref=' + s['recommendation']['native']['exchange_ref'])['data']

def oracle(s, previous):
    ex = exchange(s)
    events = ex['request']['feedback']
    assert events and all(e['source'] == 'web_detail' for e in events)
    assert len({e['listing_id'] for e in events}) >= 2
    refs = {c['listing']['listing_id']: c['listing'] for c in previous['recommendation']['recommendations']}
    totals = {}
    for event in events:
        totals[event['listing_id']] = totals.get(event['listing_id'], 0) + event['payload']['duration_seconds']
    weights = {k: math.log1p(min(t, 120)) / math.log(121) for k, t in totals.items()}
    denominator = sum(weights.values())
    values = dotenv_values(ROOT / '.env')
    engine = create_engine(values['PROPERTYGURU_READ_DATABASE_URL'])
    budget = next(c['value'] for c in s['requirements']['criteria'] if c['field'] == 'price')
    try:
        with engine.connect() as c:
            rows = [dict(r) for r in c.execute(text("SELECT listing_id,price,property_type,floor_area_sqft FROM properties WHERE listing_type='RENT' AND district_code='D05' AND bedrooms>=2 AND price<=:budget"), {'budget': budget}).mappings()]
    finally:
        engine.dispose()
    def fit(row):
        total = 0
        for ident, weight in weights.items():
            ref = refs[ident]
            kind = str(row['property_type']).strip().lower() if row['property_type'] else None
            same = float(kind == ref['property_type']) if kind is not None and ref['property_type'] is not None else 0
            area = row['floor_area_sqft']
            similarity = max(0, 1 - abs(float(area) - ref['floor_area_sqft']) / 500) if area is not None and ref['floor_area_sqft'] is not None else 0
            total += weight / denominator * (.5 * same + .5 * similarity)
        return total
    rows.sort(key=lambda r: (-fit(r), str(r['listing_id'])))
    rec = s['recommendation']
    assert rec['state']['counts']['eligible'] == len(rows)
    assert [c['listing']['listing_id'] for c in rec['recommendations']] == [str(r['listing_id']) for r in rows[:4]]
    for candidate in rec['recommendations']:
        scores = rec['native']['scores'][candidate['listing']['listing_id']]
        row = next(r for r in rows if str(r['listing_id']) == candidate['listing']['listing_id'])
        assert abs(scores['implicit_score'] - fit(row)) < 1e-9
        assert scores['implicit_weight'] == 1 and scores['explicit_weight'] == 0
    return {'events': len(events), 'seconds_by_listing': totals, 'eligible': len(rows), 'top4': [str(r['listing_id']) for r in rows[:4]], 'scoring': 'independent SQL and property/area similarity oracle'}

sid = resume['session_id'] if resume else api('sessions', {})['session_id']
try:
    print('Starting a fresh real-model search against the full dataset.', flush=True)
    if not resume: query('我要租D05的房子，预算5000新币每月，至少2间卧室')
    baseline = wait_result() if not resume else state()
    assert baseline['recommendation']['state']['counts']['assessed'] == 21431
    report['baseline'] = {'request': baseline['latest_request_id'], 'eligible': baseline['recommendation']['state']['counts']['eligible']}
    (ROOT / 'data/.runtime/browser-interest-session.json').write_text(json.dumps({'owner': owner, 'session_id': sid}), encoding='utf-8')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel='chrome')
        context = browser.new_context(viewport={'width': 1600, 'height': 1000})
        fixture = {'owner': owner, 'session_id': sid}
        context.add_init_script('const f=' + json.dumps(fixture) + ';localStorage.setItem("rental.owner",f.owner);localStorage.setItem("rental.session",f.session_id);')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(BASE)
        try: page.wait_for_load_state('networkidle', timeout=2000)
        except BrowserTimeout: pass  # Persistent SSE is intentional.
        expect(page.locator('body')).to_have_attribute('data-ready', 'true')
        expect(page.get_by_role('button', name='录入浏览样例并重排')).to_have_count(0)
        expect(page.locator('.listing-card')).to_have_count(len(baseline['recommendation']['recommendations']) + len(baseline['recommendation']['unverified_candidates']))
        ids = page.locator('.listing-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)')
        page.screenshot(path=str(ROOT / 'evaluation/reports/browser-interest-list.png'), full_page=True)
        for index in (0, 1):
            page.get_by_role('button', name='展开详情 ↗').nth(index).click()
            expect(page.locator('#property-detail [data-loaded=true]')).to_be_visible(timeout=15000)
            expect(page.locator('#property-detail')).to_have_attribute('data-reading-state','active', timeout=8000)
            assert page.locator('#property-detail').bounding_box()['width'] > 900
            if index == 0: page.screenshot(path=str(ROOT / 'evaluation/reports/browser-interest-detail.png'), full_page=True)
            print('Reading listing %d for 32 real seconds, with deliberate scroll input.' % (index + 1), flush=True)
            for _ in range(4):
                page.wait_for_timeout(8000)
                page.mouse.move(1200, 700)
                page.mouse.wheel(0, 100)
            page.get_by_role('button', name='← 返回推荐列表').click()
            page.wait_for_timeout(1800)
            assert page.locator('.listing-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)') == ids
            assert state()['latest_request_id'] == baseline['latest_request_id']
        # Losing focus to a real second tab pauses the first view.
        page.get_by_role('button', name='展开详情 ↗').first.click()
        expect(page.locator('#property-detail')).to_have_attribute('data-reading-state','active', timeout=8000)
        page.wait_for_timeout(2000)
        other = context.new_page(); other.goto('about:blank'); other.bring_to_front()
        page.wait_for_timeout(1800)
        paused = api('behavior/summary?session_id=' + sid)['active_duration_ms']
        page.wait_for_timeout(2500)
        assert api('behavior/summary?session_id=' + sid)['active_duration_ms'] == paused
        other.close(); page.bring_to_front()
        page.get_by_role('button', name='← 返回推荐列表').click()
        page.wait_for_timeout(1000)
        # Offline completion survives in IndexedDB and is retried on reconnection.
        page.get_by_role('button', name='展开详情 ↗').first.click()
        expect(page.locator('#property-detail')).to_have_attribute('data-reading-state','active', timeout=8000)
        page.wait_for_timeout(2000);context.set_offline(True)
        page.get_by_role('button', name='← 返回推荐列表').click();page.wait_for_timeout(600)
        context.set_offline(False);page.wait_for_timeout(2200)
        assert state()['latest_request_id'] == baseline['latest_request_id']
        assert page.locator('.listing-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)') == ids
        report['observations'] = api('behavior/summary?session_id=' + sid)
        assert report['observations']['active_duration_ms'] >= 60000
        report['checks'].append('Two real timed detail views, foreground lease, background pause, offline completion, unchanged list and no unsolicited query')
        print('User explicitly requests another recommendation; applying confirmed real feedback.', flush=True)
        page.locator('#query').fill('请按当前需求重新推荐，结合刚才浏览的房源')
        page.locator('#send').click()
        personalized = wait_result(baseline['latest_request_id'])
        assert personalized['requirements_version'] == baseline['requirements_version']
        report['personalized'] = oracle(personalized, baseline)
        report['checks'].append('User request preserves requirements and matches independent full-SQL behavioral ranking')
        expect(page.locator('.listing-card')).to_have_count(len(personalized['recommendation']['recommendations'])+len(personalized['recommendation']['unverified_candidates']),timeout=15000)
        page.wait_for_timeout(1000)
        print('User changes budget; checking hard constraints still dominate implicit interest.', flush=True)
        page.locator('#query').fill('预算改为4500新币，其余条件不变')
        page.locator('#send').click()
        updated = wait_result(personalized['latest_request_id'])
        assert updated['requirements_version'] == personalized['requirements_version'] + 1
        assert all(c['listing']['price'] <= 4500 for c in updated['recommendation']['recommendations'])
        assert all(updated['recommendation']['native']['scores'][c['listing']['listing_id']]['implicit_score'] is not None for c in updated['recommendation']['recommendations'])
        report['checks'].append('Budget correction reuses persistent interest and never violates the new hard cap')
        mobile = browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        mobile.add_init_script('const f=' + json.dumps(fixture) + ';localStorage.setItem("rental.owner",f.owner);localStorage.setItem("rental.session",f.session_id);')
        small = mobile.new_page();small.on('pageerror', lambda e: errors.append(str(e)));small.goto(BASE);small.wait_for_selector('body[data-ready=true]')
        small.get_by_role('button', name='展开详情 ↗').first.click();expect(small.locator('#property-detail [data-loaded=true]')).to_be_visible(timeout=15000)
        assert small.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
        small.screenshot(path=str(ROOT / 'evaluation/reports/browser-interest-mobile.png'), full_page=True)
        small.get_by_role('button', name='← 返回推荐列表').click()
        report['checks'].append('390px mobile details and return navigation have no horizontal overflow')
        assert not errors, errors
        browser.close()
    report['status'] = 'passed'
except Exception as e:
    report['status'] = 'failed';report['error'] = str(e)
    raise
finally:
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (ROOT / 'evaluation/reports/browser-interest-live.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    client.close()
