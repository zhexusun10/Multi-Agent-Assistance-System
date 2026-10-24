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
            card.get_by_role('button', name='设置条件', exact=True).click()
            card.get_by_label('数量要求').select_option('eq')
            card.get_by_label('卧室数量').fill('1')
            card.get_by_label('重要程度').select_option('hard')
        elif question['kind'] == 'ideal_rent':
            card.get_by_role('button', name='目前预算即可', exact=True).click()
        elif question['kind'] == 'closer':
            card.get_by_role('button', name='更近优先', exact=True).click()
        else:
            # Native exploration can select a different sample with different
            # useful questions; do not force a specific property type into it.
            card.get_by_role('button', name='暂未想好', exact=True).click()
        if index < len(questions)-1:
            card.get_by_role('button', name='下一项 →').click()
    expect(card.locator('.refinement-summary')).to_contain_text('保留原预算上限 2,500 新币')
    assert not posts
    assert page.locator('.listing-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)') == before_ids
    # A real refresh must restore the saved form without changing results.
    page.reload()
    settled()
    expect(card.get_by_role('button', name={'ideal_rent': '目前预算即可', 'closer': '更近优先'}.get(questions[-1]['kind'], '暂未想好'), exact=True)).to_have_attribute('aria-pressed', 'true')
    card.scroll_into_view_if_needed()
    page.screenshot(path=str(identity_path.parent/'before-submit.png'))
    card.get_by_role('button', name='补充好了，重新推荐', exact=True).click()
    expect(card.get_by_text('已记录您的选择', exact=True)).to_be_visible(timeout=150000)
    expect(page.locator('#agent-stage')).to_have_text('本轮已更新', timeout=150000)
    expect(page.locator('.listing-card')).to_have_count(5)
    assert page.locator('.listing-card').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)') != before_ids
    expect(card).to_have_count(1)
    assert not card.locator('input,select,button').count()
    expect(card.locator('.refinement-summary')).to_contain_text('保留原预算上限 2,500 新币')
    assert len(posts) == 1, posts
    assert not page.locator('#messages').get_by_text('本轮未完成', exact=False).count()
    page.screenshot(path=str(identity_path.parent/'after-submit.png'))
    # Historical submitted card survives another real state load, no new card.
    page.reload()
    settled()
    expect(card.get_by_text('已记录您的选择', exact=True)).to_be_visible()
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
