"""Inspect the exact screenshot query's completed real session, without resending it."""
from pathlib import Path
import json,re
from playwright.sync_api import sync_playwright,expect,TimeoutError

ROOT=Path(__file__).resolve().parents[1]
fixture=json.loads((ROOT/'data/.runtime/recommendation-empty-prefix-session.json').read_text(encoding='utf-8'))
report={'checks':[],'external_messages_sent':0,'model_requests_sent':0}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='chrome');errors=[]
    for name,options in [('desktop',{'viewport':{'width':1600,'height':1000}}),('mobile',{'viewport':{'width':390,'height':844},'is_mobile':True,'has_touch':True})]:
        context=browser.new_context(**options);context.add_init_script('const f='+json.dumps(fixture)+';localStorage.setItem("rental.owner",f.owner);localStorage.setItem("rental.session",f.session_id);')
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto('http://127.0.0.1:8010/')
        try:page.wait_for_load_state('networkidle',timeout=2000)
        except TimeoutError:pass
        page.wait_for_selector('body[data-ready=true]')
        expect(page.locator('#criteria')).to_contain_text('2500')
        expect(page.locator('#criteria')).to_contain_text('JURONG EAST')
        expect(page.locator('#criteria')).to_contain_text('卫浴')
        expect(page.locator('.listing-card')).to_have_count(4)
        for card in page.locator('.listing-card').all():
            pending=card.locator('.pending-summary').inner_text()
            assert '卫浴' in pending and '5,000 米' in pending and '1,000 米' in pending,pending
            assert card.get_by_role('button',name='联系经纪人').count()==1
        text=page.locator('#messages').inner_text()
        assert not re.search(r'Model turn|time budget|exhausted|本轮未完成',text)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        if name=='mobile':page.locator('.results').scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/f'evaluation/reports/recommendation-empty-prefix-{name}.png'))
        report['checks'].append(name+': four pending cards, 1B1B and all distance/budget conditions retained, no raw budget error or overflow');context.close()
    browser.close();assert not errors,errors;report.update(status='passed',browser_errors=errors)
(ROOT/'evaluation/reports/recommendation-empty-prefix-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report))
