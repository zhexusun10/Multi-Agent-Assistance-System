"""Detail-view follow-up stays on the current recommendation, without sending."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright, expect

root=Path(__file__).resolve().parents[1]
f=json.loads((root/'data/.runtime/browser-interest-session.json').read_text(encoding='utf-8'))
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,channel='chrome');c=b.new_context(viewport={'width':1600,'height':1000})
    c.add_init_script('const f='+json.dumps(f)+';localStorage.setItem("rental.owner",f.owner);localStorage.setItem("rental.session",f.session_id);')
    page=c.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    def state():
        return page.evaluate('async()=>{const r=await fetch("/api/state?session_id="+localStorage.getItem("rental.session"),{headers:{Authorization:"Bearer "+localStorage.getItem("rental.owner")}});return r.json();}')
    page.goto('http://127.0.0.1:8010/');page.wait_for_selector('body[data-ready=true]');before=state()
    page.get_by_role('button',name='展开详情 ↗').first.click();page.wait_for_selector('#property-detail [data-loaded=true]')
    page.get_by_role('button',name='联系经纪人',exact=True).click()
    expect(page.locator('#actions')).to_be_visible(timeout=90000)
    page.locator('#actions').get_by_role('button',name='编辑草稿',exact=True).first.click()
    expect(page.locator('#action-dialog')).to_be_visible(timeout=20000)
    after=state()
    assert after['latest_request_id']==before['latest_request_id']
    assert after['requirements_version']==before['requirements_version']
    assert len(after['actions'])>len(before['actions'])
    assert after['actions'][-1]['action_type']=='VIEWING'
    expect(page.locator('#viewing-slots')).to_be_visible()
    expect(page.locator('#starts-at')).to_be_visible()
    assert not errors,errors
    report={'status':'passed','same_recommendation':True,'same_requirements':True,'new_followup_draft':True,'external_messages_sent':0,'browser_errors':errors}
    (root/'evaluation/reports/browser-interest-followup.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report));b.close()
