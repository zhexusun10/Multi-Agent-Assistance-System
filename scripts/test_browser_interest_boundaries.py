"""Short UI boundary checks, using real elapsed time and an existing isolated fixture."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
fixture=json.loads((ROOT/'data/.runtime/browser-interest-session.json').read_text(encoding='utf-8'))
report={'checks':[]}
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,channel='chrome')
    c=b.new_context(viewport={'width':1600,'height':1000})
    c.add_init_script('const f='+json.dumps(fixture)+';localStorage.setItem("rental.owner",f.owner);localStorage.setItem("rental.session",f.session_id);')
    page=c.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    def get(path):
        return page.evaluate('async path=>{const r=await fetch("/api/"+path,{headers:{Authorization:"Bearer "+localStorage.getItem("rental.owner")}});if(!r.ok)throw Error(r.status);return r.json();}',path)
    def summary():return get('behavior/summary?session_id='+fixture['session_id'])
    def load():page.goto('http://127.0.0.1:8010/');page.wait_for_selector('body[data-ready=true]')
    def open_detail():
        page.get_by_role('button',name='展开详情 ↗').first.click()
        page.wait_for_selector('#property-detail [data-loaded=true]')
        expect(page.locator('#property-detail')).to_have_attribute('data-reading-state','active',timeout=8000)
    load();before=get('state?session_id='+fixture['session_id']);ids=page.locator('.listing-card').evaluate_all('(ns)=>ns.map(n=>n.dataset.listingId)')
    open_detail();page.wait_for_timeout(2200)
    page.locator('#query').click();page.keyboard.type('仅测试输入，暂不发送');page.wait_for_timeout(1500)
    paused=summary()['active_duration_ms'];page.wait_for_timeout(2500);assert summary()['active_duration_ms']==paused
    assert page.locator('#property-detail').is_visible();page.locator('#query').fill('')
    report['checks'].append('Chat typing pauses interest measurement without closing the property or requesting new recommendations')
    page.locator('#help-button').click();page.wait_for_selector('#details-dialog[open]');page.wait_for_timeout(1500)
    paused=summary()['active_duration_ms'];page.wait_for_timeout(2000);assert summary()['active_duration_ms']==paused
    page.locator('#details-dialog .close-dialog').click();page.get_by_role('button',name='← 返回推荐列表').click()
    report['checks'].append('System help modal is not counted as property reading')
    open_detail();print('Checking 30-second inactivity boundary with real browser time.',flush=True);page.wait_for_timeout(34000)
    expect(page.locator('#property-detail')).to_have_attribute('data-reading-state','paused')
    paused=summary()['active_duration_ms'];page.wait_for_timeout(3000);assert summary()['active_duration_ms']==paused
    page.get_by_role('button',name='← 返回推荐列表').click();page.wait_for_timeout(500)
    report['checks'].append('Long idle reading is conservatively capped; further idle wall time is not counted')
    open_detail();page.wait_for_timeout(2200);c.set_offline(True);page.get_by_role('button',name='← 返回推荐列表').click();page.wait_for_timeout(800)
    # Same browser storage, a new document: queued completion survives the old page.
    page.close();c.set_offline(False);page=c.new_page();page.on('pageerror',lambda e:errors.append(str(e)));load();page.wait_for_timeout(2200)
    acknowledged=summary()['active_duration_ms'];page.reload();page.wait_for_selector('body[data-ready=true]');page.wait_for_timeout(1500);assert summary()['active_duration_ms']==acknowledged
    after=get('state?session_id='+fixture['session_id']);assert after['latest_request_id']==before['latest_request_id'];assert after['requirements_version']==before['requirements_version']
    assert page.locator('.listing-card').evaluate_all('(ns)=>ns.map(n=>n.dataset.listingId)')==ids
    assert not errors,errors
    report['checks'].append('IndexedDB retries a completion across document recreation without duplicate accumulation or automatic reranking')
    report['status']='passed';b.close()
(ROOT/'evaluation/reports/browser-interest-boundaries.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
