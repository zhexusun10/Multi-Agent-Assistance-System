import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {writeFile} from 'node:fs/promises';
import pg from 'pg';

test('native full-data live: exact SQL/ranking oracle, model tools, feedback, explanation and follow-up', {skip:process.env.NATIVE_LIVE!=='1',timeout:720000},async()=>{
 const origin='http://127.0.0.1:8010/api/',owner=randomUUID()+randomUUID(),headers={authorization:'Bearer '+owner,'content-type':'application/json'};
 const app=new pg.Pool({connectionString:process.env.DATABASE_URL}),sql=new pg.Pool({connectionString:process.env.PROPERTYGURU_DATABASE_URL!.replace('postgresql+psycopg2:','postgresql:')});
 const report:any={started_at:new Date().toISOString(),checks:[],provider:'jiangshan-native',upstream:'907cfa8'};
 const api=async(path:string,body?:unknown)=>{const r=await fetch(origin+path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body)});assert(r.ok,await r.clone().text());return r.json();};
 const session=await api('sessions',{}),sid=session.session_id;
 const state=()=>api('state?session_id='+sid);
 const run=async(query:string)=>{
   const old=await state(),request_id=randomUUID(),started=Date.now();
   const r=await fetch(origin+'query',{method:'POST',headers,body:JSON.stringify({query,session_id:sid,request_id,...(old.latest_request_id?{source_request_id:old.latest_request_id}:{})})});assert(r.ok);await r.text();
   const deadline=Date.now()+210000;
   while(Date.now()<deadline){const op=(await app.query('SELECT status FROM domain_operations WHERE session_id=$1 AND request_id=$2',[sid,request_id])).rows[0];if(op&&['failed','completed'].includes(op.status)){const s=await state();assert.equal(op.status,'completed',s.messages.at(-1)?.text);report.checks.push({query,elapsed_ms:Date.now()-started,request:s.latest_request_id,counts:s.recommendation?.state.counts,ids:[...(s.recommendation?.recommendations||[]),...(s.recommendation?.unverified_candidates||[])].map((c:any)=>c.listing.listing_id)});return s;}await new Promise(done=>setTimeout(done,250));}throw Error('Native workflow timed out');
 };
 try{
   assert.equal((await api('config')).recommendation_mode,'native');
   const properties=(await sql.query("SELECT listing_id,price,bedrooms,district_code,property_type,floor_area_sqft FROM properties WHERE listing_type='RENT' ORDER BY listing_id::text")).rows;
   assert.equal(properties.length,21431);
   const eligible=properties.filter(p=>p.price!==null&&Number(p.price)<=5000&&p.bedrooms!==null&&Number(p.bedrooms)>=2&&p.district_code==='D05');
   let s=await run('帮我找租房，预算不超过5000新币，至少2房，D05');
   let rec=s.recommendation;assert(rec.native);assert.equal(rec.state.counts.assessed,21431);assert.equal(rec.state.counts.eligible,eligible.length);assert.deepEqual(rec.recommendations.map((c:any)=>c.listing.listing_id),eligible.slice(0,4).map(p=>String(p.listing_id)));assert.equal(rec.recommendations.length,4);
   const exchange=await api(`evidence?session_id=${sid}&ref=${rec.native.exchange_ref}`);
   assert.equal(exchange.data.response.execution.controller_mode,'live');assert(exchange.data.response.execution.model_calls>0);assert(exchange.data.response.execution.tool_calls>=4);assert.equal(exchange.data.response.state.source_count,21431);
   assert.deepEqual(rec.native.ranking,exchange.data.response.recommendations.map((c:any)=>c.listing.listing_id));assert.equal(exchange.data.request.result_limit,4);
   const source=s.latest_request_id,version=s.requirements_version;
   s=await run('解释一下第一套为什么推荐，为什么排在前面');assert.equal(s.latest_request_id,source);assert.equal(s.requirements_version,version);assert(s.messages.at(-1).text.includes('原生房源ID')||s.messages.at(-1).text.includes('房源ID字符串'));
   s=await run('请为第1套房源生成看房询问草稿');assert.equal(s.actions.length,1);assert.equal(s.actions[0].source_request_id,source);assert(!s.actions[0].body.includes('请为房源ID'));
   s=await run('生成当前推荐的总结文档');assert(s.artifacts.length>0);assert.equal(s.latest_request_id,source);
   const selected=rec.recommendations[0],reference=properties.find(p=>String(p.listing_id)===selected.listing.listing_id)!;
   await api('feedback',{session_id:sid,source_request_id:source,event:{event_id:randomUUID(),listing_id:selected.listing.listing_id,occurred_at:new Date().toISOString(),event_type:'active_view',payload:{source:'sample',recommendation_request_id:source,view_session_id:randomUUID(),active_duration_ms:90000}}});
   s=await run('请按当前需求重新推荐，参考新增的浏览样例');rec=s.recommendation;
   const text=(p:any)=>typeof p.property_type==='string'?p.property_type.trim().toLowerCase():null;
   const implicit=(p:any)=>.5*(text(p)!==null&&text(reference)!==null?Number(text(p)===text(reference)):0)+.5*(p.floor_area_sqft===null||reference.floor_area_sqft===null?0:Math.max(0,1-Math.abs(Number(p.floor_area_sqft)-Number(reference.floor_area_sqft))/500));
   const ranked=[...eligible].sort((a,b)=>implicit(b)-implicit(a)||(String(a.listing_id)<String(b.listing_id)?-1:1));
   assert.deepEqual(rec.recommendations.map((c:any)=>c.listing.listing_id),ranked.slice(0,4).map(p=>String(p.listing_id)));
   for(const c of rec.recommendations){const p=properties.find(p=>String(p.listing_id)===c.listing.listing_id);assert(Math.abs(c.score-implicit(p))<1e-9);assert.equal(rec.native.scores[c.listing.listing_id].ordering,'behavioral');assert(rec.native.scores[c.listing.listing_id].suppressed_features.includes('geography'));}
   assert.equal(s.requirements_version,version);
   const follow=await api(`evidence?session_id=${sid}&ref=${rec.native.exchange_ref}`);assert.equal(follow.data.request.previous_state_id,exchange.data.response.state.state_id);assert.equal(follow.data.response.state.feedback_version,1);assert.equal(follow.data.response.state.run_sequence,2);
   const replay=await fetch(process.env.RECOMMENDATION_API_URL+'/recommend',{method:'POST',headers:{'content-type':'application/json','x-recommendation-token':process.env.RECOMMENDATION_SERVICE_TOKEN!},body:JSON.stringify(follow.data.request)});assert(replay.ok);assert.deepEqual(await replay.json(),follow.data.response);
   // Simulate a native run that commits after its application operation was
   // superseded: the UI still holds the previous state, while native has advanced.
   const hiddenInput={...follow.data.request,request_id:randomUUID(),previous_state_id:follow.data.response.state.state_id,feedback:[]};
   const hiddenHttp=await fetch(process.env.RECOMMENDATION_API_URL+'/app/recommend',{method:'POST',headers:{'content-type':'application/json','x-recommendation-token':process.env.RECOMMENDATION_SERVICE_TOKEN!},body:JSON.stringify(hiddenInput)});assert(hiddenHttp.ok);const hidden=await hiddenHttp.json();assert.equal(hidden.response.status,'success');
   s=await run('预算改为4500新币，其余要求保持');rec=s.recommendation;assert.equal(rec.state.counts.eligible,eligible.filter(p=>Number(p.price)<=4500).length);
   const aligned=await api(`evidence?session_id=${sid}&ref=${rec.native.exchange_ref}`);
   assert.equal(aligned.data.transport_request.previous_state_id,follow.data.response.state.state_id);assert.equal(aligned.data.request.previous_state_id,hidden.response.state.state_id);assert.equal(aligned.data.response.state.run_sequence,4);
   report.checks.push({name:'A native run committed without UI adoption is reconciled at the transport reference boundary; native stale/requirement checks and request replay remain unchanged'});
   const presentation=await api(`listings/${rec.recommendations[0].listing.listing_id}/presentation?session_id=${sid}&source_request_id=${s.latest_request_id}`);assert(presentation.photos.length>0);assert(presentation.nearby.length>0);
   report.checks.push({name:'Independent 21,431-row eligibility, lexicographic tie order and duration-based implicit scores match exactly; immutable HTTP replay, photo/context, direct C/D routing passed'});
   report.status='passed';report.session_id=sid;report.owner=undefined;
   // Owner stays in ignored runtime storage for an isolated browser walkthrough.
   await writeFile('data/.runtime/native-browser-session.json',JSON.stringify({owner,session_id:sid}));
 }catch(e){report.status='failed';report.error=String(e);throw e;}
 finally{report.finished_at=new Date().toISOString();await writeFile('evaluation/reports/native-integration-live.json',JSON.stringify(report,null,2));await app.end();await sql.end();}
});
