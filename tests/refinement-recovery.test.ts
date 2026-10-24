import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {compileRefinement} from '../src/agents/requirement/refinement/answers.js';
import {publicRefinement,refinementSubmissionText} from '../src/contracts/requirement-refinement.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {recoverExplanation} from '../src/agents/explanation/recovery.js';
import {generateExplanation} from '../src/agents/explanation/generator.js';
import {validateExplanation} from '../src/agents/explanation/validation.js';
import {recommendationForDisplay} from '../src/shared/recommendation-display.js';
import {DomainStore} from '../src/stores/domain.js';

const f=JSON.parse(readFileSync(new URL('./fixtures/explanation-refinement-regression.json',import.meta.url),'utf8'));
const make=()=>addDecisionSupport(makeExplanationBundle(f.requirements,f.recommendation,f.query),f.requirements,f.recommendation,f.enrichment);

test('the exact submitted 500-1500 sqft plus keep-budget choices preserve SGD3000 and produce an unambiguous summary',()=>{
 const base={...f.requirements,criteria:f.requirements.criteria.filter((c:any)=>c.field!=='floor_area_sqft')};
 const c=compileRefinement(f.plan.body.questions,{submission_id:randomUUID(),answers:f.plan.answers},base);
 assert.deepEqual(c.requirements,f.requirements);assert.equal(c.draft.upsert.length,1);
 assert.match(c.summary[1],/保留原预算上限 3,000 新币/);assert(!c.summary[1].includes('不限'));
 const publicPlan=publicRefinement({...f.plan,id:'plan',status:'SUBMITTED'});
 assert.deepEqual(publicPlan.answer_summary,c.summary);assert(refinementSubmissionText(c.summary).includes('不新增理想月租偏好'));
});

test('the reported result replays offline into five grounded introductions with no changes to requirements or ranking',()=>{
 const before=JSON.stringify(f),bundle=make(),out=recoverExplanation(bundle,f.requirements,f.recommendation);
 assert.equal(out.focus_listing_ids.length,5);validateExplanation(out,bundle);
 assert(out.paragraphs.some(p=>p.text.includes('800平方英尺')));
 assert(out.paragraphs.some(p=>p.text.includes('CLEMENTI')&&p.text.includes('还缺少资料')));
 assert(!out.paragraphs.some(p=>/本轮未完成|JSON|分数|权重|原生|候选池/.test(p.text)));
 assert.equal(JSON.stringify(f),before);
});

test('other-four and listing-four references are scoped counts, never permission to invent four bedrooms or route minutes',()=>{
 const b=make(),good=recoverExplanation(b,f.requirements,f.recommendation),p=good.paragraphs.find(p=>p.listing_id==='20114703')!;
 p.support_refs.push('cohort:20114703:floor_area_sqft','cohort:20114703:price');
 p.text+='比其他4套的房源面积都大；月租和第4套相同。';validateExplanation(good,b);
 const wrongCount=structuredClone(good);wrongCount.paragraphs[5].text=p.text.replace('其他4套','其他8套');assert.throws(()=>validateExplanation(wrongCount,b),/比较的其他房源数量/);
 const wrongBeds=structuredClone(good);wrongBeds.paragraphs[5].text+='有4间卧室。';assert.throws(()=>validateExplanation(wrongBeds,b),/GROUNDING/);
 const wrongTime=structuredClone(good);wrongTime.paragraphs[5].text+='步行4分钟到地铁站。';assert.throws(()=>validateExplanation(wrongTime,b),/GROUNDING/);
});

test('writer failure keeps the accepted homes, saves diagnostics and returns validated facts without another paid request',async()=>{
 let calls=0;const diagnostics:unknown[]=[];
 const model={withStructuredOutput:()=>({invoke:async()=>{calls++;throw Error('network timeout');}})} as unknown as BaseChatModel;
 const out=await generateExplanation(f.requirements,f.recommendation,'2026-10-09',f.query,model,'skill',undefined,f.enrichment,async d=>{diagnostics.push(d);return 'saved-diagnostic';});
 assert.equal(calls,1);assert.equal(out.generation.mode,'evidence_recovery');assert.equal(out.generation.semantic_audit,'not_run');
 assert.equal(out.generation.diagnostic_ref,'saved-diagnostic');assert.equal(diagnostics.length,1);assert.equal(out.narrative.focus_listing_ids.length,5);
 assert(!out.summary.includes('timeout'));
});

test('public state repairs old budget captions and caps old nine-home snapshots while keeping stored state untouched',async()=>{
 const original={session_id:'session',revision:3,messages:[{role:'user',text:f.query}],recommendation:structuredClone(f.recommendation),owner_hash:'private'};
 const plan={...f.plan,id:'plan',session_id:'session',status:'SUBMITTED',submitted_operation_id:'op'};
 const tx={async query(sql:string){return {rows:sql.includes('FROM session_domain_state')?[original]:sql.includes('FROM requirement_refinement_plans')?[plan]:sql.includes('FROM recommendation_calls')?[{operation_id:'op'}]:[],rowCount:0};},release(){}};
 const store=new DomainStore({connect:async()=>tx} as any,null as any,null as any),before=JSON.stringify(original),view=await store.state('session');
 assert.equal(view.recommendation!.recommendations.length+view.recommendation!.unverified_candidates.length,5);
 assert.match(view.messages[0].text,/保留原预算上限 3,000 新币/);assert(!view.messages[0].text.includes('不限'));
 assert.equal(JSON.stringify(original),before);assert.equal(recommendationForDisplay(f.recommendation)!.unverified_candidates.length,0);
});
