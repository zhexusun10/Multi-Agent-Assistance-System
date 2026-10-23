import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import pg from 'pg';

test('user screenshots search successfully, nearest MRT is correct, and the existing phantom-place conversation recovers',{skip:process.env.LOCATION_SUBJECT_LIVE!=='1',timeout:240000},async()=>{
  const origin='http://127.0.0.1:8010/api/',owner=randomUUID()+randomUUID(),headers={authorization:'Bearer '+owner,'content-type':'application/json'};
  const pool=new pg.Pool({connectionString:process.env.DATABASE_URL}),oracle=new pg.Pool({connectionString:'postgresql://propertyguru_scraper@127.0.0.1:5432/propertyguru_sample'});
  const before=JSON.parse(await readFile('evaluation/reports/location-subject-before.json','utf8'));
  const catalog=JSON.parse(await readFile('knowledge/locations/lta-stations.json','utf8'));
  const exits=catalog.stations.filter((s:any)=>s.name.endsWith(' MRT STATION')).flatMap((s:any)=>s.reference_points);
  const rad=(n:number)=>n*Math.PI/180;
  const distance=(a:any,b:any)=>{
    const lat1=rad(Number(a.latitude)),lat2=rad(Number(b.latitude)),delta=rad(Number(a.longitude)-Number(b.longitude));
    const cosine=Math.sin(lat1)*Math.sin(lat2)+Math.cos(lat1)*Math.cos(lat2)*Math.cos(delta);
    return 6371008.8*Math.acos(Math.max(-1,Math.min(1,cosine)));
  };
  const rawPlaces=await (await fetch('http://127.0.0.1:8089/api/v1/places/search',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({filters:{q:'National University of Singapore'},page_size:50,cursor:null})})).json();
  const nus=rawPlaces.items.find((x:any)=>x.properties.name==='National University of Singapore').properties;
  const properties=(await oracle.query("SELECT listing_id,price,currency,latitude,longitude FROM properties WHERE listing_type='RENT'")).rows;
  const coordinates=(p:any)=>p.latitude!==null&&p.longitude!==null&&Number(p.latitude)>=1.15&&Number(p.latitude)<=1.5&&Number(p.longitude)>=103.55&&Number(p.longitude)<=104.2;
  const mrtDistance=(p:any)=>Math.min(...exits.map((e:any)=>distance(p,e)));
  const expected=(mrt:boolean)=>properties.filter((p:any)=>(p.price===null||p.currency!=='SGD'||Number(p.price)<=3000)&&(!coordinates(p)||(distance(p,nus)<=3000&&(!mrt||mrtDistance(p)<=3000)))).length;
  const api=async(path:string,body?:unknown)=>{const r=await fetch(origin+path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body)});assert(r.ok,await r.clone().text());return r.json();};
  const rows:any[]=[];
  const run=async(sid:string,query:string)=>{
    const request_id=randomUUID(),response=await fetch(origin+'query',{method:'POST',headers,body:JSON.stringify({query,session_id:sid,request_id})});assert(response.ok);await response.text();
    const until=Date.now()+70000;
    while(Date.now()<until){const op=(await pool.query('SELECT status,intent_plan FROM domain_operations WHERE session_id=$1 AND request_id=$2',[sid,request_id])).rows[0];if(op&&['completed','failed'].includes(op.status)){const s=await api('state?session_id='+sid);assert.equal(op.status,'completed',s.messages.at(-1)?.text);rows.push({query,session_id:sid,requirements:s.requirements,pending:s.pending,counts:s.recommendation?.state.counts,message:s.messages.at(-1)?.text});return s;}await new Promise(r=>setTimeout(r,150));}throw Error('Workflow timeout');
  };
  const verify=(s:any,hasMrt:boolean)=>{
    assert.equal(s.pending,null,s.messages.at(-1)?.text);assert(s.latest_request_id);assert(s.recommendation);
    assert.match(s.messages.at(-1).text,/已找到待核实候选/);
    const criteria=s.requirements.criteria,hard=criteria.filter((c:any)=>c.strength==='hard');
    assert(hard.some((c:any)=>c.field==='price'&&c.value===3000));assert(hard.some((c:any)=>c.field==='amenities'&&c.value==='private_bathroom'));
    assert(hard.some((c:any)=>c.field==='place_proximity'&&c.target?.name==='National University of Singapore'&&c.value===3000));
    assert.equal(hard.some((c:any)=>c.target?.category==='MRTStation'&&c.value===3000),hasMrt);
    assert.equal(s.recommendation.state.counts.unverified,expected(hasMrt));assert.equal(s.recommendation.recommendations.length,0);
    assert.equal(s.recommendation.unverified_candidates.length,10);
    for(const c of s.recommendation.unverified_candidates){const p=properties.find((p:any)=>String(p.listing_id)===c.listing.listing_id);assert(Number(p.price)<=3000);assert(distance(p,nus)<=3000);if(hasMrt)assert(mrtDistance(p)<=3000);
      const bath=criteria.find((r:any)=>r.value==='private_bathroom');assert.equal(c.criterion_results.find((r:any)=>r.criterion_id===bath.criterion_id).status,'unknown');
      if(hasMrt){const mrt=hard.find((r:any)=>r.target?.category==='MRTStation'),result=c.criterion_results.find((r:any)=>r.criterion_id===mrt.criterion_id);assert.equal(result.status,'pass');assert(Math.abs(result.observed_value-mrtDistance(p))<.01);}
    }
  };
  try{
    const first=await api('sessions',{}),full=await run(first.session_id,before.query);verify(full,true);
    const second=await api('sessions',{}),simple=await run(second.session_id,before.simple_query);verify(simple,false);
    const legacy=await api('sessions',{}),draft=structuredClone(before.draft);
    draft.upsert.push(...simple.requirements.criteria.filter((c:any)=>c.field==='place_proximity'&&c.target?.place_id));
    await pool.query('UPDATE session_domain_state SET pending=$2 WHERE session_id=$1',[legacy.session_id,JSON.stringify(draft)]);
    const resumed=await run(legacy.session_id,before.simple_query);verify(resumed,true);
    const known=await api('sessions',{}),verified=await run(known.session_id,'我想租新加坡国立大学附近3公里内的房子，预算3000新币每月');
    assert.equal(verified.pending,null);assert.equal(verified.recommendation.state.counts.eligible,expected(false));assert.equal(verified.recommendation.recommendations.length,10);
    await writeFile('data/.runtime/location-subject-ui-fixture.json',JSON.stringify({owner,session_id:first.session_id,legacy_session_id:legacy.session_id,simple_query:before.simple_query,query:before.query}));
  }finally{await writeFile('evaluation/reports/location-subject-live.json',JSON.stringify({rows,oracle:{mrt_exits:exits.length,with_mrt:expected(true),without_mrt:expected(false)}},null,2));await pool.end();await oracle.end();}
});
