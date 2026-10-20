import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {writeFile,mkdir,readFile} from 'node:fs/promises';
import pg from 'pg';
import {extractRules} from '../src/domain/requirements.js';

test('nearby ranks without a hidden radius; actual MRT follow-up replaces the area, converts 3km and preserves transit/30min',{skip:process.env.BUSINESS_LIVE!=='1',timeout:180000},async()=>{
  const origin=process.env.TEST_APP_URL||'http://127.0.0.1:8010',headers={authorization:'Bearer '+randomUUID()+randomUUID(),'content-type':'application/json'};
  const pool=new pg.Pool({connectionString:process.env.DATABASE_URL}),oracle=new pg.Pool({connectionString:'postgresql://propertyguru_scraper@127.0.0.1:5432/propertyguru_sample'});
  const create=async()=>(await (await fetch(origin+'/api/sessions',{method:'POST',headers,body:'{}'})).json()).session_id;
  const run=async(sid:string,query:string)=>{
    const id=randomUUID(),response=await fetch(origin+'/api/query',{method:'POST',headers,body:JSON.stringify({query,session_id:sid,request_id:id})});assert.equal(response.status,200);await response.text();
    const deadline=Date.now()+65000;while(Date.now()<deadline){const op=(await pool.query('SELECT id,status FROM domain_operations WHERE session_id=$1 AND request_id=$2',[sid,id])).rows[0];if(op&&['completed','failed'].includes(op.status)){const s=await (await fetch(origin+'/api/state?session_id='+sid,{headers})).json();assert.equal(op.status,'completed',s.messages.at(-1)?.text);return s;}await new Promise(r=>setTimeout(r,200));}throw Error('Location regression timed out');
  };
  const catalog=JSON.parse(await readFile('knowledge/locations/lta-stations.json','utf8')),station=catalog.stations.find((s:any)=>s.name==='CLEMENTI MRT STATION');
  const rad=(n:number)=>n*Math.PI/180;
  const distance=(p:any)=>p.latitude===null||p.longitude===null?null:Math.min(...station.reference_points.map((exit:any)=>6371008.8*Math.acos(Math.max(-1,Math.min(1,Math.sin(rad(p.latitude))*Math.sin(rad(exit.latitude))+Math.cos(rad(p.latitude))*Math.cos(rad(exit.latitude))*Math.cos(rad(p.longitude-exit.longitude)))))));
  const checks:string[]=[];
  try{
    const sid=await create();let s=await run(sid,'我要找金文泰附近，预算2500每月的房子，要求有独立卫浴，离新国立大学通勤30分钟以内');
    assert.equal(s.pending,null);assert(s.latest_request_id);assert(!s.messages.at(-1).text.includes('还需确认'));
    const area=s.requirements.criteria.find((c:any)=>c.field==='place_proximity');assert.equal(area.target.place_id,'URA:AREA:CLEMENTI');assert.equal(area.strength,'soft');assert(!s.requirements.criteria.some((c:any)=>c.field==='place_proximity'&&c.strength==='hard'));
    const budgetCount=Number((await oracle.query("SELECT count(*) FROM properties WHERE listing_type='RENT' AND currency='SGD' AND price<=2500")).rows[0].count);assert.equal(s.recommendation.state.counts.unverified,budgetCount);assert.equal(s.recommendation.recommendations.length,0);
    checks.push('Original sentence resolves to the real Clementi area and ranks by proximity without requiring landmarks/radius or losing unknown hard conditions');
    s=await run(sid,'通勤指定方式为公交\n就是Clementi MRT附近3公里');assert.equal(s.pending,null);
    const commute=s.requirements.criteria.find((c:any)=>c.field==='commute_time');assert.equal(commute.value,30);assert.equal(commute.transport_mode,'transit');assert.equal(s.requirements.criteria.find((c:any)=>c.field==='price').value,2500);assert(s.requirements.criteria.some((c:any)=>c.field==='amenities'&&c.value==='private_bathroom'));
    const geo=s.requirements.criteria.filter((c:any)=>c.field==='place_proximity');assert(geo.every((c:any)=>c.target.place_id==='LTA:MRT:CLEMENTI'));assert(geo.some((c:any)=>c.strength==='hard'&&c.value===3000));
    const rows=(await oracle.query("SELECT listing_id,latitude,longitude,price FROM properties WHERE listing_type='RENT' AND currency='SGD' AND price<=2500")).rows;
    const expected=rows.filter(p=>distance(p)===null||distance(p)!<=3000).length;assert.equal(s.recommendation.state.counts.unverified,expected);assert.equal(s.recommendation.recommendations.length,0);
    for(const c of s.recommendation.unverified_candidates){const cr=c.criterion_results.find((r:any)=>r.criterion_id===geo.find((g:any)=>g.strength==='hard').criterion_id);assert(cr.status==='unknown'||cr.observed_value<=3000);}
    checks.push('Exact follow-up resolves MRT, converts 3km to 3000m, clears the old area target and preserves budget/private-bathroom/transit/30min');
    const nearSid=await create(),near=await run(nearSid,'预算2500新币，Clementi MRT附近');assert.equal(near.recommendation.state.counts.eligible,budgetCount);
    const sorted=rows.filter(p=>distance(p)!==null).map(p=>({...p,d:distance(p)!})).sort((a,b)=>a.d-b.d||Number(a.price)-Number(b.price)||(BigInt(a.listing_id)>BigInt(b.listing_id)?1:-1));assert.deepEqual(near.recommendation.recommendations.map((c:any)=>c.listing.listing_id),sorted.slice(0,5).map(p=>String(p.listing_id)));
    for(const c of near.recommendation.recommendations){const actual=c.criterion_results.find((r:any)=>r.criterion_id.startsWith('place_proximity')).observed_value;assert(Math.abs(actual-sorted.find(p=>String(p.listing_id)===c.listing.listing_id)!.d)<.01);}
    checks.push('Independent SQL plus official exit coordinates reproduce exact top-5 nearest ranking; eligible count is unchanged by an unspecified nearby preference');
    const legacySid=await create(),legacy={...extractRules('我要找金文泰附近，预算2500每月的房子，要求有独立卫浴，离新国立大学通勤30分钟以内',{listing_type:'RENT',criteria:[]}),location_intents:[],clarifications:['请明确“金文泰附近”的参照地点、范围（米或公里）。','请明确“就是Clementi MRT附近”的参照地点、范围（米或公里）。']};
    await pool.query('UPDATE session_domain_state SET pending=$2 WHERE session_id=$1',[legacySid,JSON.stringify(legacy)]);
    const resumed=await run(legacySid,'通勤指定方式为公交\n就是Clementi MRT附近3公里');assert.equal(resumed.pending,null);assert.equal(resumed.requirements.criteria.find((c:any)=>c.field==='commute_time').transport_mode,'transit');assert(resumed.requirements.criteria.some((c:any)=>c.field==='place_proximity'&&c.strength==='hard'&&c.value===3000));
    checks.push('A legacy conversation carrying both obsolete location questions resumes without asking them again');
    await mkdir('evaluation/reports',{recursive:true});await writeFile('evaluation/reports/intake-regression.json',JSON.stringify({checked_at:new Date().toISOString(),status:'passed',session_id:sid,nearby_only_session_id:nearSid,checks,unrestricted_budget_candidates:budgetCount,within_3km_or_unknown:expected},null,2));
  }finally{await pool.end();await oracle.end();}
});
