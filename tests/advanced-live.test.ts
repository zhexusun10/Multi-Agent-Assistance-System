import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import pg from 'pg';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {selectV1Violations,scoreOrder,oracleSettings} from './helpers/select-v1-oracle.js';

test('compound plans, independent Neo4j spatial oracle, rapid edits and request replay',{skip:process.env.BUSINESS_LIVE!=='1',timeout:180000},async()=>{
  const origin=process.env.TEST_APP_URL||'http://127.0.0.1:8010',owner=randomUUID()+randomUUID();
  const headers={authorization:'Bearer '+owner,'content-type':'application/json'};
  const pool=new pg.Pool({connectionString:process.env.DATABASE_URL});
  const oracle=new pg.Pool({connectionString:'postgresql://propertyguru_scraper@127.0.0.1:5432/propertyguru_sample'});
  const sid=(await (await fetch(origin+'/api/sessions',{method:'POST',headers,body:'{}'})).json()).session_id;
  const state=async()=>await (await fetch(origin+'/api/state?session_id='+sid,{headers})).json();
  const submit=async(query:string,request_id=randomUUID())=>{const response=await fetch(origin+'/api/query',{method:'POST',headers,body:JSON.stringify({query,request_id,session_id:sid})});assert.equal(response.status,200);await response.text();return request_id;};
  const wait=async(id:string)=>{const deadline=Date.now()+65000;while(Date.now()<deadline){const row=(await pool.query('SELECT status FROM domain_operations WHERE session_id=$1 AND request_id=$2',[sid,id])).rows[0];if(row&&['failed','completed','superseded'].includes(row.status)){const s=await state();assert.equal(row.status,'completed',s.messages.at(-1)?.text);return s;}await new Promise(r=>setTimeout(r,150));}throw Error('Operation timeout');};
  const checks:{name:string;passed:boolean;detail?:unknown}[]=[];
  try{
    let s=await wait(await submit('先帮我找租房，预算不超过5000新币，至少2房，D05，并生成总结文档'));
    assert(s.recommendation.recommendations.length>0);assert.equal(s.artifacts.length,1);assert.equal(s.artifacts[0].source_request_id,s.latest_request_id);
    const stages=(await pool.query("SELECT t.agent FROM agent_tasks t JOIN domain_operations o ON t.metadata->>'operation_id'=o.id::text WHERE o.session_id=$1 ORDER BY t.created_at",[sid])).rows.map(r=>r.agent);
    assert.deepEqual(stages,['a','b','c','d']);checks.push({name:'Compound search -> verified recommendation -> explanation -> summary runs A/B/C/D in dependency order',passed:true});

    s=await wait(await submit('最好超市直线距离800米以内'));
    const proximity=s.requirements.criteria.find((c:any)=>c.field==='place_proximity');assert.equal(proximity.strength,'soft');assert.equal(proximity.target.category,'Supermarket');assert.equal(s.recommendation.state.counts.eligible,65);
    const neo=await (await fetch('http://127.0.0.1:7475/db/neo4j/tx/commit',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({statements:[{statement:"MATCH (p:Place) WHERE p.category='Supermarket' RETURN p.latitude,p.longitude,coalesce(p.active,false)"}]})})).json();
    assert.equal(neo.errors.length,0);const native=s.recommendation.native;
    // Native resolves categories over ACTIVE OSM places only; the baseline adapter's oracle keeps every place (unchanged).
    const places=neo.results[0].data.map((x:any)=>x.row).filter((r:any[])=>!native||r[2]===true).map((r:any[])=>[r[0],r[1]]);assert(places.length>0);
    const properties=(await oracle.query("SELECT listing_id,latitude,longitude,price FROM properties WHERE listing_type='RENT' AND currency='SGD' AND price<=5000 AND bedrooms>=2 AND district_code='D05'")).rows;
    // Independent spherical cosine formula; production uses haversine, never this helper.
    const rad=(n:number)=>n*Math.PI/180;
    const expected=properties.map(p=>{const distance=p.latitude==null||p.longitude==null?null:Math.min(...places.map(([lat,lon]:number[])=>6371008.8*Math.acos(Math.max(-1,Math.min(1,Math.sin(rad(p.latitude))*Math.sin(rad(lat))+Math.cos(rad(p.latitude))*Math.cos(rad(lat))*Math.cos(rad(p.longitude-lon)))))));return {id:String(p.listing_id),distance,score:distance===null?0:800/(800+distance),price:Number(p.price)};}).sort((a,b)=>b.score-a.score||a.price-b.price||(BigInt(a.id)>BigInt(b.id)?1:-1));
    const shown=s.recommendation.recommendations.map((c:any)=>c.listing.listing_id);
    if(native){
      // Native contract 2.0 (select-v1): fused-v2 score = soft_weight x 800/(800+d) (one soft criterion, no feedback; no
      // coordinates -> unknown utility 0); O = score desc, then listing_id code-point order (no price tie-break). Leaders
      // must be O's prefix and the other items must come from the tie group / exploration window right after them.
      const exchange=await (await fetch(origin+`/api/evidence?session_id=${sid}&ref=${native.exchange_ref}`,{headers})).json(),response=exchange.data.response;
      const softWeight=oracleSettings(await readFile('recommendation/settings/recommendation.yaml','utf8')).soft_weight;
      const scores=new Map(expected.map(p=>[p.id,softWeight*p.score]));
      assert.deepEqual(selectV1Violations(response,scoreOrder(scores),10,scores),[],JSON.stringify(response.selection));
      for(const c of response.recommendations)assert(Math.abs(c.scores.combined_score-scores.get(c.listing.listing_id)!)<1e-6);
      assert.deepEqual(shown,native.ranking);
    }else assert.deepEqual(shown,expected.slice(0,10).map(p=>p.id)); // baseline adapter: deterministic top-10 with its price tie-break
    // Per-candidate geographic evidence in both modes. Baseline: geodesic, route not verified (unchanged). Native 2.0: the
    // adapter projects the native evidence (raw_distance_type = the criterion's straight_line, route_verified false,
    // measurement_method = native method: coordinate_haversine for a measured OSM category member, 'unavailable' when the
    // listing has no coordinates). A listing without coordinates has no observed distance.
    for(const candidate of s.recommendation.recommendations){
      const observed=candidate.criterion_results.find((c:any)=>c.criterion_id===proximity.criterion_id).observed_value;const distance=expected.find(p=>p.id===candidate.listing.listing_id)!.distance;
      if(distance===null)assert.equal(observed??null,null);else assert(Math.abs(observed-distance)<0.01);
      const evidence=s.recommendation.evidence.filter((e:any)=>e.listing_id===candidate.listing.listing_id&&e.route_verified===false);
      if(native)assert(evidence.some((e:any)=>e.field_path==='place_proximity'&&(distance===null?e.measurement_method==='unavailable':e.raw_distance_type==='straight_line'&&e.measurement_method==='coordinate_haversine')),candidate.listing.listing_id);
      else assert(evidence.some((e:any)=>e.raw_distance_type==='geodesic'));
    }
    checks.push({name:native?'Actual Neo4j active Supermarket points independently reproduce distances, fused-v2 scores and a valid select-v1 selection (native) without excluding hard matches':'Actual Neo4j Supermarket points independently reproduce distances and the top-10 soft ranking (baseline) without excluding hard matches',passed:true,detail:{places:places.length,eligible:65,tolerance_m:0.01,mode:native?'native-select-v1':'baseline'}});
    const oldRequest=s.latest_request_id;
    const first=await submit('预算改为4800新币');const last=await submit('预算改为4500新币');s=await wait(last);
    assert.equal(s.requirements.criteria.find((c:any)=>c.field==='price').value,4500);assert.equal(s.requirements.criteria.find((c:any)=>c.field==='bedrooms').value,2);assert.equal(s.requirements.criteria.find((c:any)=>c.field==='district').value,'D05');assert.notEqual(s.latest_request_id,oldRequest);
    const firstOp=(await pool.query('SELECT status FROM domain_operations WHERE session_id=$1 AND request_id=$2',[sid,first])).rows[0];assert.equal(firstOp.status,'superseded');
    const currentRequest=s.latest_request_id;await submit('预算改为4500新币',last);await new Promise(r=>setTimeout(r,1200));assert.equal((await state()).latest_request_id,currentRequest);
    checks.push({name:'Rapid correction supersedes older work; replaying the same message ID creates no new recommendation',passed:true});
    await mkdir('evaluation/reports',{recursive:true});await writeFile('evaluation/reports/advanced-live.json',JSON.stringify({checked_at:new Date().toISOString(),session_id:sid,checks},null,2));
  }finally{await pool.end();await oracle.end();}
});
