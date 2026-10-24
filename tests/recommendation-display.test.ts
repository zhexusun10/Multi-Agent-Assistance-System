import test from 'node:test';
import assert from 'node:assert/strict';
import {explanationFixture} from '../evaluation/explanation-cases.js';
import {displayedCandidates,resolveListingSelection} from '../src/domain/selection.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {buildExplanation,renderSummary} from '../src/domain/explanation.js';
import {ExplanationEnricher} from '../src/agents/explanation/enrichment.js';
import {ToolExecutor} from '../src/tools/executor.js';

function fixture(formal=5,pending=4){
  const f=explanationFixture('mixed_groups');delete f.rec.native;
  const make=(template:typeof f.rec.recommendations[0],n:number,offset:number)=>Array.from({length:n},(_,i)=>{
    const c=structuredClone(template);c.rank=i+1;c.listing={...c.listing,listing_id:String(85000001+offset+i),title:'Display Home '+String.fromCharCode(65+offset+i),price:1000+i*100,floor_area_sqft:500+i*100};return c;
  });
  f.rec.recommendations=make(f.rec.recommendations[0],formal,0);
  f.rec.unverified_candidates=make(f.rec.unverified_candidates[0],pending,formal);
  return f;
}

test('five formal plus four pending homes expose exactly five IDs everywhere without mutating the native result',()=>{
  const {req,rec}=fixture(),before=JSON.stringify(rec),visible=displayedCandidates(rec),ids=visible.map(c=>c.listing.listing_id);
  assert.equal(visible.length,5);assert.deepEqual(ids,rec.recommendations.map(c=>c.listing.listing_id));
  const b=addDecisionSupport(makeExplanationBundle(req,rec,'介绍这几套'),req,rec);
  assert.deepEqual(b.listings.map(l=>l.listing_id),ids);assert.deepEqual(b.decision_support!.target_listing_ids,ids);
  assert(b.facts.every(f=>f.listing_ids.every(id=>ids.includes(id))));
  assert(b.facts.filter(f=>f.cohort).every(f=>f.cohort!.scope_count===5));
  const structured=buildExplanation(req,rec,'synthetic'),doc=renderSummary(req,rec,'synthetic');
  assert.equal(structured.verified.length,5);assert.equal(structured.unverified.length,0);
  for(const hidden of rec.unverified_candidates)assert(!doc.markdown.includes(hidden.listing.title));
  assert.equal(resolveListingSelection(rec,'我要看第5套').listing.listing_id,ids[4]);
  assert.throws(()=>resolveListingSelection(rec,'我要看第6套'),/点击房源卡片/);
  assert.throws(()=>resolveListingSelection(rec,'请联系待核实第1套'),/点击房源卡片/);
  assert.throws(()=>resolveListingSelection(rec,'房源ID '+rec.unverified_candidates[0].listing.listing_id),/不在当前列表/);
  assert.equal(JSON.stringify(rec),before);
});

test('pending homes only fill remaining places, retain unknown status, and use global visible numbering',()=>{
  const {req,rec}=fixture(2,4),rows=displayedCandidates(rec);
  assert.equal(rows.length,5);assert.equal(resolveListingSelection(rec,'看第5套').listing.listing_id,rec.unverified_candidates[2].listing.listing_id);
  assert.equal(resolveListingSelection(rec,'待核实第3套').listing.listing_id,rows[4].listing.listing_id);
  assert.throws(()=>resolveListingSelection(rec,'待核实第4套'),/点击房源卡片/);
  const b=makeExplanationBundle(req,rec,'介绍');assert.deepEqual(b.listings.map(l=>l.group),['hard_pass','hard_pass','hard_unknown','hard_unknown','hard_unknown']);
  assert.deepEqual(b.listings.map(l=>l.position),[1,2,3,4,5]);
  assert.equal(displayedCandidates(fixture(0,4).rec).length,4);
  assert.deepEqual(displayedCandidates(null),[]);assert.deepEqual(displayedCandidates({recommendations:[],unverified_candidates:[]}),[]);
});

test('detail enrichment reads only the five visible homes, never the four hidden candidates',async()=>{
  const {rec}=fixture(),read:string[]=[];
  const service=new ExplanationEnricher({async request(path){read.push(path);const id=path.split('/')[4];return path.endsWith('/context')?{listing:{entity_id:'Listing:'+id,label:'Listing',properties:{}},relationships:[]}:{item:{entity_id:'Listing:'+id,label:'Listing',properties:{listing_id:id,bathrooms:2}}};}},new ToolExecutor(),async()=> 'source');
  const extra=await service.load(rec,{sessionId:'session',operationId:'operation',agent:'c',dataset:'synthetic',deadline:Date.now()+60000,calls:0});
  assert.equal(read.length,10);assert.deepEqual(extra.map(e=>e.listing_id),rec.recommendations.map(c=>c.listing.listing_id));
  assert(read.every(path=>rec.recommendations.some(c=>path.split('/')[4]===c.listing.listing_id)));
});
