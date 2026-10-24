import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import pg from 'pg';

test('full snapshot: exhaustive SQL business oracle, proximity ranking, presentation and follow-up',{
  skip:process.env.FULL_DATA_LIVE!=='1',timeout:240000,
},async()=>{
  const owner=randomUUID()+randomUUID(),headers={authorization:'Bearer '+owner,'content-type':'application/json'};
  const base='http://127.0.0.1:8010/api/',pool=new pg.Pool({connectionString:process.env.DATABASE_URL});
  const sql=new pg.Pool({connectionString:process.env.PROPERTYGURU_DATABASE_URL!.replace('postgresql+psycopg2:','postgresql:')});
  const report:any={checked_at:new Date().toISOString(),checks:[]};
  const api=async(path:string,body?:unknown)=>{const r=await fetch(base+path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body)});assert(r.ok,await r.clone().text());return r.json();};
  const run=async(sid:string,query:string,source_request_id?:string)=>{
    const request_id=randomUUID(),start=Date.now();
    const r=await fetch(base+'query',{method:'POST',headers,body:JSON.stringify({query,session_id:sid,request_id,source_request_id})});assert(r.ok);await r.text();
    const deadline=Date.now()+100000;
    while(Date.now()<deadline){const op=(await pool.query('SELECT status FROM domain_operations WHERE session_id=$1 AND request_id=$2',[sid,request_id])).rows[0];
      if(op&&['completed','failed'].includes(op.status)){const s=await api('state?session_id='+sid);assert.equal(op.status,'completed',s.messages.at(-1)?.text);report.checks.push({query,elapsed_ms:Date.now()-start,counts:s.recommendation?.state.counts,ids:[...(s.recommendation?.recommendations||[]),...(s.recommendation?.unverified_candidates||[])].map((x:any)=>x.listing.listing_id)});return s;}
      await new Promise(r=>setTimeout(r,150));
    }throw Error('Full snapshot workflow timed out');
  };
  try{
    const config=await api('config');assert.equal(config.full_snapshot,true);assert.equal(config.dataset,'2026-10-09');assert.equal(config.dataset_counts.properties,21431);
    const properties=(await sql.query('SELECT listing_id,price,currency,listing_type,bedrooms,bathrooms,district_code,latitude,longitude,floor_area_sqft,property_type FROM properties ORDER BY listing_id')).rows;
    assert.equal(properties.length,21431);
    const raw=await (await fetch('http://127.0.0.1:8089/api/v1/places/search',{method:'POST',headers,body:JSON.stringify({filters:{q:'National University of Singapore'},page_size:50})})).json();
    const nus=raw.items.find((p:any)=>p.properties.name==='National University of Singapore').properties;
    const catalog=JSON.parse(await readFile('knowledge/locations/lta-stations.json','utf8'));
    const exits=catalog.stations.filter((s:any)=>s.name.endsWith(' MRT STATION')).flatMap((s:any)=>s.reference_points);
    const rad=(n:number)=>n*Math.PI/180;
    const distance=(a:any,b:any)=>6371008.8*Math.acos(Math.max(-1,Math.min(1,Math.sin(rad(a.latitude))*Math.sin(rad(b.latitude))+Math.cos(rad(a.latitude))*Math.cos(rad(b.latitude))*Math.cos(rad(a.longitude-b.longitude)))));
    const valid=(p:any)=>p.latitude!==null&&p.longitude!==null&&Number(p.latitude)>=1.15&&Number(p.latitude)<=1.5&&Number(p.longitude)>=103.55&&Number(p.longitude)<=104.2;
    const distances=new Map(properties.map(p=>[String(p.listing_id),valid(p)?{nus:distance(p,nus),mrt:Math.min(...exits.map((e:any)=>distance(p,e)))}:null]));
    const verify=(s:any)=>{
      assert.equal(s.pending,null,s.messages.at(-1)?.text);const rec=s.recommendation;assert(rec);assert.equal(rec.state.counts.assessed,21431);assert.equal(rec.state.coverage.complete_within_dataset,true);assert.equal(rec.state.coverage.truncated,false);
      const criteria=s.requirements.criteria;
      const compare=(value:any,c:any)=>c.operator==='eq'?value===c.value:c.operator==='lte'?value<=c.value:c.operator==='gte'?value>=c.value:c.operator==='between'?value>=c.value[0]&&value<=c.value[1]:c.operator==='in'?c.value.includes(value):(()=>{throw Error('Unexpected test operator');})();
      const expected=properties.map(p=>{
        const id=String(p.listing_id),d=distances.get(id);
        const results=criteria.map((c:any)=>{let v:any=null;
          if(c.field==='price')v=Number(p.price);else if(c.field==='district')v=p.district_code;
          else if(c.field==='bedrooms'||c.field==='bathrooms'||c.field==='floor_area_sqft')v=p[c.field]===null||Number(p[c.field])<0?null:Number(p[c.field]);
          else if(c.field==='place_proximity')v=d?(c.target.category==='MRTStation'?d.mrt:d.nus):null;
          else if(c.field!=='amenities')throw Error('Unexpected criterion '+c.field);
          return {c,v,status:v===null?'unknown':compare(v,c)?'pass':'fail'};
        });
        const hard=results.filter((r:any)=>r.c.strength==='hard'),group=hard.some((r:any)=>r.status==='fail')?'excluded':hard.some((r:any)=>r.status==='unknown')?'unverified':'eligible';
        const soft=results.filter((r:any)=>r.c.strength==='soft'),denom=soft.reduce((n:number,r:any)=>n+r.c.weight,0);
        const score=soft.reduce((n:number,r:any)=>n+(r.v===null?0:r.c.field==='place_proximity'?r.c.value/(r.c.value+r.v):r.status==='pass'?1:0)*r.c.weight/denom,0);
        return {id,p,results,group,score};
      });
      const order=(a:any,b:any)=>b.score-a.score||Number(a.p.price)-Number(b.p.price)||(BigInt(a.id)>BigInt(b.id)?1:-1);
      for(const [group,key,limit] of [['eligible','recommendations',10],['unverified','unverified_candidates',10]] as const){
        const e=expected.filter(e=>e.group===group).sort(order);assert.equal(rec.state.counts[group],e.length);
        assert.deepEqual(rec[key].map((c:any)=>c.listing.listing_id),e.slice(0,limit).map(x=>x.id));
        for(const c of rec[key])for(const r of c.criterion_results){const v=e.find(p=>p.id===c.listing.listing_id)!.results.find((x:any)=>x.c.criterion_id===r.criterion_id);assert.equal(r.status,v.status);if(typeof v.v==='number')assert(Math.abs(r.observed_value-v.v)<.02);else assert.equal(r.observed_value,v.v);}
      }
      assert.equal(rec.state.counts.excluded,expected.filter(p=>p.group==='excluded').length);
    };
    const one=await api('sessions',{});let s=await run(one.session_id,'帮我找租房，预算不超过5000新币，至少2房，D05');verify(s);
    const two=await api('sessions',{});s=await run(two.session_id,'我想租新加坡国立大学附近3公里内的房子，要求房子3公里内附近有MRT，预算3000新币每月，要求有独立浴室');verify(s);
    assert.equal(s.recommendation.recommendations.length,0);assert.equal(s.recommendation.unverified_candidates.length,10);
    const id=s.recommendation.unverified_candidates[0].listing.listing_id,source=s.latest_request_id;
    const presentation=await api(`listings/${id}/presentation?session_id=${two.session_id}&source_request_id=${source}`);
    assert.equal(presentation.dataset,'2026-10-09');assert.equal(presentation.warnings.length,0);assert(presentation.photos.length>0);assert(presentation.nearby.length>0);assert.equal(String(presentation.details.listing_id),id);
    s=await run(two.session_id,`请为房源ID ${id} 生成看房询问草稿，逐项询问尚未核实的条件。`,source);assert.equal(s.actions.length,1);assert.equal(s.actions[0].source_request_id,source);assert.deepEqual(s.actions[0].listing_ids,[id]);assert.match(s.actions[0].body,/独立卫浴/);
    s=await run(two.session_id,'生成当前推荐的总结文档',source);assert(s.artifacts.length>0);
    const three=await api('sessions',{});s=await run(three.session_id,'我想租新加坡国立大学附近3公里内的房子，预算3000新币每月');verify(s);assert.equal(s.recommendation.recommendations.length,10);
    report.status='passed';report.full_count=properties.length;report.mrt_exits=exits.length;report.presentation={listing_id:id,photos:presentation.photos.length,nearby:presentation.nearby.length};
    await writeFile('data/.runtime/full-data-ui-fixture.json',JSON.stringify({owner,session_id:two.session_id,plain_session_id:three.session_id}));
  }finally{
    await writeFile('evaluation/reports/full-data-live.json',JSON.stringify(report,null,2));await pool.end();await sql.end();
  }
});
