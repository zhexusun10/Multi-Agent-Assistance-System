"""Observed UI journey: pending Queenstown listing -> reviewed viewing inquiry."""
import json,time
from pathlib import Path
from datetime import datetime,timezone,timedelta
from playwright.sync_api import sync_playwright,expect,TimeoutError
root=Path(__file__).resolve().parents[1]
errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1100})
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8010/')
    try:page.wait_for_load_state('networkidle',timeout=2000)
    except TimeoutError:pass  # Persistent SSE is expected.
    page.locator('body[data-ready="true"]').wait_for()
    page.locator('#query').fill('我要找女皇镇附近3公里的公寓，要求面积至少500sqft，预算2000刀每月，要求有独立卫浴。')
    page.locator('#send').click()
    card=page.locator('.listing-card').first
    expect(card).to_be_visible(timeout=65000)
    expect(card.locator('.verified-badge.unknown')).to_be_visible()
    expect(card.get_by_role('button',name='核实并咨询',exact=True)).to_be_visible()
    expect(card.get_by_role('button',name='我想看这套',exact=True)).to_be_visible()
    listing_id=card.get_attribute('data-listing-id')
    card.locator('.listing-claims summary').click()
    expect(card).to_contain_text('位于 QUEENSTOWN 区域内')
    expect(card).to_contain_text('不是距地铁站0米')
    page.screenshot(path=str(root/'evaluation/screenshots/09-queenstown-area-distance.png'),full_page=True)
    card.get_by_role('button',name='我想看这套',exact=True).click()
    expect(page.locator('[data-tab="actions"]')).to_have_attribute('aria-selected','true',timeout=65000)
    action=page.locator('.action-card').first
    expect(action).to_be_visible()
    expect(action).to_contain_text(listing_id)
    expect(action).to_contain_text('独立卫浴')
    expect(action).to_contain_text('尚无证据证明满足')
    expect(action).to_contain_text('待核实候选')
    action.get_by_role('button',name='设置24小时后跟进提醒',exact=True).click()
    expect(action).to_contain_text('跟进提醒',timeout=10000)
    expect(action).to_contain_text('修订 2')
    action.get_by_role('button',name='编辑草稿',exact=True).click()
    dialog=page.locator('#action-dialog')
    expect(dialog).to_be_visible()
    page.locator('#recipient-name').fill('Browser test salesperson')
    page.locator('#recipient-email').fill(f'queenstown-ui-{time.time_ns()}@example.test')
    page.locator('#recipient-verified').check()
    sg=timezone(timedelta(hours=8))
    start=datetime.now(sg)+timedelta(days=1,minutes=20)
    page.locator('#starts-at').fill(start.strftime('%Y-%m-%dT%H:%M'))
    page.locator('#ends-at').fill((start+timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M'))
    dialog.get_by_role('button',name='保存当前草稿',exact=True).click()
    expect(dialog).not_to_be_visible(timeout=10000)
    expect(action).to_contain_text('修订 3',timeout=10000)
    action.get_by_role('button',name='批准此修订',exact=True).click()
    expect(action.get_by_role('button',name='模拟执行',exact=True)).to_be_visible(timeout=10000)
    action.get_by_role('button',name='模拟执行',exact=True).click()
    expect(action).to_contain_text('已模拟执行',timeout=10000)
    expect(action).to_contain_text('模拟记录：simulated:')
    assert page.locator('.verified-badge:not(.unknown)').count()==0
    page.screenshot(path=str(root/'evaluation/screenshots/10-unverified-viewing-workbench.png'),full_page=True)
    page.reload()
    page.locator('body[data-ready="true"]').wait_for()
    page.locator('[data-tab="actions"]').click()
    expect(page.locator('.action-card').first).to_contain_text('已模拟执行',timeout=10000)
    assert not errors,errors
    browser.close()
report={'checked_at':datetime.now(timezone.utc).isoformat(),'status':'passed','listing_id':listing_id,'checks':['Original Queenstown request renders pending candidates with both inquiry and viewing controls','Area zero is explicitly labeled containment, not station distance','Click binds a stable listing ID and automatically opens the follow-up workbench','Unknown hard conditions are carried into the inquiry without becoming passed','24-hour reminder can be set, then contact/slots edited and current revision approved','Execution is visibly simulation and survives reload; listing remains pending'],'page_errors':errors,'screenshots':['09-queenstown-area-distance.png','10-unverified-viewing-workbench.png']}
(root/'evaluation/reports/queenstown-followup-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print('Queenstown pending-listing workflow passed: 6 browser checks.')
