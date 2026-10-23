import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {writeFile} from 'node:fs/promises';
import pg from 'pg';
import {readFile} from 'node:fs/promises';
import {selectV1Violations,scoreOrder,singleBrowsingReferenceScores,oracleRow,oracleSettings} from './helpers/select-v1-oracle.js';

test('native full-data live (contract 2.0): SQL eligibility + select-v1 / fused-v2 oracles, model tools, feedback snapshot, explanation and follow-up', {skip:process.env.NATIVE_LIVE!=='1',timeout:720000},async()=>{
 const origin='http://127.0.0.1:8010/api/',owner=randomUUID()+randomUUID(),headers={authorization:'Bearer '+owner,'content-type':'application/json'};
 const app=new pg.Pool({connectionString:process.env.DATABASE_URL}),sql=new pg.Pool({connectionString:process.env.PROPERTYGURU_DATABASE_URL!.replace('postgresql+psycopg2:','postgresql:')});
 const report:any={started_at:new Date().toISOString(),checks:[],provider:'jiangshan-native',upstream:'907cfa8',contract:'2.0'};
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
   const settings=oracleSettings(await readFile('recommendation/settings/recommendation.yaml','utf8'));
   const properties=(await sql.query("SELECT listing_id,price,currency,bedrooms,district_code,property_type,floor_area_sqft,latitude,longitude FROM properties WHERE listing_type='RENT' ORDER BY listing_id::text")).rows.map(oracleRow);
   assert.equal(properties.length,21431);
   // Canonical rows as the native repository normalises them; all three criteria are hard, so this is the pool.
   const eligibleAt=(budget:number)=>properties.filter(p=>p.price!==null&&p.price<=budget&&p.bedrooms!==null&&p.bedrooms>=2&&p.district_code==='D05');
   const eligible=eligibleAt(5000);
   // No soft criterion and no feedback: every fused-v2 score is 0, so O is the pool in listing-ID order (all tied).
   const zero=(rows:typeof eligible)=>new Map(rows.map(p=>[p.listing_id,0]));
   const checkSelectV1=(response:any,order:string[],scores:Map<string,number>)=>assert.deepEqual(selectV1Violations(response,order,10,scores),[],JSON.stringify(response.selection));
   let s=await run('帮我找租房，预算不超过5000新币，至少2房，D05');
   let rec=s.recommendation;assert(rec.native);assert.equal(rec.native.contract,'2.0');assert.equal(rec.state.counts.assessed,21431);assert.equal(rec.state.counts.eligible,eligible.length);
   const exchange=await api(`evidence?session_id=${sid}&ref=${rec.native.exchange_ref}`),first=exchange.data.response;
   assert.equal(first.execution.controller_mode,'live');assert(first.execution.model_calls>0);assert(first.execution.tool_calls>=4);assert.equal(first.state.source_count,21431);assert.equal(first.pool.size,eligible.length);
   assert.equal(exchange.data.request.result_limit,10);assert.equal(exchange.data.request.feedback.previous_round,null);
   assert(first.recommendations.every((c:any)=>c.scores.combined_score===0&&c.scores.soft_score===null&&c.scores.behavior_score===null));
   checkSelectV1(first,scoreOrder(zero(eligible)),zero(eligible));
   // Without soft criteria C = 0, so cold start is active and can never be exited (R9).
   assert.equal(first.selection.cold_start.coverage_c,0);assert(first.selection.cold_start.reason.includes('no_soft_requirements'));assert.equal(first.selection.cold_start.exit_conditions_met,false);
   assert.deepEqual(rec.native.ranking,first.recommendations.map((c:any)=>c.listing.listing_id));assert.deepEqual(rec.recommendations.map((c:any)=>c.listing.listing_id),rec.native.ranking);assert.equal(rec.unverified_candidates.length,0);
   const source=s.latest_request_id,version=s.requirements_version;
   s=await run('解释一下第一套为什么推荐，为什么排在前面');assert.equal(s.latest_request_id,source);assert.equal(s.requirements_version,version);assert(s.messages.at(-1).text.includes('原生房源ID')||s.messages.at(-1).text.includes('房源ID字符串'));
   s=await run('请为第1套房源生成看房询问草稿');assert.equal(s.actions.length,1);assert.equal(s.actions[0].source_request_id,source);assert(!s.actions[0].body.includes('请为房源ID'));
   s=await run('生成当前推荐的总结文档');assert(s.artifacts.length>0);assert.equal(s.latest_request_id,source);
   const selected=rec.recommendations[0];
   await api('feedback',{session_id:sid,source_request_id:source,event:{event_id:randomUUID(),listing_id:selected.listing.listing_id,occurred_at:new Date().toISOString(),event_type:'active_view',payload:{source:'sample',recommendation_request_id:source,view_session_id:randomUUID(),active_duration_ms:90000}}});
   s=await run('请按当前需求重新推荐，参考新增的浏览样例');rec=s.recommendation;
   assert.equal(s.requirements_version,version);
   const follow=await api(`evidence?session_id=${sid}&ref=${rec.native.exchange_ref}`),second=follow.data.response;
   assert.equal(follow.data.request.previous_state_id,first.state.state_id);assert.equal(second.state.run_sequence,2);
   // Complete feedback snapshot built by the orchestrator: previous round = the adopted native round, one browsing
   // record per shown ID (the sampled one recorded at 90 s, the rest missing), no counted appointment (the inquiry
   // draft is not a counted VIEWING status). The native side never accumulates feedback.
   const round=follow.data.request.feedback.previous_round;
   assert.equal(round.request_id,first.request_id);assert.deepEqual(round.shown_listing_ids,first.recommendations.map((c:any)=>c.listing.listing_id));
   assert.deepEqual(round.browsing.filter((b:any)=>b.status==='recorded'),[{listing_id:selected.listing.listing_id,status:'recorded',duration_seconds:90}]);
   assert.equal(round.browsing.length,round.shown_listing_ids.length);assert.deepEqual(follow.data.request.feedback.appointments,[]);
   // fused-v2 with ONE browsing reference: combined = behavior_weight x browsing_weight x pair similarity (duration
   // cancels); hard price/bedrooms/district suppress price, bedrooms and geography; the reference itself has no pair.
   const suppressed=['bedrooms','geography','price'],scores=singleBrowsingReferenceScores(eligible,selected.listing.listing_id,settings,suppressed);
   for(const c of second.recommendations){assert(Math.abs(c.scores.combined_score-scores.get(c.listing.listing_id)!)<1e-9,c.listing.listing_id);assert.deepEqual(c.scores.suppressed_features,suppressed);}
   checkSelectV1(second,scoreOrder(scores),scores);
   assert.deepEqual(rec.native.ranking,second.recommendations.map((c:any)=>c.listing.listing_id));
   const replay=await fetch(process.env.RECOMMENDATION_API_URL+'/recommend',{method:'POST',headers:{'content-type':'application/json','x-recommendation-token':process.env.RECOMMENDATION_SERVICE_TOKEN!},body:JSON.stringify(follow.data.request)});assert(replay.ok);assert.deepEqual(await replay.json(),follow.data.response);
   // Simulate a native run that commits after its application operation was
   // superseded: the UI still holds the previous state, while native has advanced.
   const hiddenInput={...follow.data.request,request_id:randomUUID(),previous_state_id:second.state.state_id,feedback:{previous_round:null,appointments:[]}};
   const hiddenHttp=await fetch(process.env.RECOMMENDATION_API_URL+'/app/recommend',{method:'POST',headers:{'content-type':'application/json','x-recommendation-token':process.env.RECOMMENDATION_SERVICE_TOKEN!},body:JSON.stringify(hiddenInput)});assert(hiddenHttp.ok);const hidden=await hiddenHttp.json();assert.equal(hidden.response.status,'success');
   s=await run('预算改为4500新币，其余要求保持');rec=s.recommendation;const tighter=eligibleAt(4500);assert.equal(rec.state.counts.eligible,tighter.length);
   const aligned=await api(`evidence?session_id=${sid}&ref=${rec.native.exchange_ref}`);
   assert.equal(aligned.data.transport_request.previous_state_id,second.state.state_id);assert.equal(aligned.data.request.previous_state_id,hidden.response.state.state_id);assert.equal(aligned.data.response.state.run_sequence,4);
   // The hard change rebuilds the pool (new pool_key); the previous round has no new sample, so every record is
   // missing, every score is 0 and select-v1 explores the whole tied pool again.
   assert.notEqual(aligned.data.response.pool.pool_key,second.pool.pool_key);assert.equal(aligned.data.response.pool.cache,'built');
   assert(aligned.data.request.feedback.previous_round.browsing.every((b:any)=>b.status==='missing'));
   checkSelectV1(aligned.data.response,scoreOrder(zero(tighter)),zero(tighter));
   report.checks.push({name:'A native run committed without UI adoption is reconciled at the transport reference boundary; native stale/requirement checks and request replay remain unchanged'});
   const presentation=await api(`listings/${rec.recommendations[0].listing.listing_id}/presentation?session_id=${sid}&source_request_id=${s.latest_request_id}`);assert(presentation.photos.length>0);assert(presentation.nearby.length>0);
   report.checks.push({name:'Independent 21,431-row eligibility, fused-v2 single-reference browsing scores (1e-9) and select-v1 selections (leaders, tie group / exploration window, score_rank) match; complete feedback snapshot, immutable HTTP replay, photo/context, direct C/D routing passed'});
   report.status='passed';report.session_id=sid;report.owner=undefined;
   // Owner stays in ignored runtime storage for an isolated browser walkthrough.
   await writeFile('data/.runtime/native-browser-session.json',JSON.stringify({owner,session_id:sid}));
 }catch(e){report.status='failed';report.error=String(e);throw e;}
 finally{report.finished_at=new Date().toISOString();await writeFile('evaluation/reports/native-integration-live.json',JSON.stringify(report,null,2));await app.end();await sql.end();}
});
