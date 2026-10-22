import test from 'node:test';
import assert from 'node:assert/strict';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {explanationFixture} from '../evaluation/explanation-cases.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {explanationOutputSchema} from '../src/agents/explanation/contracts.js';
import {validateExplanation} from '../src/agents/explanation/validation.js';
import {generateExplanation} from '../src/agents/explanation/generator.js';
import {buildExplanation} from '../src/domain/explanation.js';
import {planIntent} from '../src/agents/orchestrator/agent-orchestrator.js';
import {describeAgentFailure} from '../src/domain/errors.js';
const fixture=()=>{const {req,rec}=explanationFixture('distance');return {req,rec,bundle:makeExplanationBundle(req,rec,'解释这几套')};};
const response=()=>({version:'2.0',focus_listing_ids:[],paragraphs:[{text:'这些房子先按你的必须要求筛选，再优先考虑离学校更近的。地铁和公交距离仍需了解，当前是否可租还需要联系确认。',support_refs:['ranking:method','availability:current']}]});
function mock(writer:(n:number)=>unknown,audit:(n:number)=>unknown=()=>({valid:true,issues:[]})){
  let writes=0,audits=0;
  const model={withStructuredOutput:(_s:unknown,o:any)=>({invoke:async()=>o.name==='write_rental_explanation_v2'?writer(++writes):audit(++audits)})} as unknown as BaseChatModel;
  return {model,counts:()=>({writes,audits})};
}
test('explanation intent routes directly to C without search or follow-up',()=>{
  assert.deepEqual(planIntent({intent:'recommendation_explanation',intent_group:'explanation',confidence:.99,requested_tasks:['explain']}),{kind:'explain',agent:'c',deferred_summary:false});
});
test('bundle contains the real ranking factors and unknown transit, without raw scores/counts',()=>{
  const {bundle}=fixture();assert.deepEqual(bundle.ranking.active_criteria,['closer']);
  assert(bundle.facts.some(f=>f.kind==='limitation'&&f.text.includes('MRT或公交站')));
  assert(!JSON.stringify(bundle).includes('21431'));assert(!JSON.stringify(bundle).includes('combined_score'));
});
test('source zero-bedroom placeholder is not emitted as a housing fact',()=>{
  const {bundle}=fixture();assert(!bundle.facts.some(f=>f.ref==='beds:83000001'));
  assert(bundle.facts.some(f=>f.ref==='beds:83000002'));
});

test('requirement overview contains every user condition, merges the same destination, and hides the internal nearer scale',()=>{
  const {bundle}=fixture();assert.equal(bundle.requirements.length,3);
  const location=bundle.requirements.find(c=>c.criterion_ids.includes('university'))!;
  assert.deepEqual(location.criterion_ids,['university','closer']);assert.match(location.label,/5000/);assert.match(location.label,/更近/);
  assert(!location.label.includes('1000'));assert(!bundle.facts.find(f=>f.ref===location.ref)!.numbers.includes(1000));
  assert.equal(bundle.requirements.find(c=>c.criterion_ids.includes('transit'))!.results[0].status,'unknown');
});

test('readable itemized requirements and separate matched facts retain numerical grounding',()=>{
  const {bundle}=fixture();
  validateExplanation({...response(),paragraphs:[
    {text:'您的要求：\n• 月租不超过3,000新币。\n• 学校5公里内，更近优先。',support_refs:['requirement:rent','requirement:university']},
    {text:'符合的部分：\n• 租金记录为1,600至1,900新币，都在预算内。\n• 到学校直线约850至1,600米。',support_refs:['condition:83000001:rent','condition:83000002:rent','condition:83000001:university','condition:83000002:university']},
    ...response().paragraphs,
  ]},bundle);
  for(const phrase of ['待确认选项','没有可比较的排序依据'])assert.throws(()=>validateExplanation({...response(),paragraphs:[{...response().paragraphs[0],text:phrase}]},bundle),/STYLE/);
});

test('bathroom count selects a count-specific explanation example, without importing a private-bathroom requirement',async()=>{
  const {req,rec}=explanationFixture('bath_count'),bundle=makeExplanationBundle(req,rec,'解释这几套');
  assert(bundle.facts.some(f=>f.ref==='bathroom-count-not-privacy'));
  assert(!req.criteria.some(c=>c.field==='amenities'&&c.value==='private_bathroom'));
  const m=mock(()=>({...response(),paragraphs:[{text:'这些房子的卫生间数量还不能确认。目前还无法判断哪套更适合您，现在是否仍可租，需要联系确认。',support_refs:['ranking:method','availability:current']}]}));
  const result=await generateExplanation(req,rec,'test','解释这几套',m.model,'role');
  assert(result.generation.example_ids.includes('bathroom-count-not-privacy'));
  const privateCase=explanationFixture('hard_unknown');assert(!makeExplanationBundle(privateCase.req,privateCase.rec,'解释').facts.some(f=>f.ref==='bathroom-count-not-privacy'));
});
test('hybrid explanation distinguishes stated preferences from actual unsuppressed behavior features',()=>{
  const {req,rec}=explanationFixture('hybrid'),b=makeExplanationBundle(req,rec,'解释');
  assert.deepEqual(b.ranking.behavior_features,['property_type','floor_area_sqft']);
  assert(b.facts.find(f=>f.ref==='ranking:method')!.text.includes('辅助作用'));
});
test('a farther cheaper home ranked first keeps its exact identity and distance direction in the evidence bundle',()=>{
  const f=explanationFixture('multiple'),b=makeExplanationBundle(f.req,f.rec,'Why were these homes selected?');
  const observations=b.requirements.find(r=>r.criterion_ids.includes('university'))!.results;
  assert.deepEqual(observations.map(r=>[r.position,r.title,r.observed_value]),[[1,'Garden Court',1600],[2,'Willow Residence',850]]);
  assert.match(b.facts.find(f=>f.ref==='compare-distance:1:2:university')!.text,/第2套更近，第1套更远/);
});
test('same-score and no-score lists do not imply the first is better',()=>{
  for(const name of ['tie','unscored','stable'] as const){const f=explanationFixture(name),b=makeExplanationBundle(f.req,f.rec,'为什么第一');assert.match(b.facts.find(f=>f.ref==='ranking:method')!.text,/固定|不能.*更|没有.*依据/);}
});
test('strict structured output rejects arbitrary model fields and reordered/nonexistent selection',()=>{
  const {bundle}=fixture();assert(!explanationOutputSchema.safeParse({...response(),score:.9}).success);
  assert.throws(()=>validateExplanation({...response(),focus_listing_ids:['83000002','83000001']},bundle),/FOCUS/);
  assert.throws(()=>validateExplanation({...response(),focus_listing_ids:['99999999']},bundle),/FOCUS/);
});
test('public prose rejects technical diagnostics even if real raw evidence contains them',()=>{
  const {bundle}=fixture();for(const text of ['原生融合匹配分0.2760','Exact criterion comparison','已评估21431套候选','```json','房源83000001'])assert.throws(()=>validateExplanation({...response(),paragraphs:[{...response().paragraphs[0],text}]},bundle),/STYLE/);
});
test('all paragraph references must exist and critical ranking/availability are cited',()=>{
  const {bundle}=fixture();assert.throws(()=>validateExplanation({...response(),paragraphs:[{text:'你好',support_refs:['missing']}]},bundle),/GROUNDING/);
  assert.throws(()=>validateExplanation({...response(),paragraphs:[{text:'你好',support_refs:['ranking:method']}]},bundle),/COVERAGE/);
});
test('numbers must follow cited facts; reasonable meter-to-kilometer rounding is allowed',()=>{
  const {bundle}=fixture(),base={...response(),paragraphs:[...response().paragraphs,{text:'直线约0.85公里。',support_refs:['condition:83000001:university']}]};
  validateExplanation(base,bundle);base.paragraphs.at(-1)!.text='直线550米。';assert.throws(()=>validateExplanation(base,bundle),/GROUNDING/);
});

test('district identifiers are literal evidence, not rent/distance quantities',()=>{
  for(const code of ['D01','D05','D10','D28']){
    const f=explanationFixture('stable');
    f.req.criteria.push({criterion_id:'district',field:'district',operator:'eq',value:code,strength:'hard'});
    for(const row of f.rec.recommendations)row.criterion_results.push({criterion_id:'district',status:'pass',observed_value:code,evidence_ids:[]});
    const b=makeExplanationBundle(f.req,f.rec,'解释这些房源');
    const paragraphs=[...response().paragraphs,{text:`您的要求：邮区${code}。`,support_refs:['requirement:district']}];
    validateExplanation({...response(),paragraphs},b);
    validateExplanation({...response(),paragraphs:[...response().paragraphs,{text:`这套位于${code}邮区。`,support_refs:['condition:83000001:district']}]},b);
    assert.throws(()=>validateExplanation({...response(),paragraphs:[...response().paragraphs,{text:'位于D06邮区。',support_refs:['requirement:district']}]},b),/GROUNDING/);
    assert.throws(()=>validateExplanation({...response(),paragraphs:[...response().paragraphs,{text:`位于${code}邮区。`,support_refs:['price:83000001']}]},b),/GROUNDING/);
    assert.throws(()=>validateExplanation({...response(),paragraphs:[...response().paragraphs,{text:`租金为${Number(code.slice(1))}新币。`,support_refs:['requirement:district']}]},b),/GROUNDING/);
  }
});

test('string lists, postcodes and ISO dates stay exact and scoped to their sources',()=>{
  const f=explanationFixture('stable');
  f.req.criteria.push(
    {criterion_id:'districts',field:'district',operator:'in',value:['D05','D10'],strength:'hard'},
    {criterion_id:'postcode',field:'postal_code',operator:'eq',value:'139409',strength:'hard'},
    {criterion_id:'move-in',field:'move_in_date',operator:'lte',value:'2026-12-01',strength:'hard'},
  );
  const b=makeExplanationBundle(f.req,f.rec,'解释');
  validateExplanation({...response(),paragraphs:[...response().paragraphs,{text:'希望在D05或D10、邮编139409，最晚2026-12-01入住。',support_refs:['requirement:districts','requirement:postcode','requirement:move-in']}]},b);
  for(const text of ['最晚2026年12月1日入住。','最晚2026 年 12 月 1 日入住。','希望2026/12/1入住。','Move in by 1 December 2026.','Move in by December 1, 2026.'])validateExplanation({...response(),paragraphs:[...response().paragraphs,{text,support_refs:['requirement:move-in']}]},b);
  for(const text of ['希望在D11。','邮编139410。','最晚2026-12-02入住。','D050']){
    assert.throws(()=>validateExplanation({...response(),paragraphs:[...response().paragraphs,{text,support_refs:['requirement:districts','requirement:postcode','requirement:move-in']}]},b),/GROUNDING/);
  }
});

test('quantities and layout shorthand are not accidentally treated as district identifiers',()=>{
  const f=explanationFixture('bath_count'),b=makeExplanationBundle(f.req,f.rec,'解释');
  validateExplanation({...response(),paragraphs:[...response().paragraphs,{text:'您要求1B1B，预算3,000新币。',support_refs:['requirement:bedroom-count','requirement:bathroom-count','requirement:rent']}]},b);
  const d=fixture();validateExplanation({...response(),paragraphs:[...response().paragraphs,{text:'学校5km内，直线850m。',support_refs:['condition:83000001:university']}]},d.bundle);
});
test('abbreviated Singapore block numbers remain grounded in the cited listing titles',()=>{
  const f=explanationFixture('numeric_titles'),b=makeExplanationBundle(f.req,f.rec,'比较前两套');
  validateExplanation({...response(),paragraphs:[...response().paragraphs,{text:'941比906离学校更近。',support_refs:['condition:83000001:closer','condition:83000002:closer']}]},b);
});

test('known distances support their real difference, and the related radius is available without leaking the nearer scale',()=>{
  const {bundle}=fixture();
  validateExplanation({...response(),paragraphs:[...response().paragraphs,{text:'在5公里范围内优先更近的，两套直线距离相差750米。',support_refs:['condition:83000001:closer','condition:83000002:closer']}]},bundle);
  assert(!bundle.facts.some(f=>f.kind==='comparison'&&f.derived_from?.some(ref=>ref.endsWith(':transit'))),'Arithmetic comparisons cannot derive values from unknown transport; missing-data summaries may cite the unknown observation.');
});
test('derived comparison references retain their parent requirement radius and the current listing titles',()=>{
  const f=explanationFixture('mixed_groups'),b=makeExplanationBundle(f.req,f.rec,'比较');
  validateExplanation({...response(),paragraphs:[...response().paragraphs,{text:'希望学校5公里内更近优先；Pending Court 1至4目前缺距离资料。',support_refs:['ranking:method','compare-distance:1:2:closer']}]},b);
  const behavior=explanationFixture('behavior'),interest=makeExplanationBundle(behavior.req,behavior.rec,'为什么第一');
  assert.match(interest.facts.find(f=>f.ref==='compare-interest:1:2')!.text,/第1套Willow Residence整体上比第2套Garden Court更贴近/);
  assert(!interest.facts.find(f=>f.ref==='compare-interest:1:2')!.text.includes('0.9'));
  const missing=behavior.rec.native!.scores['83000002'];for(const c of missing.contributions){c.unknown=true;c.similarity=null;}missing.implicit_score=0;
  assert(!makeExplanationBundle(behavior.req,behavior.rec,'为什么第一').facts.some(f=>f.ref==='compare-interest:1:2'),'A missing comparison is never treated as a lower interest match');
});

test('a small straight-line difference cannot become a claimed commute experience, even with a hedge',()=>{
  const {bundle}=fixture();
  for(const text of ['实际步行或通勤感受可能没有明显区别。','The walking experience may be similar.'])assert.throws(()=>validateExplanation({...response(),paragraphs:[{text,support_refs:['ranking:method','availability:current']}]},bundle),/GROUNDING/);
  validateExplanation({...response(),paragraphs:[{text:'不能据此判断实际通勤体验相同。现在是否仍可租，需要联系确认。',support_refs:['ranking:method','availability:current']}]},bundle);
});
test('English questions cannot silently receive a Chinese explanation',()=>{
  const {bundle}=fixture();bundle.question='Why did you recommend these homes?';
  assert.throws(()=>validateExplanation(response(),bundle),/question is in English/);
  validateExplanation({...response(),paragraphs:[{text:'These homes fit the stated requirements. The order favors a shorter straight-line distance to the university. Nearby transport is unconfirmed, and current availability needs checking.',support_refs:['ranking:method','availability:current']}]},bundle);
});
test('every English repair ends with an explicit English language instruction after Chinese feedback',async()=>{
  const {req,rec}=fixture(),inputs:any[][]=[];
  const englishOutput={...response(),paragraphs:[{text:'These homes fit the budget and location limits. The order favors a shorter straight-line distance to the university. Nearby transport and current availability remain unconfirmed.',support_refs:['ranking:method','availability:current']}]};
  const model={withStructuredOutput:(_s:unknown,o:any)=>({invoke:async(messages:any[])=>{
    if(o.name!=='write_rental_explanation_v2')return {valid:true,issues:[]};
    inputs.push(messages);return inputs.length===1?response():englishOutput;
  }})} as unknown as BaseChatModel;
  const result=await generateExplanation(req,rec,'test','Why were these homes selected and ordered this way?',model,'role');
  assert.equal(result.generation.attempts,2);assert.equal(inputs.length,2);
  for(const messages of inputs)assert.match(String(messages.at(-1).content),/OUTPUT LANGUAGE: English only/);
  assert.match(String(inputs[1].at(-2).content),/Rewrite the complete answer in English/);
  assert.equal(result.summary,englishOutput.paragraphs[0].text);
});
test('LLM text is the actual user summary and does not mutate recommendations',async()=>{
  const {req,rec}=fixture(),before=structuredClone(rec),m=mock(response);
  const out=await generateExplanation(req,rec,'test','解释',m.model,'role');assert.equal(out.summary,response().paragraphs[0].text);assert.equal(out.generation.mode,'llm_grounded');assert.deepEqual(rec,before);assert.deepEqual(m.counts(),{writes:1,audits:1});
});
test('invalid references trigger a fresh model answer instead of silent deterministic fallback',async()=>{
  const {req,rec}=fixture(),m=mock(n=>n===1?{...response(),paragraphs:[{text:'你好',support_refs:['fake']}]}:response());
  const out=await generateExplanation(req,rec,'test','解释',m.model,'role');assert.equal(out.generation.attempts,2);assert.deepEqual(m.counts(),{writes:2,audits:1});
});
test('independent semantic review returns false ranking attribution for bounded repair',async()=>{
  const {req,rec}=fixture(),m=mock(response,n=>n===1?{valid:false,issues:[{code:'ranking',detail:'不能说按价格排序'}]}:{valid:true,issues:[]});
  const out=await generateExplanation(req,rec,'test','解释',m.model,'role');assert.equal(out.generation.attempts,2);assert.equal(out.generation.repair_notes.length,1);
});
test('repeated invalid prose stops after three attempts',async()=>{
  const {req,rec}=fixture(),m=mock(()=>({...response(),version:'wrong'}));await assert.rejects(generateExplanation(req,rec,'test','解释',m.model,'role'),/EXPLANATION_GENERATION_FAILED/);assert.equal(m.counts().writes,3);
});
test('provider and missing-model failures cannot dump the technical report as a fallback',async()=>{
  const {req,rec}=fixture();await assert.rejects(generateExplanation(req,rec,'test','解释',undefined,'role'),/MODEL_REQUIRED/);
  const m=mock(()=>{throw Error('401 unauthorized');});await assert.rejects(generateExplanation(req,rec,'test','解释',m.model,'role'),/401/);assert.equal(m.counts().writes,1);assert(!describeAgentFailure('c','401').message.includes('401'));
});
test('archived native rationale is accepted only for the same request and conversation',()=>{
  const {req,rec}=fixture();assert.throws(()=>makeExplanationBundle(req,rec,'解释',{response:{request_id:'other'}}));
});
test('evidence/document builder no longer contains the raw diagnostic chat branch',()=>{
  const {req,rec}=fixture(),e=buildExplanation(req,rec,'test','解释这几套');assert(!e.summary.includes('已评估'));assert(!e.summary.includes('原生融合匹配分'));assert(e.verified[0].native_scores);
});
