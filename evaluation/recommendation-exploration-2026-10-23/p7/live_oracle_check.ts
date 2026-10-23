// P7 live check of the rewritten select-v1 / fused-v2 oracles of tests/native-recommendation-live.test.ts, run
// directly against a native service started from THIS tree on a separate port (p7/serve_smoke.py), because the
// full test needs the app stack on 8010 running this tree (not available: 8010/8092 run older code and are not
// restarted). Same SQL eligibility, same helper functions, same assertions on the native response; the request
// bodies are the contract-2.0 bodies the orchestrator would send (round 1 initial, round 2 with a complete
// previous-round snapshot: the first item browsed 90 s, the rest missing, no appointments), then round 3 with a
// tighter hard budget (pool rebuild) and an all-missing snapshot.
// Usage: P7_URL=http://127.0.0.1:8192 P7_TOKEN=... node --env-file=.env --import tsx evaluation/recommendation-exploration-2026-10-23/p7/live_oracle_check.ts
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import pg from 'pg';
import {selectV1Violations,scoreOrder,singleBrowsingReferenceScores,oracleRow,oracleSettings} from '../../../tests/helpers/select-v1-oracle.js';

const url=process.env.P7_URL!,token=process.env.P7_TOKEN!;
assert(url&&token&&!/:(8010|8091|8092)\b/.test(url),'P7_URL/P7_TOKEN of the separate smoke service are required');
const sql=new pg.Pool({connectionString:process.env.PROPERTYGURU_DATABASE_URL!.replace('postgresql+psycopg2:','postgresql:')});
const report:any={started_at:new Date().toISOString(),service:url,checks:[]};
const post=async(body:unknown)=>{const t=Date.now(),r=await fetch(url+'/app/recommend',{method:'POST',headers:{'content-type':'application/json','x-recommendation-token':token},body:JSON.stringify(body)});assert(r.ok,await r.clone().text());return {envelope:await r.json(),wall_ms:Date.now()-t};};
const criteria=(budget:number)=>[{criterion_id:'budget',field:'price',operator:'lte',value:budget,unit:'SGD/month',strength:'hard'},{criterion_id:'bedrooms',field:'bedrooms',operator:'gte',value:2,strength:'hard'},{criterion_id:'district',field:'district_code',operator:'eq',value:'D05',strength:'hard'}];
const body=(conversation:string,version:number,budget:number,previous:string|null,previous_round:unknown=null)=>({contract_version:'2.0',request_id:randomUUID(),conversation_id:conversation,requirements_version:version,requirements:{listing_type:'RENT',criteria:criteria(budget)},previous_state_id:previous,result_limit:10,feedback:{previous_round,appointments:[]},cold_start:{exited:false,exited_pool_key:null}});
try{
  const settings=oracleSettings(await readFile('recommendation/settings/recommendation.yaml','utf8'));
  const properties=(await sql.query("SELECT listing_id,price,currency,bedrooms,district_code,property_type,floor_area_sqft,latitude,longitude FROM properties WHERE listing_type='RENT' ORDER BY listing_id::text")).rows.map(oracleRow);
  assert.equal(properties.length,21431);
  const eligibleAt=(budget:number)=>properties.filter(p=>p.price!==null&&p.price<=budget&&p.bedrooms!==null&&p.bedrooms>=2&&p.district_code==='D05');
  const zero=(rows:ReturnType<typeof eligibleAt>)=>new Map(rows.map(p=>[p.listing_id,0]));
  const check=(name:string,response:any,order:string[],scores:Map<string,number>)=>{const v=selectV1Violations(response,order,10,scores);report.checks.push({name,mode:response.selection.mode,leaders:response.selection.leaders,tie_group:response.selection.tie_group?.size??null,exploration:response.selection.exploration?.candidate_count??null,violations:v});assert.deepEqual(v,[],name);};
  const conversation='p7-oracle-'+randomUUID().slice(0,8),eligible=eligibleAt(5000);
  const r1=await post(body(conversation,1,5000,null)),first=r1.envelope.response;
  assert.equal(first.status,'success');assert.equal(r1.envelope.presentation.counts.assessed,21431);assert.equal(first.pool.size,eligible.length);
  assert(first.recommendations.every((c:any)=>c.scores.combined_score===0&&c.scores.soft_score===null&&c.scores.behavior_score===null));
  check('round 1: no soft criterion, no feedback -> all scores 0, O = listing-ID order',first,scoreOrder(zero(eligible)),zero(eligible));
  assert.equal(first.selection.cold_start.coverage_c,0);assert(first.selection.cold_start.reason.includes('no_soft_requirements'));assert.equal(first.selection.cold_start.exit_conditions_met,false);
  const shown=first.recommendations.map((c:any)=>c.listing.listing_id),selected=shown[0];
  const round={request_id:first.request_id,shown_listing_ids:shown,browsing:shown.map((id:string)=>id===selected?{listing_id:id,status:'recorded',duration_seconds:90}:{listing_id:id,status:'missing',duration_seconds:null})};
  const r2=await post(body(conversation,1,5000,first.state.state_id,round)),second=r2.envelope.response;
  assert.equal(second.status,'success');assert.equal(second.pool.cache,'reused');assert.equal(second.state.run_sequence,2);
  const suppressed=['bedrooms','geography','price'],scores=singleBrowsingReferenceScores(eligible,selected,settings,suppressed);
  let maxDiff=0;for(const c of second.recommendations){const d=Math.abs(c.scores.combined_score-scores.get(c.listing.listing_id)!);maxDiff=Math.max(maxDiff,d);assert(d<1e-9,c.listing.listing_id);assert.deepEqual(c.scores.suppressed_features,suppressed);}
  check('round 2: one browsing reference (90 s) -> fused-v2 single-reference scores',second,scoreOrder(scores),scores);
  report.checks.push({name:'round 2 fused-v2 oracle: max |native - oracle| over returned items',max_abs_difference:maxDiff,reference:selected});
  const tighter=eligibleAt(4500),missing={request_id:second.request_id,shown_listing_ids:second.recommendations.map((c:any)=>c.listing.listing_id),browsing:second.recommendations.map((c:any)=>({listing_id:c.listing.listing_id,status:'missing',duration_seconds:null}))};
  const r3=await post(body(conversation,2,4500,second.state.state_id,missing)),third=r3.envelope.response;
  assert.equal(third.status,'success');assert.notEqual(third.pool.pool_key,second.pool.pool_key);assert.equal(third.pool.cache,'built');assert.equal(third.pool.size,tighter.length);
  check('round 3: tighter hard budget -> pool rebuilt; all records missing -> all scores 0',third,scoreOrder(zero(tighter)),zero(tighter));
  report.rounds=[r1,r2,r3].map(({envelope,wall_ms})=>({request_id:envelope.response.request_id,status:envelope.response.status,pool:{size:envelope.response.pool.size,cache:envelope.response.pool.cache},selection_mode:envelope.response.selection.mode,ids:envelope.response.recommendations.map((c:any)=>c.listing.listing_id),score_ranks:envelope.response.recommendations.map((c:any)=>c.score_rank),wall_ms,service_ms:envelope.response.execution.duration_ms,controller:envelope.response.execution.controller_mode}));
  report.status='passed';
}catch(e){report.status='failed';report.error=String(e);process.exitCode=1;}
finally{report.finished_at=new Date().toISOString();await writeFile('evaluation/recommendation-exploration-2026-10-23/p7/live-oracle-check.json',JSON.stringify(report,null,1)+'\n');await sql.end();console.log(JSON.stringify({status:report.status,error:report.error,checks:report.checks.map((c:any)=>c.name+(c.violations?` violations=${c.violations.length}`:''))},null,1));}
