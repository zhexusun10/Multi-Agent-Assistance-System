import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {nativeRequest,projectNative} from '../src/adapters/native-recommendation.js';
import {recommendationRequestSchema,validateResponse} from '../src/contracts/recommendation-v1.js';
import {buildExplanation} from '../src/domain/explanation.js';
import type {ToolContext} from '../src/tools/executor.js';

async function fixture(){
 const native=JSON.parse((await readFile('recommendation/examples/contracts/success.json','utf8')).replace(/fixture-(\d+)/g,(_m,n)=>'910000'+n));
 native.unverified_candidates=[]; // This transport fixture isolates the verified branch.
 const original=JSON.parse(await readFile('recommendation/examples/contracts/initial-search.json','utf8'));
 const req=recommendationRequestSchema.parse({contract_version:'1.0',request_id:native.request_id,conversation_id:native.conversation_id,requirements_version:0,requirements:{listing_type:'RENT',criteria:original.requirements.criteria.map((c:any)=>({criterion_id:c.criterion_id,field:c.field==='district_code'?'district':c.field,operator:c.operator,value:c.value,strength:c.strength,...(c.strength==='soft'?{weight:c.weight}:{}),...(c.field==='bedrooms'?{unit:'count'}:c.unit?{unit:c.unit}:{})}))},result_limit:5});
 const n=native.recommendations.length;
 const envelope={response:native,presentation:{listings:native.recommendations.map((i:any)=>i.listing),counts:{assessed:n,eligible:n,unverified:0,excluded:0}}};
 const ctx:ToolContext={sessionId:req.conversation_id,operationId:randomUUID(),agent:'b',dataset:'synthetic-adapter-test',deadline:Date.now()+90000,calls:0};
 let archive:any;const save=async(_ctx:ToolContext,data:unknown)=>{archive=data;return '00000000-0000-4000-8000-000000000055';};
 return {req,envelope,ctx,save,archive:()=>archive};
}
test('native request uses native field/unit/utility/limit/version semantics, without faking missing fields',async()=>{
 const {req}=await fixture();req.requirements.criteria.push({criterion_id:'bath',field:'amenities',operator:'eq',value:'private_bathroom',strength:'hard'},{criterion_id:'near',field:'place_proximity',operator:'lte',value:1000,unit:'m',strength:'soft',weight:3,target:{place_id:'OSM:way:54519165',name:'NUS'},distance_type:'straight_line'});
 const mapped=nativeRequest(req);assert.equal(mapped.request.result_limit,4);assert.equal(mapped.request.requirements_version,1);assert.equal(mapped.request.previous_state_id,null);assert.equal(mapped.unsupported[0].field,'amenities');
 const criteria=mapped.request.requirements.criteria;assert.equal(criteria.find(c=>c.field==='bedrooms')!.unit,undefined);assert.equal(criteria.find(c=>c.field==='district_code')!.utility,'threshold');const geo=criteria.find(c=>c.field==='distance')!;assert.equal(geo.utility,'proximity');assert.equal(geo.destination_id,'OSM:way:54519165');assert.equal(geo.destination_name,undefined);assert(!criteria.some(c=>c.field==='amenities'));
 req.previous_state_id='native:state-123';assert.equal(nativeRequest(req).request.previous_state_id,'state-123');req.previous_state_id='baseline:old';assert.equal(nativeRequest(req).request.previous_state_id,null);
});
test('native output preserves order, fused scores, full breakdown and raw exchange for Explanation',async()=>{
 const f=await fixture(),rec=await projectNative(f.req,f.ctx,f.envelope,f.save);
 assert.deepEqual(rec.recommendations.map(c=>c.listing.listing_id),f.envelope.response.recommendations.map((c:any)=>c.listing.listing_id));
 assert.deepEqual(f.archive().response,f.envelope.response);
 for(const item of rec.recommendations){const original=f.envelope.response.recommendations.find((c:any)=>c.listing.listing_id===item.listing.listing_id);assert.equal(item.score,original.scores.combined_score);assert.deepEqual(rec.native!.scores[item.listing.listing_id],original.scores);}
 assert(rec.evidence.every(e=>e.source_ref===rec.native!.exchange_ref));assert(buildExplanation(f.req.requirements,rec,'synthetic','解释第一套为什么推荐').summary.includes('原生融合匹配分'));
});
test('unsupported hard app criteria stay visible and unverified; native response remains byte-equivalent',async()=>{
 const f=await fixture();f.req.requirements.criteria.push({criterion_id:'bath',field:'amenities',operator:'eq',value:'private_bathroom',strength:'hard'});
 const original=structuredClone(f.envelope.response),rec=await projectNative(f.req,f.ctx,f.envelope,f.save);
 assert.equal(rec.recommendations.length,0);assert.equal(rec.unverified_candidates.length,original.recommendations.length);assert.equal(rec.native!.status,'success');assert.equal(rec.status,'no_match');assert(rec.unverified_candidates.every(c=>c.criterion_results.find(r=>r.criterion_id==='bath')?.status==='unknown'));assert.deepEqual(f.archive().response,original);assert.deepEqual(rec.unverified_candidates.map(c=>c.score),original.recommendations.map((c:any)=>c.scores.combined_score));assert(rec.warnings.some(w=>w.code==='NATIVE_CONTRACT_GAP'));
 validateResponse(rec,f.req);
});
test('only explicit completed sample durations reach native feedback; saves/likes are not invented durations',async()=>{
 const f=await fixture();f.req.feedback=[{event_id:'save',listing_id:'91000001',occurred_at:new Date().toISOString(),event_type:'save',payload:{}},{event_id:'sample',listing_id:'91000001',occurred_at:new Date().toISOString(),event_type:'active_view',payload:{active_duration_ms:30000,source:'sample',view_session_id:'view-1',recommendation_request_id:'old-native'}}];
 const r=nativeRequest(f.req);assert.deepEqual(r.ignored_feedback,['save']);assert.equal(r.request.feedback.length,1);assert.equal(r.request.feedback[0].payload.duration_seconds,30);assert.equal(r.request.feedback[0].event_type,'listing_view_duration');assert.equal(r.request.feedback[0].recommendation_request_id,'old-native');
});
test('projection rejects wrong identity, corrupt scores and missing native listing proofs',async()=>{
 const f=await fixture();const wrong=structuredClone(f.envelope);wrong.response.conversation_id='other';await assert.rejects(projectNative(f.req,f.ctx,wrong,f.save),/identity/);
 const corrupt=structuredClone(f.envelope);corrupt.response.recommendations[0].scores.combined_score=1.5;await assert.rejects(projectNative(f.req,f.ctx,corrupt,f.save));
 const missing=structuredClone(f.envelope);missing.response.recommendations=[];missing.response.status='needs_clarification';missing.response.unverified_candidates=[{listing_id:'1234',criterion_results:[]}];await assert.rejects(projectNative(f.req,f.ctx,missing,f.save),/proof|incomplete/);
});
