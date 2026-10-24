// Native request (main contract 1.0 + behavior_context + cold_start) / response 2.0 validation (TS half)
// driven by the SAME fixture files as recommendation/tests/test_contract_v2_fixtures.py: tests/fixtures/recommendation-v2/.
// The app contract requires numeric listing IDs: fixture-NN -> 910000NN, consistently across a file.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {z} from 'zod';
import {nativeRequest,projectNative} from '../src/adapters/native-recommendation.js';
import {recommendationRequestSchema,validateResponse} from '../src/contracts/recommendation-v1.js';
import type {ToolContext} from '../src/tools/executor.js';
import {nativeRequestSchema,nativeResponseSchema,nativeItemSchema,coldStartInputSchema,COUNTED_APPOINTMENT_SIGNALS,NATIVE_MAX_RESULTS,NOT_EXITED} from '../src/contracts/recommendation-native.js';
import {coldStartAfterAdoption} from '../src/domain/cold-start.js';
import {interpretFeedback} from '../src/domain/selection.js';

const read=async(path:string)=>JSON.parse(await readFile(path,'utf8'));
const numeric=async(path:string)=>JSON.parse((await readFile(path,'utf8')).replace(/fixture-(\d+)/g,(_m,n)=>'910000'+n));
const dir='tests/fixtures/recommendation-v2/';
const yaml=await readFile('recommendation/settings/recommendation.yaml','utf8');

test('generated Python examples parse with the strict 2.0 mirror; max_result_limit == schema maximum',async()=>{
 const folder='recommendation/examples/contracts/';let responses=0,requests=0;
 for(const name of (await readdir(folder)).filter(n=>n.endsWith('.json'))){
  const body=await numeric(folder+name);
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

test('TS counted viewing signals equal ranking.behavior.counted_appointment_signals (YAML)',()=>{
 const list=yaml.match(/counted_appointment_signals:\s*\[([^\]]*)\]/)![1].split(',').map(s=>s.trim());
 assert.deepEqual([...COUNTED_APPOINTMENT_SIGNALS],[...list].sort());
});

test('contract cases: nativeRequestSchema accepts exactly the cases Python accepts',async()=>{
 const {cases}=await numeric(dir+'contract-cases.json');assert(cases.length>=25);
 for(const c of cases){
  const parsed=nativeRequestSchema.safeParse(c.body);assert.equal(parsed.success,c.valid,`${c.name}: ${parsed.success?'accepted':parsed.error.message}`);
 }
});

test('flow rounds: nativeRequest(v1_request, cold_start) == native_body (version+1, native: prefix, behavior_context and events, no field loss), pure',async()=>{
 const flow=await numeric(dir+'flow.json');
 for(const round of flow.rounds){
  const v1=recommendationRequestSchema.parse(round.v1_request),cold=coldStartInputSchema.parse(round.cold_start) as any;
  const body=nativeRequest(v1,cold).request;
  assert.deepEqual(JSON.parse(JSON.stringify(body)),round.native_body,round.name);
  assert.equal(body.requirements_version,v1.requirements_version+1);
  assert.equal(body.previous_state_id,v1.previous_state_id?.replace(/^native:/,'')??null);
  assert.deepEqual(body.behavior_context,v1.behavior_context);
  assert.equal(JSON.stringify(nativeRequest(structuredClone(v1),structuredClone(cold)).request),JSON.stringify(body));
  nativeRequestSchema.parse(body);
 }
 assert.deepEqual(flow.rounds[0].cold_start,NOT_EXITED);
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
