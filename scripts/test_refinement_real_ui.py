"""Real HTTP browser acceptance. No API routes are mocked or intercepted."""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout

identity_path = Path(sys.argv[1])
identity = json.loads(identity_path.read_text(encoding='utf-8'))
report = {'status': 'running', 'checks': [], 'api_interception': False}
errors, posts, failed_responses = [], [], []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    context = browser.new_context(viewport={'width': 1600, 'height': 1000})
    context.add_init_script('localStorage.setItem("rental.owner",'+json.dumps(identity['owner'])+');localStorage.setItem("rental.session",'+json.dumps(identity['session'])+');')
    page = context.new_page()
    def settled():
        # Live SSE stays open by design. Bound networkidle, then require the
        # actual application readiness marker before inspecting the DOM.
        try:
            page.wait_for_load_state('networkidle', timeout=3000)
        except PlaywrightTimeout:
            pass
        page.wait_for_selector('body[data-ready=true]')
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('request', lambda r: posts.append(r.post_data_json) if '/api/refinements/' in r.url and r.method == 'POST' else None)
    page.on('response', lambda r: failed_responses.append([r.status, r.url]) if '/api/' in r.url and r.status >= 400 else None)
    page.goto(identity['url'])
    settled()
    card = page.locator('.requirement-refinement')
    expect(card).to_have_count(1)
    expect(page.locator('.listing-card')).to_have_count(5)
    before_ids = page.locator('.listing-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)')
    questions = identity['plan']['questions']
    for index, question in enumerate(questions):
        expect(card.get_by_text(f'{index+1} / {len(questions)}', exact=True)).to_be_visible()
        if question['kind'] == 'bedrooms':
            card.get_by_role('button', name='Set a requirement', exact=True).click()
            card.get_by_label('Number requirement').select_option('eq')
            card.get_by_label('Number of bedrooms').fill('1')
            card.get_by_label('Importance').select_option('hard')
        elif question['kind'] == 'ideal_rent':
            card.get_by_role('button', name='Keep my current budget', exact=True).click()
        elif question['kind'] == 'closer':
            card.get_by_role('button', name='Prefer closer', exact=True).click()
        else:
            # Native exploration can select a different sample with different
            # useful questions; do not force a specific property type into it.
            card.get_by_role('button', name='Not sure yet', exact=True).click()
        if index < len(questions)-1:
            card.get_by_role('button', name='Next →').click()
    expect(card.locator('.refinement-summary')).to_contain_text('Keep the existing budget limit of 2,500 SGD')
    assert not posts
    assert page.locator('.listing-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)') == before_ids
    # A real refresh must restore the saved form without changing results.
    page.reload()
    settled()
    expect(card.get_by_role('button', name={'ideal_rent': 'Keep my current budget', 'closer': 'Prefer closer'}.get(questions[-1]['kind'], 'Not sure yet'), exact=True)).to_have_attribute('aria-pressed', 'true')
    card.scroll_into_view_if_needed()
    page.screenshot(path=str(identity_path.parent/'before-submit.png'))
    card.get_by_role('button', name='Apply choices and recommend again', exact=True).click()
    expect(card.get_by_text('Your choices have been saved', exact=True)).to_be_visible(timeout=150000)
    expect(page.locator('#agent-stage')).to_have_text('Up to date', timeout=150000)
    expect(page.locator('.listing-card')).to_have_count(5)
    assert page.locator('.listing-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)') != before_ids
    expect(card).to_have_count(1)
    assert not card.locator('input,select,button').count()
    expect(card.locator('.refinement-summary')).to_contain_text('Keep the existing budget limit of 2,500 SGD')
    assert len(posts) == 1, posts
    assert not page.locator('#messages').get_by_text('this round’s 未完成', exact=False).count()
    page.screenshot(path=str(identity_path.parent/'after-submit.png'))
    # Historical submitted card survives another real state load, no new card.
    page.reload()
    settled()
    expect(card.get_by_text('Your choices have been saved', exact=True)).to_be_visible()
    expect(card).to_have_count(1)
    expect(page.locator('.listing-card')).to_have_count(5)
    assert not errors, errors
    assert not failed_responses, failed_responses
    report.update(status='passed', submit_posts=len(posts), page_errors=errors, failed_api_responses=failed_responses,
                  checks=['real state and SSE without API interception', 'Top5 before and after', 'no rerank while editing', 'refresh restores form', 'one structured POST', 'new results and explanation arrive without second query', 'budget retained', 'submitted history restored; no new card'])
    context.close()
    browser.close()
(identity_path.parent/'browser-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
