"""Verify extracted explicit soft radii survive into the real workbench."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright,expect,TimeoutError
ROOT=Path(__file__).resolve().parents[1]
fixture=json.loads((ROOT/'data/.runtime/requirement-model-session.json').read_text(encoding='utf-8'))
report={'external_messages_sent':0,'checks':[]}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='chrome');errors=[]
    for name,options in [('desktop',{'viewport':{'width':1600,'height':1000}}),('mobile',{'viewport':{'width':390,'height':844},'is_mobile':True,'has_touch':True})]:
        c=browser.new_context(**options);c.add_init_script('const f='+json.dumps(fixture)+';localStorage.setItem("rental.owner",f.owner);localStorage.setItem("rental.session",f.session_id);')
        page=c.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto('http://127.0.0.1:8010/')
        try:page.wait_for_load_state('networkidle',timeout=2000)
        except TimeoutError:pass
        page.wait_for_selector('body[data-ready=true]')
        expect(page.locator('#criteria')).to_contain_text('最近的MRT或公交站')
        expect(page.locator('#criteria')).to_contain_text('1000 m')
        expect(page.locator('#criteria')).to_contain_text('700 m')
        expect(page.locator('#criteria')).to_contain_text('公园')
        assert not page.locator('.listing-card img,.listing-collapse-toggle,.notice,.interest-notice').count()
        if page.locator('.listing-card').count():
            assert page.locator('.listing-card').first.locator('.pending-summary').count()==1
            assert '1,000 米' in page.locator('.listing-card').first.locator('.pending-summary').inner_text()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        if name=='mobile':page.locator('.results').scroll_into_view_if_needed()
        page.screenshot(path=str(ROOT/f'evaluation/reports/requirement-model-{name}.png'))
        report['checks'].append(name+': explicit 1000m transport union and 700m park preference remain visible; clean layout and no horizontal overflow')
        c.close()
    assert not errors,errors;report.update(status='passed',browser_errors=errors);browser.close()
(ROOT/'evaluation/reports/requirement-model-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report))
