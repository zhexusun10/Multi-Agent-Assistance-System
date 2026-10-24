import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {requirementsSchema,recommendationResponseSchema} from '../src/contracts/recommendation-v1.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {explanationModelInput,translateExplanationRefs} from '../src/agents/explanation/model-input.js';
import {explainBundle} from '../src/agents/explanation/generator.js';
import {validateExplanation} from '../src/agents/explanation/validation.js';
const source=JSON.parse(readFileSync(new URL('../evaluation/inputs/explanation-update-ranking.json',import.meta.url),'utf8'));
const fixture=(knownPending=false)=>{
  const rec=recommendationResponseSchema.parse(source.recommendation);
  // Isolate the historical known-distance pending case within today's five-home scope.
  if(knownPending)rec.unverified_candidates=rec.unverified_candidates.slice(-1);
  return makeExplanationBundle(requirementsSchema.parse(source.requirements),rec,source.query);
};

test('the actual NUS update separates ranking eligibility from known distance and hard budget',()=>{
  const b=fixture(true),extra=b.listings.find(l=>l.title==='345 Clementi Avenue 5')!;
  assert.equal(extra.ranking_status,'not_ranked');assert(!b.ranking.ranked_listing_ids.includes(extra.listing_id));
  assert(b.requirements.some(r=>r.results.some(x=>x.listing_id===extra.listing_id&&typeof x.observed_value==='number'&&x.unit==='m')));
  assert(b.ranking.filter_only_criteria.includes('monthly-rent'));assert(!b.ranking.active_criteria.includes('monthly-rent'));
  const school=b.requirements.find(r=>r.label.includes('新加坡国立大学'))!;assert.equal(school.comparison_only,true);assert(!school.label.includes('1000'));
  const comparison=b.facts.find(f=>f.ref==='compare-ranking:1:2')!;
  assert.match(comparison.text,/新加坡国立大学.*此项更有利于第2套/);assert.match(comparison.text,/超市.*此项更有利于第1套/);
  assert(!b.facts.some(f=>f.ref.startsWith('compare-ranking:')&&f.listing_ids.includes(extra.listing_id)));
  const missing=b.facts.find(f=>f.ref==='coverage:missing')!;
  assert(missing.derived_from?.includes('condition:'+extra.listing_id+':minimum-bedrooms'));
  assert(!missing.derived_from?.includes('condition:'+extra.listing_id+':place_proximity:Supermarket-preference'));
});

test('compact prompts retain every condition observation, reversible citations and immutable source',()=>{
  const b=fixture(),before=structuredClone(b),p=explanationModelInput(b);
  assert.deepEqual(b,before);assert(p.prompt_characters<24000);assert(p.prompt_characters<p.source_characters*.6);
  assert(!JSON.stringify(p.input).includes('source_refs'));
  for(let i=0;i<b.requirements.length;i++)for(let j=0;j<b.requirements[i].results.length;j++){
    const a=b.requirements[i].results[j],v=p.input.requirements[i].results[j];
    assert.equal(a.status,v.status);assert.deepEqual(a.observed_value,v.observed_value);assert.equal(a.listing_id,v.listing_id);
    assert.equal(a.unit,p.input.requirements[i].unit);
    assert.deepEqual(v.fact_refs.map((r:string)=>p.toInternal.get(r)),a.fact_refs);
  }
  const draft={version:'2.0',focus_listing_ids:[],paragraphs:[{text:'同时考虑学校和超市的距离，价格只是预算门槛；部分房源还缺卧室资料。现在价格和可租情况需确认。',support_refs:p.input.required_refs}]};
  validateExplanation(translateExplanationRefs(draft,p.toInternal),b);
  assert.throws(()=>validateExplanation(translateExplanationRefs({...draft,paragraphs:[{text:'到学校直线999999米。',support_refs:p.input.required_refs}]},p.toInternal),b),/GROUNDING/);
  const focus=explanationModelInput({...b,question:'比较第4套和第5套'});
  assert(focus.input.facts.some((f:any)=>focus.toInternal.get(f.ref)==='compare:4:5'));
  assert(!focus.input.facts.some((f:any)=>focus.toInternal.get(f.ref)==='compare:4:8'));
});

test('writer and semantic reviewer receive the same ranking scope and short citation map',async()=>{
  const b=fixture(),messages:any[]=[];
  const model={withStructuredOutput:(_schema:unknown,o:any)=>({invoke:async(m:any[])=>{
    messages.push({name:o.name,m});
    if(o.name==='audit_rental_explanation_v2')return {valid:true,issues:[]};
    const input=JSON.parse(m.findLast((x:any)=>x._getType()==='human').content);
    return {version:'2.0',focus_listing_ids:[],paragraphs:[{text:'这次同时考虑学校和超市的距离，允许两项之间有所取舍。部分房源还缺卧室资料，未进入本次排名；当前价格和可租情况需确认。',support_refs:input.required_refs}]};
  }})} as unknown as BaseChatModel;
  const result=await explainBundle(b,model,'evidence contract');assert.equal(messages.length,2);
  const audit=JSON.parse(messages[1].m[1].content);assert.deepEqual(audit.bundle.ranking.ranked_listing_ids,b.ranking.ranked_listing_ids);
  assert(audit.output.paragraphs[0].support_refs.every((r:string)=>/^f\d+$/.test(r)));
  assert(result.output.paragraphs[0].support_refs.includes('ranking:method'));
  assert(result.metadata.example_ids.includes('distance-tradeoff-with-unranked'));
});
