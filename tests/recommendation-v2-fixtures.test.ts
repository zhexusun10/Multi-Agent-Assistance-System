// Contract 2.0 / P2 validation (TS half) driven by the SAME fixture files as
// recommendation/tests/test_contract_v2_fixtures.py: tests/fixtures/recommendation-v2/.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {z} from 'zod';
import {nativeRequest,projectNative} from '../src/adapters/native-recommendation.js';
import {recommendationRequestSchema,validateResponse} from '../src/contracts/recommendation-v1.js';
import type {ToolContext} from '../src/tools/executor.js';
import {nativeRequestSchema,nativeResponseSchema,nativeItemSchema,feedbackSidecarSchema,COUNTED_APPOINTMENT_STATUSES,NATIVE_MAX_RESULTS,EMPTY_SIDECAR} from '../src/contracts/recommendation-native.js';
import {buildFeedbackSidecar,coldStartAfterAdoption} from '../src/domain/feedback-sidecar.js';
import {interpretFeedback} from '../src/domain/selection.js';

const read=async(path:string)=>JSON.parse(await readFile(path,'utf8'));
const dir='tests/fixtures/recommendation-v2/';
const yaml=await readFile('recommendation/settings/recommendation.yaml','utf8');

test('generated Python examples parse with the strict 2.0 mirror; max_result_limit == schema maximum',async()=>{
 const folder='recommendation/examples/contracts/';let responses=0,requests=0;
 for(const name of (await readdir(folder)).filter(n=>n.endsWith('.json'))){
  const body=await read(folder+name);
  if('status' in body){const r=nativeResponseSchema.parse(body);assert.match(r.execution.config_hash,/^[0-9a-f]{64}$/);assert.match(r.execution.selection_hash,/^[0-9a-f]{64}$/);responses++;}else{nativeRequestSchema.parse(body);requests++;}
 }
 assert(responses>=4&&requests>=3);
 const max=Number(yaml.match(/^\s*max_result_limit:\s*(\d+)/m)![1]);
 const item=z.toJSONSchema(nativeItemSchema) as any,response=z.toJSONSchema(nativeResponseSchema,{unrepresentable:'any'}) as any,request=z.toJSONSchema(nativeRequestSchema,{unrepresentable:'any'}) as any;
 const requestMax=(request.properties.result_limit.anyOf??[request.properties.result_limit]).find((x:any)=>x.maximum!==undefined).maximum;
 assert.deepEqual([max,item.properties.rank.maximum,response.properties.recommendations.maxItems,requestMax,NATIVE_MAX_RESULTS],[10,10,10,10,10]);
});

test('10 results: the shared ten-results fixture parses with the strict mirror and projects to V1 ranks 1..10',async()=>{
 // Same file as recommendation/tests/test_contract_v2_fixtures.py::test_ten_results_fixture_is_valid_and_reproduced.
 // Production listing IDs are numeric: fixture-NN -> 910000NN, consistently across the envelope.
 const fixture=JSON.parse((await readFile(dir+'ten-results.json','utf8')).replace(/fixture-(\d+)/g,(_m,n)=>'910000'+n));
 const native=nativeResponseSchema.parse(fixture.envelope.response);
 assert.equal(native.recommendations.length,NATIVE_MAX_RESULTS);assert.deepEqual(native.recommendations.map(i=>i.rank),[1,2,3,4,5,6,7,8,9,10]);
 const v1=recommendationRequestSchema.parse(fixture.v1_request);assert.equal(v1.result_limit,10);
 assert.deepEqual(nativeRequest(v1).request,JSON.parse(JSON.stringify(fixture.native_body).replace(/fixture-(\d+)/g,(_m,n)=>'910000'+n)));
 const ctx:ToolContext={sessionId:v1.conversation_id,operationId:'00000000-0000-4000-8000-000000000010',agent:'b',dataset:'synthetic-ten-results',deadline:Date.now()+90000,calls:0};
 const rec=await projectNative(v1,ctx,fixture.envelope,async()=>'00000000-0000-4000-8000-000000000011');
 assert.equal(rec.status,'success');assert.deepEqual(rec.recommendations.map(c=>c.rank),[1,2,3,4,5,6,7,8,9,10]);
 assert.deepEqual(rec.recommendations.map(c=>c.listing.listing_id),native.recommendations.map(i=>i.listing.listing_id));
 assert.deepEqual(rec.native!.ranking,native.recommendations.map(i=>i.listing.listing_id));
 validateResponse(rec,v1);
});

test('response cases: the strict mirror enforces the same 2.0 response invariants as the Python model',async()=>{
 const {cases}=await read(dir+'response-cases.json'),base=(await read(dir+'ten-results.json')).envelope.response;assert(cases.length>=14);
 for(const c of cases){
  const body=structuredClone(base);
  for(const op of c.ops){let target:any=body;for(const key of op.path)target=target[key];if(op.op==='merge')Object.assign(target,structuredClone(op.value));else if(op.op==='append')target.push(structuredClone(op.value));else throw Error(op.op);}
  const parsed=nativeResponseSchema.safeParse(body);assert.equal(parsed.success,c.valid,`${c.name}: ${parsed.success?'accepted':parsed.error.message}`);
 }
});

test('TS valid appointment statuses equal ranking.behavior.counted_appointment_statuses (YAML)',()=>{
 const list=yaml.match(/counted_appointment_statuses:\s*\[([^\]]*)\]/)![1].split(',').map(s=>s.trim());
 assert.deepEqual([...COUNTED_APPOINTMENT_STATUSES],[...list].sort());
});

test('contract cases: nativeRequestSchema accepts exactly the cases Python accepts',async()=>{
 const {cases}=await read(dir+'contract-cases.json');assert(cases.length>=25);
 for(const c of cases){
  const parsed=nativeRequestSchema.safeParse(c.body);assert.equal(parsed.success,c.valid,`${c.name}: ${parsed.success?'accepted':parsed.error.message}`);
  if(c.service_error&&c.body.feedback.appointments.some((a:any)=>!(COUNTED_APPOINTMENT_STATUSES as readonly string[]).includes(a.statuses[0])))
   assert.equal(feedbackSidecarSchema.safeParse({...c.body.feedback,cold_start:c.body.cold_start}).success,false,`${c.name}: the orchestrator sidecar must never carry it`);
 }
});

test('flow rounds: nativeRequest(v1_request, sidecar) == native_body (version+1, native: prefix, snapshot, no field loss), pure',async()=>{
 const flow=await read(dir+'flow.json');
 for(const round of flow.rounds){
  const v1=recommendationRequestSchema.parse(round.v1_request),sidecar=feedbackSidecarSchema.parse(round.sidecar);
  const body=nativeRequest(v1,sidecar).request;
  assert.deepEqual(body,round.native_body,round.name);
  assert.equal(body.requirements_version,v1.requirements_version+1);
  assert.equal(body.previous_state_id,v1.previous_state_id?.replace(/^native:/,'')??null);
  assert.equal(JSON.stringify(nativeRequest(structuredClone(v1),structuredClone(sidecar)).request),JSON.stringify(body));
  nativeRequestSchema.parse(body);
 }
 assert.deepEqual(flow.rounds[0].sidecar,EMPTY_SIDECAR);
});

test('sidecar cases: buildFeedbackSidecar == expected (native adopted, hardGap, baseline, legacy, empty, none); always a valid native body',async()=>{
 const {cases}=await read(dir+'sidecar-cases.json');assert(cases.length>=6);
 for(const c of cases){
  const built=buildFeedbackSidecar(c.input.recommendation,c.input.durations,c.input.actions,c.input.cold_start);
  assert.deepEqual(built,c.expected,c.name);
  const v1=recommendationRequestSchema.parse({contract_version:'1.0',request_id:'v2-next',conversation_id:'v2-fixture',requirements_version:0,requirements:{listing_type:'RENT',criteria:[]},result_limit:10});
  nativeRequestSchema.parse(nativeRequest(v1,built).request);
  // Shuffled rows give the identical snapshot (deterministic; persisted once, never recomputed).
  assert.deepEqual(buildFeedbackSidecar(c.input.recommendation,[...c.input.durations].reverse(),[...c.input.actions].reverse(),c.input.cold_start),built);
 }
});

test('cold-start exit is stored only when the adopted native response evaluated and met all exit conditions',()=>{
 const rec=(cold:any)=>({native:{contract:'2.0',pool:{pool_key:'pk-pool'},selection:{cold_start:cold}}}) as any;
 assert.equal(coldStartAfterAdoption(null,rec({evaluated:false,exit_conditions_met:false,pool_key:'pk'})),null);
 assert.equal(coldStartAfterAdoption(null,rec({evaluated:true,exit_conditions_met:false,pool_key:'pk'})),null);
 assert.deepEqual(coldStartAfterAdoption(null,rec({evaluated:true,exit_conditions_met:true,pool_key:'pk'})),{exited:true,exited_pool_key:'pk'});
 assert.deepEqual(coldStartAfterAdoption(null,rec({evaluated:true,exit_conditions_met:true,pool_key:null})),{exited:true,exited_pool_key:'pk-pool'});
 const kept={exited:true,exited_pool_key:'older'};assert.deepEqual(coldStartAfterAdoption(kept,rec({evaluated:true,exit_conditions_met:false,pool_key:'pk'})),kept);
 assert.deepEqual(coldStartAfterAdoption(kept,{native:{provider:'jiangshan-native'}} as any),kept);// legacy/baseline rounds never change it
});

test('interpretFeedback understands ordinals 1-10 (Chinese numerals and digits) for the 10-result list',()=>{
 const listings=Array.from({length:10},(_,i)=>({listing:{listing_id:String(91000001+i)}}));
 const rec={request_id:'r',recommendations:listings,unverified_candidates:[]};
 const pick=(q:string)=>interpretFeedback(q,rec,'r','op').map(e=>e.listing_id)[0];
 assert.equal(pick('第十套太小了'),'91000010');assert.equal(pick('第10套不喜欢'),'91000010');assert.equal(pick('第六套喜欢'),'91000006');assert.equal(pick('第 7 套面积小'),'91000007');assert.equal(pick('第一套喜欢'),'91000001');
 assert.equal(pick('第11套太小'),undefined);assert.equal(interpretFeedback('第十套太小',rec,'other','op').length,0);
});
