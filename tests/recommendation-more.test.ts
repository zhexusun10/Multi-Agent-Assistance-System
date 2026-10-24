import test from 'node:test';
import assert from 'node:assert/strict';
import {moreFixture} from './helpers/recommendation-more.js';
import {withPresentation,nextRecommendationBatch,displayedCandidates,recommendationForDisplay} from '../src/shared/recommendation-display.js';
import {resolveListingSelection} from '../src/domain/selection.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {recoverExplanation} from '../src/agents/explanation/recovery.js';
import {generateExplanation} from '../src/agents/explanation/generator.js';
import {IntentRecognizer} from '../src/agents/orchestrator/intent-recognizer.js';
import {planIntent} from '../src/agents/orchestrator/agent-orchestrator.js';

test('five then ten preserves the first batch, original order and all data, including after public projection',()=>{
  const {rec}=moreFixture(),original=JSON.stringify(rec),initial=withPresentation(rec),before=displayedCandidates(initial).map(c=>c.listing.listing_id),next=nextRecommendationBatch(initial);
  assert.equal(before.length,5);assert.equal(next.listing_ids.length,5);
  assert.equal(displayedCandidates(next.recommendation).length,10);
  assert.deepEqual(displayedCandidates(next.recommendation).slice(0,5).map(c=>c.listing.listing_id),before);
  assert.equal(new Set(displayedCandidates(next.recommendation).map(c=>c.listing.listing_id)).size,10);
  assert.equal(displayedCandidates(recommendationForDisplay(next.recommendation)).length,10);
  assert.equal(JSON.stringify(rec),original);
  assert.deepEqual(nextRecommendationBatch(next.recommendation).listing_ids,[]);
  assert.equal(resolveListingSelection(next.recommendation,'property 10').listing.listing_id,rec.recommendations[9].listing.listing_id);
  assert.throws(()=>resolveListingSelection(initial,'property 6'));
});

test('legacy defaults stay five, shortages are not padded, and unknown-condition homes keep their status',()=>{
  const {rec}=moreFixture(7);assert.equal(displayedCandidates(rec).length,5);
  const next=nextRecommendationBatch(withPresentation(rec));assert.equal(next.listing_ids.length,2);
  assert.equal(displayedCandidates(next.recommendation).length,7);
  const mixed=moreFixture().rec;mixed.unverified_candidates=mixed.recommendations.splice(3);
  assert.equal(displayedCandidates(withPresentation(mixed)).length,5);
  const expanded=nextRecommendationBatch(withPresentation(mixed)).recommendation;
  assert.equal(expanded.unverified_candidates.length,7);assert.equal(displayedCandidates(expanded).length,10);
  assert.equal(nextRecommendationBatch(withPresentation(moreFixture(3).rec)).listing_ids.length,0);
});

test('general explanations cover ten visible homes; continuation recovery describes only new homes at positions 6-10',async()=>{
  const {req,rec}=moreFixture(),next=nextRecommendationBatch(withPresentation(rec));
  const bundle=addDecisionSupport(makeExplanationBundle(req,next.recommendation,'Explain these homes'),req,next.recommendation);
  assert.equal(bundle.decision_support!.target_listing_ids.length,10);
  assert.equal(addDecisionSupport(makeExplanationBundle(req,next.recommendation,'property 10'),req,next.recommendation).decision_support!.target_listing_ids.length,1);
  let localAttempts=0;
  const model={withStructuredOutput:()=>({invoke:async()=>{localAttempts++;throw Error('OFFLINE_TEST_WRITER');}})};
  const result=await generateExplanation(req,next.recommendation,'synthetic','继续推荐',model as any,'',undefined,[],undefined,{previous_count:5,listing_ids:next.listing_ids});
  assert.equal(localAttempts,1);assert.equal(result.generation.mode,'evidence_recovery');
  assert.deepEqual(result.narrative.focus_listing_ids,next.listing_ids);
  assert.deepEqual(result.narrative.paragraphs.filter(p=>p.listing_id).map(p=>p.listing_id),next.listing_ids);
  assert(result.summary.includes('6'));assert(result.summary.includes('10'));
  await assert.rejects(()=>generateExplanation(req,next.recommendation,'synthetic','more',model as any,'',undefined,[],undefined,{previous_count:5,listing_ids:next.listing_ids.slice().reverse()}),/CONTINUATION_IDENTITY/);
});

test('LLM continuation contract is contextual, schema constrained and separate from changed requirements',async()=>{
  const cases:[string,string,boolean,string][]=[
    ['再帮我多推荐一些','recommendation_more',true,'more'],['前面推荐的不行，再继续推荐','recommendation_more',true,'more'],['帮我推荐一下吧','recommendation_more',true,'more'],
    ['Show me five more homes','recommendation_more',true,'more'],['These are not suitable. Keep recommending.','recommendation_more',true,'more'],['Recommend some homes','recommendation_more',false,'search'],
    ['再推荐一些，预算改成3500','requirement_update',true,'search'],['More please, but change my budget to 3500','requirement_update',true,'search'],['Why these homes?','recommendation_explanation',true,'explain'],
  ];
  for(const [message,intent,has_recommendation,kind] of cases){
    let prompt='';const model={invoke:async(messages:any[])=>{prompt=String(messages[0].content);return {text:JSON.stringify({intent,confidence:1,requested_tasks:[]})};}};
    const r=await new IntentRecognizer(model as any,async()=>({intent:'other',confidence:0})).recognize(message,[],{has_recommendation});
    assert(prompt.includes('recommendation_more'));assert(prompt.includes('has_recommendation'));
    assert.equal(planIntent(r).kind,kind,message);
  }
});

// Exercise the normal writer + semantic-review path using deterministic local stubs.
test('continuation writer and reviewer receive only the new target IDs and publish a reviewed narrative',async()=>{
  const {req,rec}=moreFixture(),next=nextRecommendationBatch(withPresentation(rec));
  const continuation={previous_count:5,listing_ids:next.listing_ids};
  const bundle=addDecisionSupport(makeExplanationBundle(req,next.recommendation,'继续推荐'),req,next.recommendation);
  Object.assign(bundle.decision_support!,{mode:'guide',explain_order:false,target_listing_ids:next.listing_ids,continuation});
  bundle.required_refs=['availability:current'];
  const narrative=recoverExplanation(bundle,req,next.recommendation),names:string[]=[];
  const model={withStructuredOutput:(_schema:unknown,options:any)=>({invoke:async(messages:any[])=>{
    names.push(options.name);
    if(options.name==='write_rental_explanation_v2'){
      const input=JSON.parse([...messages].reverse().find(m=>m._getType?.()==='human').content);
      assert.deepEqual(input.decision_support.target_listing_ids,next.listing_ids);
      assert.deepEqual(input.decision_support.continuation,continuation);
      return narrative;
    }
    return {valid:true,issues:[]};
  }})};
  const result=await generateExplanation(req,next.recommendation,'synthetic','继续推荐',model as any,'',undefined,[],undefined,continuation);
  assert.deepEqual(names,['write_rental_explanation_v2','audit_rental_explanation_v2']);
  assert.equal(result.generation.mode,'llm_grounded');assert.equal(result.generation.semantic_audit,'passed');
  assert.deepEqual(result.narrative.focus_listing_ids,next.listing_ids);
});
