import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {projectNative} from '../src/adapters/native-recommendation.js';
import {recommendationRequestSchema} from '../src/contracts/recommendation-v1.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {explanationModelInput} from '../src/agents/explanation/model-input.js';
import {recoverExplanation} from '../src/agents/explanation/recovery.js';
import {withPresentation} from '../src/shared/recommendation-display.js';

// cold-start-exploration.json: price exploration (mpd-v1 D = 0, active); ten-results.json: near-tie sampling (inactive).
async function setup(question:string,fixture='cold-start-exploration.json'){
 const f=JSON.parse(readFileSync(new URL(`./fixtures/recommendation-v2/${fixture}`,import.meta.url),'utf8').replace(/fixture-(\d+)/g,(_m,n)=>'910000'+n));
 const req=recommendationRequestSchema.parse(f.v1_request);let archive:unknown;
 const rec=await projectNative(req,{sessionId:req.conversation_id,operationId:'00000000-0000-4000-8000-000000000010',agent:'b',dataset:'synthetic-ten-results',deadline:Date.now()+60000,calls:0},f.envelope,async(_ctx,value)=>{archive=value;return '00000000-0000-4000-8000-000000000011';});
 const bundle=addDecisionSupport(makeExplanationBundle(req.requirements,rec,question,archive),req.requirements,rec);
 return {req,rec,bundle,archive};
}

test('native 2.0 exploration reaches ranking evidence without being described as a stable score order',async()=>{
 const {req,rec,bundle}=await setup('解释一下这次房源为什么这样排序');
 assert.equal(rec.native!.selection!.mode,'cold_start_exploration');
 const method=bundle.facts.find(f=>f.ref==='ranking:method')!;
 assert.match(method.text,/price strata/i);assert.match(method.text,/not pure score rank/);assert(!method.text.includes('沿用固定次序'));
 assert.equal(bundle.decision_support!.target_listing_ids.length,5);
 assert(explanationModelInput(bundle).input.facts.some((f:any)=>f.text.includes('compare price levels')));
 const output=recoverExplanation(bundle,req.requirements,rec);
 for(const id of output.focus_listing_ids)assert(output.paragraphs.find(p=>p.listing_id===id)!.support_refs.includes(`selection:${id}`));
});

test('a cold-start list whose returned prefix holds only leaders is not described as price-tier picks',async()=>{
 const f=JSON.parse(readFileSync(new URL('./fixtures/recommendation-v2/cold-start-exploration.json',import.meta.url),'utf8').replace(/fixture-(\d+)/g,(_m,n)=>'910000'+n));
 for(const item of f.envelope.response.recommendations)item.selection_reason='leader';// e.g. result_limit below the leaders
 const req=recommendationRequestSchema.parse(f.v1_request);let archive:unknown;
 const rec=await projectNative(req,{sessionId:req.conversation_id,operationId:'00000000-0000-4000-8000-000000000012',agent:'b',dataset:'synthetic-leaders',deadline:Date.now()+60000,calls:0},f.envelope,async(_c,v)=>{archive=v;return '00000000-0000-4000-8000-000000000013';});
 const bundle=makeExplanationBundle(req.requirements,rec,'解释一下这次房源为什么这样排序',archive);
 assert.equal(rec.native!.selection!.mode,'cold_start_exploration');
 assert(!/price strata/i.test(bundle.facts.find(x=>x.ref==='ranking:method')!.text));
});

test('price-tier wording follows the displayed five, not the reserved ten',async()=>{
 const f=JSON.parse(readFileSync(new URL('./fixtures/recommendation-v2/cold-start-exploration.json',import.meta.url),'utf8').replace(/fixture-(\d+)/g,(_m,n)=>'910000'+n));
 f.envelope.response.recommendations.slice(0,5).forEach((item:any)=>item.selection_reason='leader');// samples only in positions 6-10
 const req=recommendationRequestSchema.parse(f.v1_request);let archive:unknown;
 const rec=await projectNative(req,{sessionId:req.conversation_id,operationId:'00000000-0000-4000-8000-000000000014',agent:'b',dataset:'synthetic-leaders',deadline:Date.now()+60000,calls:0},f.envelope,async(_c,v)=>{archive=v;return '00000000-0000-4000-8000-000000000015';});
 const method=(r:typeof rec)=>makeExplanationBundle(req.requirements,r,'解释一下这次房源为什么这样排序',archive).facts.find(x=>x.ref==='ranking:method')!.text;
 assert(!/price strata/i.test(method(withPresentation(rec,5))));
 assert.match(method(withPresentation(rec,10)),/price strata/i);
});

test('native 2.0 near-tie sampling is described only when a sampled home is displayed',async()=>{
 // ten-results.json: leaders in positions 1-6, tie-group samples in 7-10.
 const {req,rec,bundle,archive}=await setup('解释一下这次房源为什么这样排序','ten-results.json');
 assert.equal(rec.native!.selection!.mode,'near_tie_sampling');
 assert.doesNotMatch(bundle.facts.find(f=>f.ref==='ranking:method')!.text,/very close matches/);
 const method=makeExplanationBundle(req.requirements,withPresentation(rec,10),'解释一下这次房源为什么这样排序',archive).facts.find(f=>f.ref==='ranking:method')!.text;
 assert.match(method,/very close matches/);assert.match(method,/are sampled/);
 assert(!/price strata/i.test(method));assert(!/stable order/.test(method));
});

test('ordinary property guidance keeps the latest local role and does not force ranking diagnostics into every introduction',async()=>{
 const {bundle}=await setup('介绍一下这几套有什么特点');
 assert.equal(bundle.decision_support!.explain_order,false);
 assert(explanationModelInput(bundle).input.facts.every((f:any)=>!f.text.includes('compare price levels')));
 assert(bundle.decision_support!.profiles.every(p=>p.core_refs!.length>0));
});
