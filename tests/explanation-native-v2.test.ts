import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {projectNative} from '../src/adapters/native-recommendation.js';
import {recommendationRequestSchema} from '../src/contracts/recommendation-v1.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {explanationModelInput} from '../src/agents/explanation/model-input.js';
import {recoverExplanation} from '../src/agents/explanation/recovery.js';

// cold-start-exploration.json: price exploration (mpd-v1 D = 0, active); ten-results.json: near-tie sampling (inactive).
async function setup(question:string,fixture='cold-start-exploration.json'){
 const f=JSON.parse(readFileSync(new URL(`./fixtures/recommendation-v2/${fixture}`,import.meta.url),'utf8').replace(/fixture-(\d+)/g,(_m,n)=>'910000'+n));
 const req=recommendationRequestSchema.parse(f.v1_request);let archive:unknown;
 const rec=await projectNative(req,{sessionId:req.conversation_id,operationId:'00000000-0000-4000-8000-000000000010',agent:'b',dataset:'synthetic-ten-results',deadline:Date.now()+60000,calls:0},f.envelope,async(_ctx,value)=>{archive=value;return '00000000-0000-4000-8000-000000000011';});
 const bundle=addDecisionSupport(makeExplanationBundle(req.requirements,rec,question,archive),req.requirements,rec);
 return {req,rec,bundle};
}

test('native 2.0 exploration reaches ranking evidence without being described as a stable score order',async()=>{
 const {req,rec,bundle}=await setup('解释一下这次房源为什么这样排序');
 assert.equal(rec.native!.selection!.mode,'cold_start_exploration');
 const method=bundle.facts.find(f=>f.ref==='ranking:method')!;
 assert.match(method.text,/不同价格层次/);assert.match(method.text,/不等于纯匹配分排名/);assert(!method.text.includes('沿用固定次序'));
 assert.equal(bundle.decision_support!.target_listing_ids.length,5);
 assert(explanationModelInput(bundle).input.facts.some((f:any)=>f.text.includes('不同价格层次')));
 const output=recoverExplanation(bundle,req.requirements,rec);
 for(const id of output.focus_listing_ids)assert(output.paragraphs.find(p=>p.listing_id===id)!.support_refs.includes(`selection:${id}`));
});

test('native 2.0 near-tie sampling is described as a draw among near-equal matches',async()=>{
 const {rec,bundle}=await setup('解释一下这次房源为什么这样排序','ten-results.json');
 assert.equal(rec.native!.selection!.mode,'near_tie_sampling');
 const method=bundle.facts.find(f=>f.ref==='ranking:method')!;
 assert.match(method.text,/匹配程度非常接近/);assert.match(method.text,/抽样选择/);
 assert(!method.text.includes('不同价格层次'));assert(!method.text.includes('沿用固定次序'));
});

test('ordinary property guidance keeps the latest local role and does not force ranking diagnostics into every introduction',async()=>{
 const {bundle}=await setup('介绍一下这几套有什么特点');
 assert.equal(bundle.decision_support!.explain_order,false);
 assert(explanationModelInput(bundle).input.facts.every((f:any)=>!f.text.includes('不同价格层次')));
 assert(bundle.decision_support!.profiles.every(p=>p.core_refs!.length>0));
});
