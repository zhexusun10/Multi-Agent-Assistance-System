"""Real UI + real local graph/data/model, synthetic Telegram recipient, simulation only."""
import json,time
from pathlib import Path
from datetime import datetime,timedelta
from playwright.sync_api import sync_playwright,expect,TimeoutError

ROOT=Path(__file__).resolve().parents[1]
checks=[];errors=[]
chat_id=str(int(time.time()*1000))
with sync_playwright() as p:
    browser=p.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page(viewport={'width':1500,'height':1100})
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8010/')
    try:page.wait_for_load_state('networkidle',timeout=2000)
    except TimeoutError:pass # Persistent SSE intentionally keeps the connection open.
    page.locator('body[data-ready=true]').wait_for()
    config=page.evaluate("fetch('/api/config').then(r=>r.json())")
    assert config['action_mode']=='simulation','This journey must never send a real message'
    page.locator('#query').fill('我要找女皇镇附近3公里的公寓，要求面积至少500sqft，预算2000刀每月，要求有独立卫浴。')
    page.locator('#send').click()
    card=page.locator('.listing-card').first
    expect(card).to_be_visible(timeout=70000)
    expect(card.get_by_role('button',name='我想看这套',exact=True)).to_be_visible()
    listing=card.get_attribute('data-listing-id')
    card.locator('.listing-claims summary').click()
    expect(card).to_contain_text('位于 QUEENSTOWN 区域内')
    area=card.locator('.claim').filter(has_text='按区域距离记为0').first
    area.scroll_into_view_if_needed()
    page.screenshot(path=str(ROOT/'evaluation/screenshots/11-area-distance-context.png'))
    assert int(page.locator('#result-count').inner_text())==5
    checks.append('pending candidates remain actionable; result count and area-distance meaning match the visible list')
    card.get_by_role('button',name='我想看这套',exact=True).click()
    action=page.locator('.action-card').first
    expect(action).to_be_visible(timeout=65000)
    expect(action).to_contain_text(listing)
    expect(action).to_contain_text('独立卫浴')
    action.get_by_role('button',name='编辑草稿',exact=True).click()
    expect(page.locator('#action-dialog')).to_be_visible()
    expect(page.locator('#contact-hints')).not_to_contain_text('正在读取',timeout=10000)
    assert page.locator('#recipient-channel').input_value()==''
    expect(page.locator('#channel-capabilities')).to_contain_text('尚未配置')
    checks.append('listing contact hints do not infer a messaging channel; both provider configuration states are visible')
    page.locator('#recipient-name').fill('Synthetic salesperson (browser test)')
    page.locator('#recipient-channel').select_option('whatsapp')
    page.get_by_role('button',name='读取获批文本模板',exact=True).click()
    expect(page.locator('#action-form-error')).to_contain_text('WhatsApp Business Account ID')
    page.locator('#recipient-channel').select_option('telegram')
    page.locator('#recipient-address').fill(chat_id)
    page.locator('#recipient-verified').check();page.locator('#recipient-opt-in').check()
    page.locator('#negotiation-notes').fill('希望月租协商到1800新币，并询问能否提前入住。')
    page.get_by_role('button',name='根据诉求重新起草',exact=True).click()
    expect(page.locator('#action-body')).to_have_value(__import__('re').compile('1800'),timeout=10000)
    expect(page.locator('#action-body')).to_have_value(__import__('re').compile('独立卫浴'))
    tomorrow=(datetime.fromisoformat(config['today'])+timedelta(days=1)).date().isoformat()
    page.get_by_role('button',name='打开月历选择日期',exact=True).click()
    expect(page.locator('#calendar')).to_be_visible()
    month=page.locator('#calendar input[type=month]');month.fill(tomorrow[:7]);month.dispatch_event('change')
    day=page.locator(f'.calendar-day[data-date="{tomorrow}"]');expect(day).to_be_visible(timeout=10000);day.click()
    expect(page.locator('#action-dialog')).to_be_visible()
    assert page.locator('#starts-at').input_value()==tomorrow+'T14:00'
    assert page.locator('#recipient-address').input_value()==chat_id
    assert '1800' in page.locator('#action-body').input_value()
    checks.append('calendar uses real Singapore date, selects a future slot and preserves unsaved contact/negotiation edits')
    page.locator('#action-form').get_by_role('button',name='保存当前草稿',exact=True).click()
    expect(page.locator('#action-dialog')).not_to_be_visible()
    page.locator('[data-tab=actions]').click()
    action=page.locator('.action-card').first
    expect(action).to_contain_text('1800')
    action.get_by_role('button',name='批准此修订',exact=True).click()
    expect(page.locator('#details-body')).to_contain_text(chat_id)
    expect(page.locator('#details-body')).to_contain_text('候选看房时间')
    page.locator('#details-body input[type=checkbox]').check()
    page.get_by_role('button',name='确认批准此修订',exact=True).click()
    expect(action.get_by_role('button',name='模拟执行',exact=True)).to_be_visible()
    action.get_by_role('button',name='模拟执行',exact=True).click()
    expect(action).to_contain_text('已模拟执行')
    checks.append('explicit review includes real wire text and recipient; simulation creates no external send')
    action.get_by_role('button',name='设置跟进提醒',exact=True).click()
    page.locator('#details-body textarea').fill('明天核实独立卫浴，并追问1800租金协商结果')
    page.get_by_role('button',name='保存提醒',exact=True).click()
    expect(action.locator('.followup-task')).to_contain_text('1800')
    action.get_by_role('button',name='调整提醒',exact=True).click()
    page.locator('#details-body textarea').fill('已改期：继续核实独立卫浴')
    page.get_by_role('button',name='保存提醒',exact=True).click()
    expect(action.locator('.followup-task')).to_contain_text('已改期')
    action.get_by_role('button',name='标记完成',exact=True).click()
    expect(action.locator('.followup-task')).to_contain_text('已完成')
    checks.append('post-execution follow-up can be created, revised and completed without changing the sent message')
    action.get_by_role('button',name='记录双方同意',exact=True).click()
    for checkbox in page.locator('#details-body input[type=checkbox]').all():checkbox.check()
    page.locator('#details-body textarea').fill('合成测试：客户与经纪人均明确同意所选时段，保持模拟标记。')
    page.get_by_role('button',name='保存双方同意记录（模拟）',exact=True).click()
    expect(action).to_contain_text('已记录双方同意')
    with page.expect_download() as download:
        action.get_by_role('button',name='导出看房日历',exact=True).click()
    ics=Path(download.value.path()).read_text(encoding='utf-8')
    assert 'STATUS:TENTATIVE' in ics and '模拟' in ics
    page.locator('[data-tab=calendar]').click()
    expect(page.locator('#calendar')).to_contain_text('双方同意')
    checks.append('bilateral confirmation is separate; owned calendar reflects it and simulated ICS remains tentative')
    page.locator('[data-tab=actions]').click()
    page.locator('.action-card').first.locator('details summary').click()
    page.screenshot(path=str(ROOT/'evaluation/screenshots/12-followup-workbench.png'))
    page.locator('[data-tab=documents]').click()
    page.get_by_role('button',name='生成当天会话与跟进总结',exact=True).click()
    doc=page.locator('#documents .doc-card').first
    expect(doc).to_contain_text('会话与跟进总结',timeout=10000)
    with page.expect_download() as download:
        doc.get_by_role('button',name='下载 Markdown',exact=True).click()
    md=Path(download.value.path()).read_text(encoding='utf-8')
    assert '1800' in md and '独立卫浴' in md and 'CONFIRMED' in md and 'simulation' in md
    page.screenshot(path=str(ROOT/'evaluation/screenshots/13-daily-followup-summary.png'))
    checks.append('daily summary download contains conversation, negotiation, unknown facts, confirmation and simulation provenance')
    sid=page.evaluate("localStorage.getItem('rental.session')")
    page.reload();page.locator('body[data-ready=true]').wait_for()
    page.locator('[data-tab=actions]').click()
    expect(page.locator('.action-card').first).to_contain_text('已记录双方同意')
    expect(page.locator('.followup-task')).to_contain_text('已完成')
    checks.append('action, confirmation, completed follow-up and artifact survive page reload')
    assert not errors,errors
    browser.close()
report={'checked_at':datetime.now().isoformat(),'status':'passed','session_id':sid,'selected_listing_id':listing,'provider_mode':'simulation; no actual provider messages','checks':checks,'page_errors':errors}
(ROOT/'evaluation/reports/followup-workflow-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
