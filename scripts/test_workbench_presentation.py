"""Acceptance of the compact cards, measured distances and full detail workspace.

Uses an isolated, real NTU recommendation stored in data/.runtime/workbench-ui-session.json.
No message is sent to an external platform.
"""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright, expect, TimeoutError

ROOT=Path(__file__).resolve().parents[1]
fixture=json.loads((ROOT/'data/.runtime/workbench-ui-session.json').read_text(encoding='utf-8'))
report={'checks':[], 'external_messages_sent':0}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='chrome')
    def context(**options):
        c=browser.new_context(**options)
        c.add_init_script('const f='+json.dumps(fixture)+';localStorage.setItem("rental.owner",f.owner);localStorage.setItem("rental.session",f.session_id);')
        return c
    c=context(viewport={'width':1600,'height':1000});page=c.new_page();errors=[];queries=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:queries.append(r.url) if r.method=='POST' and r.url.endswith('/api/query') else None)
    page.goto('http://127.0.0.1:8010/')
    try:page.wait_for_load_state('networkidle',timeout=2000)
    except TimeoutError:pass  # The server's event stream intentionally remains open.
    page.wait_for_selector('body[data-ready=true]')
    def api(path):
        return page.evaluate('async path=>{const r=await fetch("/api/"+path,{headers:{Authorization:"Bearer "+localStorage.getItem("rental.owner")}});if(!r.ok)throw Error(r.status);return r.json();}',path)
    initial=api('state?session_id='+fixture['session_id']);rec=initial['recommendation']
    candidates=rec['recommendations']+rec['unverified_candidates']
    cards=page.locator('.listing-card');expect(cards).to_have_count(len(candidates))
    ids=cards.evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)')
    rail=page.locator('#listing-navigator');expect(rail.locator('button')).to_have_count(len(candidates))
    right=page.locator('.results')
    for text in ['浏览偏好会自动记录','已评估','快照条件已核验','历史记录','逐项查看条件与证据','本轮列表固定','原生融合匹配分']:
        assert text not in right.inner_text(),text
    assert page.locator('.notice,.interest-notice,.listing-collapse-toggle,.listing-card img,.listing-subtitle,.verified-badge').count()==0
    expect(page.get_by_role('button',name='我想看这套',exact=True)).to_have_count(0)
    for i,candidate in enumerate(candidates):
        assert cards.nth(i).get_by_role('button',name='联系经纪人',exact=True).count()==1
        assert cards.nth(i).locator('.pending-summary').count()<=1
        distances=[r['observed_value'] for r in candidate['criterion_results'] if r['criterion_id']==next(x['criterion_id'] for x in initial['requirements']['criteria'] if x['field']=='place_proximity' and x['strength']=='hard')]
        if distances and isinstance(distances[0],(int,float)):
            # JavaScript Math.round, not Python bankers' rounding.
            measured=f'{int(distances[0]+.5):,} 米'
            assert measured in cards.nth(i).inner_text(),(i,measured)
    amount=cards.first.locator('.rent-amount');period=cards.first.locator('.rent-period')
    assert amount.inner_text()=='$1,100'
    assert amount.evaluate('(n)=>getComputedStyle(n).fontSize')==period.evaluate('(n)=>getComputedStyle(n).fontSize')
    source=cards.first.locator('.listing-source-button')
    assert source.evaluate('(n)=>parseFloat(getComputedStyle(n).borderWidth)')>=1
    page.screenshot(path=str(ROOT/'evaluation/reports/workbench-list.png'))
    report['checks'].append('Every card is compact, has no thumbnail or evidence inspector, shows its actual measured distance and one pending line; rent and monthly unit have equal font size')
    rail.locator('button').nth(2).click();expect(rail.locator('button').nth(2)).to_have_attribute('aria-current','true')
    offset=page.locator('#recommendations').evaluate('(n)=>n.scrollTop')
    assert offset>100
    assert abs(cards.nth(2).bounding_box()['y']-page.locator('#recommendations').bounding_box()['y'])<3
    cards.nth(2).get_by_role('button',name='展开详情 ↗').click()
    page.wait_for_selector('#property-detail [data-loaded=true]')
    expect(page.locator('#property-detail')).to_have_attribute('data-reading-state','active',timeout=8000)
    page.get_by_role('button',name='← 返回推荐列表').click()
    assert abs(page.locator('#recommendations').evaluate('(n)=>n.scrollTop')-offset)<2
    expect(rail.locator('button').nth(2)).to_have_attribute('aria-current','true')
    rail.locator('button').last.click();expect(rail.locator('button').last).to_have_attribute('aria-current','true')
    page.wait_for_timeout(300);expect(rail.locator('button').last).to_have_attribute('aria-current','true')
    rail.locator('button').first.click();page.mouse.move(1200,650);page.mouse.wheel(0,800);page.wait_for_timeout(500)
    assert rail.locator('[aria-current=true]').inner_text()!='1'
    rail.locator('button').first.click()
    report['checks'].append('Numbered navigation jumps directly to the selected card, tracks manual scrolling, and restores position and selection after returning from detail')
    baseline=api('behavior/summary?session_id='+fixture['session_id'])['active_duration_ms']
    cards.first.get_by_role('button',name='展开详情 ↗').click();page.wait_for_selector('#property-detail [data-loaded=true]')
    expect(page.locator('#property-detail')).to_have_attribute('data-reading-state','active',timeout=8000)
    detail=page.locator('#property-detail');facts=detail.locator('.property-facts')
    expect(detail.get_by_role('heading',name='房源详细资料')).to_be_visible()
    assert facts.count()==1 and facts.locator('.property-fact').count()>4
    for forbidden in ['原始记录为0','未收录','更多房源资料','家具配置、独立卫浴使用权','OSM 周边记录','原生融合匹配分','正在记录有效浏览','本轮列表固定','查看证据','房源 60020439']:
        assert forbidden not in detail.inner_text(),forbidden
    assert facts.get_by_text('卧室数量',exact=True).count()==0
    assert facts.get_by_text('室内面积',exact=True).count()==0
    assert detail.locator('.property-address,.property-mrt,.property-extra,.property-facilities,#reading-status').count()==0
    assert '812 米' in detail.locator('.detail-match').inner_text()
    img=detail.locator('.property-photo').first
    page.wait_for_function('()=>document.querySelector("#property-detail .property-photo")?.naturalWidth>0',timeout=25000)
    assert img.evaluate('(n)=>getComputedStyle(n).objectFit')=='contain'
    assert img.evaluate('(n)=>n.clientHeight<=n.parentNode.clientHeight+1 && n.clientWidth<=n.parentNode.clientWidth+1')
    page.wait_for_timeout(1000)
    gallery=detail.locator('.property-gallery');gh=gallery.bounding_box()['height'];fh=facts.bounding_box()['height']
    assert abs(gh-fh)<55,(gh,fh)
    page.screenshot(path=str(ROOT/'evaluation/reports/workbench-detail.png'))
    page.get_by_role('button',name='下一张',exact=True).click()
    page.locator('.property-gallery>button').click();expect(page.locator('#details-dialog')).to_be_visible()
    assert page.locator('#details-dialog').get_attribute('data-reading-listing-id')==ids[0]
    page.locator('#details-dialog .close-dialog').click()
    page.locator('.property-detail-body').evaluate('(n)=>n.scrollTop=n.scrollHeight')
    page.screenshot(path=str(ROOT/'evaluation/reports/workbench-detail-bottom.png'))
    page.wait_for_timeout(2200);page.get_by_role('button',name='← 返回推荐列表').click();page.wait_for_timeout(1800)
    assert api('behavior/summary?session_id='+fixture['session_id'])['active_duration_ms']>baseline
    assert cards.evaluate_all('(nodes)=>nodes.map(n=>n.dataset.listingId)')==ids
    assert api('state?session_id='+fixture['session_id'])['latest_request_id']==initial['latest_request_id']
    assert not queries
    report['checks'].append('Detail unifies all known fields beside uncropped photos, hides missing bedroom/area values, balances panel heights, and keeps the gallery and silent real reading collection functional without reranking')
    mobile=context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
    small=mobile.new_page();small.on('pageerror',lambda e:errors.append(str(e)));small.goto('http://127.0.0.1:8010/');small.wait_for_selector('body[data-ready=true]')
    small.locator('#recommendation-browser').scroll_into_view_if_needed();small.screenshot(path=str(ROOT/'evaluation/reports/workbench-mobile-list.png'))
    assert small.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    small.locator('#listing-navigator button').nth(2).click();expect(small.locator('#listing-navigator button').nth(2)).to_have_attribute('aria-current','true')
    small.locator('.listing-card').nth(2).get_by_role('button',name='展开详情 ↗').click();small.wait_for_selector('#property-detail [data-loaded=true]')
    small.wait_for_function('()=>document.querySelector("#property-detail .property-photo")?.naturalWidth>0',timeout=25000)
    assert small.locator('#property-detail .property-photo').first.evaluate('(n)=>n.clientHeight<=n.parentNode.clientHeight+1')
    small.screenshot(path=str(ROOT/'evaluation/reports/workbench-mobile-detail.png'))
    assert small.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    small.get_by_role('button',name='← 返回推荐列表').click();mobile.close()
    report['checks'].append('390px mobile list navigation and detail layout have no horizontal overflow')
    assert not errors,errors
    report.update(status='passed',browser_errors=errors,listing_count=len(candidates),first_distance_m=811.64)
    browser.close()
(ROOT/'evaluation/reports/workbench-presentation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report))
