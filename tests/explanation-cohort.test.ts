import test from 'node:test';
import assert from 'node:assert/strict';
import {describeCohort,unitRentAssessment} from '../src/agents/explanation/cohort.js';
import {explanationFixture} from '../evaluation/explanation-cases.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {explanationModelInput} from '../src/agents/explanation/model-input.js';
import {validateExplanation} from '../src/agents/explanation/validation.js';
import {guideResponse} from './helpers/explanation-guide.js';
import type {ExplanationEnrichment} from '../src/agents/explanation/enrichment.js';

const values=(numbers:number[])=>numbers.map((value,i)=>({listing_id:String(i),value,ref:'source:'+i}));
test('a 1500 rent among four 2000-plus homes is the clear minimum, while ties and tiny differences are not exaggerated',()=>{
 const x=values([1500,2300,2400,2600,2700]),scope=x.map(v=>v.listing_id);
 assert.equal(describeCohort('0',scope,x)?.position,'lowest');assert.equal(describeCohort('0',scope,x)?.stands_out,true);
 assert.equal(describeCohort('2',scope,x)?.position,'middle');assert.equal(describeCohort('4',scope,x)?.position,'highest');
 const tied=values([1500,1500,2400,2600,2700]);assert.equal(describeCohort('0',scope,tied)?.position,'tied_lowest');
 const close=values([2000,2010,2020,2030,2040]);assert.equal(describeCohort('0',scope,close)?.clustered,true);assert.equal(describeCohort('0',scope,close)?.stands_out,false);
 assert.equal(describeCohort('0',scope,values([2000,2000,2000,2000,2000]))?.position,'similar');
});
test('unknown and zero placeholders do not establish a complete lowest/largest comparison set',()=>{
 const x=values([400,500,700,0]),scope=['0','1','2','3','4'];
 const r=describeCohort('2',scope,x)!;assert.equal(r.complete,false);assert.equal(r.known_count,3);assert.equal(r.scope_count,5);
 assert.equal(describeCohort('3',scope,x),null);assert.equal(describeCohort('0',['0'],x),null);
 assert.throws(()=>describeCohort('0',scope,[...x,x[0]]),/DUPLICATE/);
});

function fixture(){
 const f=explanationFixture('stable'),template=f.rec.recommendations[0];delete f.rec.native;
 const prices=[1500,2300,2400,2600,2700],areas=[400,600,1000,500,700];
 f.rec.recommendations=prices.map((price,i)=>{
  const row=structuredClone(template);row.rank=i+1;row.listing={...row.listing,listing_id:String(84000001+i),title:'Home '+String.fromCharCode(65+i),price,floor_area_sqft:areas[i]};
  row.criterion_results=row.criterion_results.map(r=>({...r,observed_value:price}));return row;
 });
 const enrichment:ExplanationEnrichment[]=f.rec.recommendations.map(r=>({listing_id:r.listing.listing_id,dataset:'synthetic',source_ref:'detail-'+r.listing.listing_id,details:{...r.listing,rental_scope:'whole_unit',bathrooms:2},nearby:[],sections:{details:'available',nearby:'available'}}));
 return {...f,enrichment};
}
test('group positions and calculated unit rents preserve recommendation order; broad guides do not feed pairwise sales scripts',()=>{
 const {req,rec,enrichment}=fixture(),before=JSON.stringify(rec),bundle=addDecisionSupport(makeExplanationBundle(req,rec,'介绍这几套'),req,rec,enrichment);
 assert.deepEqual(bundle.decision_support!.target_listing_ids,rec.recommendations.map(r=>r.listing.listing_id));
 const cheapest=bundle.facts.find(f=>f.ref==='cohort:84000001:price')!;
 assert.equal(cheapest.cohort?.position,'lowest');assert.equal(cheapest.cohort?.complete,true);assert.equal(cheapest.cohort?.stands_out,true);
 assert.equal(bundle.facts.find(f=>f.ref==='cohort:84000003:floor_area_sqft')?.cohort?.position,'highest');
 assert(!bundle.facts.some(f=>f.ref.startsWith('unit-rent:')));
 const low=bundle.facts.find(f=>f.ref==='cohort:84000003:rent_per_sqft')!;
 assert.equal(low.numbers[0],2.4);assert(low.derived_from?.includes('area:84000003'));assert(low.derived_from?.includes('price:84000003'));
 assert.match(low.text,/最低.*单看空间和月租更划算/);
 assert.match(bundle.facts.find(f=>f.ref==='cohort:84000004:rent_per_sqft')!.text,/最高.*同样的面积.*月租相对高/);
 assert(!bundle.facts.some(f=>f.ref==='cohort:84000001:rent_per_sqft'));
 const packed=explanationModelInput(bundle);
 assert(!packed.input.facts.some((f:any)=>packed.toInternal.get(f.ref)?.startsWith('advantage:')));
 assert(packed.input.facts.some((f:any)=>packed.toInternal.get(f.ref)==='cohort:84000001:price'));
 assert.equal(JSON.stringify(rec),before);
});
test('an unknown or mixed rental scope suppresses the entire unit-rent topic, not just its superlative',()=>{
 const {req,rec,enrichment}=fixture();delete enrichment[0].details!.rental_scope;
 const bundle=addDecisionSupport(makeExplanationBundle(req,rec,'介绍'),req,rec,enrichment);
 assert(!bundle.facts.some(f=>f.ref.startsWith('unit-rent:')));
 assert(!bundle.facts.some(f=>f.ref.endsWith(':rent_per_sqft')));
 enrichment[0].details!.rental_scope='room';
 const mixed=addDecisionSupport(makeExplanationBundle(req,rec,'介绍'),req,rec,enrichment);
 assert(!mixed.facts.some(f=>f.cohort?.field==='rent_per_sqft'));
});

test('similar ratios and the middle of a varied group are omitted; distinct high and low ratios carry meaning',()=>{
 const x=values([2.4,4,4.2,4.3,6]),scope=x.map(v=>v.listing_id);
 assert.equal(unitRentAssessment(describeCohort('0',scope,x)!),'lower');
 assert.equal(unitRentAssessment(describeCohort('4',scope,x)!),'higher');
 assert.equal(unitRentAssessment(describeCohort('2',scope,x)!),null);
 const close=values([4,4.01,4.02,4.03,4.04]);assert.equal(unitRentAssessment(describeCohort('4',scope,close)!),null);
 const {req,rec,enrichment}=fixture();rec.recommendations.forEach((r,i)=>{r.listing.price=[4,4.01,4.02,4.03,4.04][i]*r.listing.floor_area_sqft!;enrichment[i].details!.price=r.listing.price;});
 const bundle=addDecisionSupport(makeExplanationBundle(req,rec,'介绍'),req,rec,enrichment);
 assert(!bundle.facts.some(f=>f.cohort?.field==='rent_per_sqft'));
});

test('zero or missing area never yields a ratio and incomplete comparisons retain the known subset limit',()=>{
 const {req,rec,enrichment}=fixture();rec.recommendations[0].listing.floor_area_sqft=0;enrichment[0].details!.floor_area_sqft=null;
 const b=addDecisionSupport(makeExplanationBundle(req,rec,'介绍'),req,rec,enrichment);
 assert(!b.facts.some(f=>f.ref==='cohort:84000001:rent_per_sqft'));
 const low=b.facts.find(f=>f.ref==='cohort:84000003:rent_per_sqft')!;
 assert.equal(low.cohort?.complete,false);assert.equal(low.cohort?.known_count,4);assert.match(low.text,/有每平方英尺月租资料的4套/);
});

test('unit rent prose requires its own comparative evidence, its practical meaning and correct units',()=>{
 const {req,rec,enrichment}=fixture(),b=addDecisionSupport(makeExplanationBundle(req,rec,'介绍'),req,rec,enrichment),good=guideResponse(req,rec,'介绍');
 good.paragraphs[4].text+='每平方英尺月租约5.2新币，是这几套中最高的，同样面积要付更多月租，单看空间和月租不太划算。';
 good.paragraphs[4].support_refs.push('cohort:84000004:rent_per_sqft');validateExplanation(good,b);
 const bare=structuredClone(good);bare.paragraphs[4].text='第4套 Home D：月租2,600新币，面积500平方英尺，每平方英尺月租约5.2新币，仅供参考。';assert.throws(()=>validateExplanation(bare,b),/单位面积月租必须/);
 const noMeaning=structuredClone(good);noMeaning.paragraphs[4].text='第4套 Home D：月租2,600新币，面积500平方英尺，每平方英尺月租约5.2新币，是本轮最高的。';assert.throws(()=>validateExplanation(noMeaning,b),/用户能理解的取舍/);
 const ungrounded=structuredClone(good);ungrounded.paragraphs[4].support_refs=ungrounded.paragraphs[4].support_refs.filter(r=>!r.endsWith(':rent_per_sqft'));assert.throws(()=>validateExplanation(ungrounded,b),/单位面积月租只在/);
 const wrongUnit=structuredClone(good);wrongUnit.paragraphs[4].text=wrongUnit.paragraphs[4].text.replace('5.2新币','5.2元/米');assert.throws(()=>validateExplanation(wrongUnit,b),/单位为新币/);
 const wrongSubject=structuredClone(good);wrongSubject.paragraphs[1].text+='每平方英尺月租约5.2新币，是这几套中最高的，同样面积要付更多月租。';wrongSubject.paragraphs[1].support_refs.push('cohort:84000004:rent_per_sqft');assert.throws(()=>validateExplanation(wrongSubject,b),/单位面积月租只在/);
});
test('known price and area are required for a broad guide, while a focused answer can address only the asked feature',()=>{
 const {req,rec}=fixture(),bundle=addDecisionSupport(makeExplanationBundle(req,rec,'介绍'),req,rec),good=guideResponse(req,rec,'介绍');
 validateExplanation(good,bundle);
 const missing=structuredClone(good);missing.paragraphs[1].support_refs=['price:84000001'];
 assert.throws(()=>validateExplanation(missing,bundle),/known rent and listing area/);
 const technical=structuredClone(good);technical.paragraphs[1].text+='挂牌标注X站约500米。';assert.throws(()=>validateExplanation(technical,bundle),/STYLE/);
 const focus=addDecisionSupport(makeExplanationBundle(req,rec,'只说第1套价格'),req,rec);
 assert.equal(focus.decision_support!.mode,'focused');
});
