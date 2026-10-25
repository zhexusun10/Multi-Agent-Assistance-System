import test from 'node:test';
import assert from 'node:assert/strict';
import {explanationFixture} from '../evaluation/explanation-cases.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {recoverExplanation} from '../src/agents/explanation/recovery.js';

function setup(){
  const f=explanationFixture('distance');
  for(const c of f.req.criteria.filter(c=>['university','closer'].includes(c.criterion_id))){
    c.target={place_id:'OSM:way:54519165',name:'National University of Singapore'};
    if(c.criterion_id==='university')c.value=3000;
  }
  for(const row of f.rec.recommendations)for(const r of row.criterion_results){
    if(['university','closer'].includes(r.criterion_id))r.observed_value=960.6929935301024;
  }
  return f;
}
function replay(f=setup(),question='我要找NUS附近3公里内的房子，预算2500新币每月'){
  return recoverExplanation(addDecisionSupport(makeExplanationBundle(f.req,f.rec,question),f.req,f.rec),f.req,f.rec);
}
const homes=(out:ReturnType<typeof replay>)=>out.paragraphs.filter(p=>p.listing_id);

test('NUS hard radius and soft nearer produce one distance per home with both source conditions retained',()=>{
  const f=setup(),before=JSON.stringify(f),out=replay(f);
  for(const p of homes(out)){
    assert.equal(p.text.match(/961 metres in a straight line from National University of Singapore/gi)?.length,1);
    assert(p.support_refs.includes(`condition:${p.listing_id}:university`));
    assert(p.support_refs.includes(`condition:${p.listing_id}:closer`));
    assert.match(p.text,/Distance to MRT.*bus stop.*lack sufficient data/i);
  }
  assert.deepEqual(out.focus_listing_ids,f.rec.recommendations.map(r=>r.listing.listing_id));
  assert.equal(JSON.stringify(f),before);
});

test('different destinations at the same distance are both described',()=>{
  const f=setup();f.req.criteria.find(c=>c.criterion_id==='closer')!.target={place_id:'OSM:other',name:'Central Library'};
  for(const p of homes(replay(f))){assert.match(p.text,/961 metres in a straight line from National University of Singapore/i);assert.match(p.text,/961 metres in a straight line from Central Library/i);}
});

test('destination identity, not its display alias, determines whether the measurement is repeated',()=>{
  const f=setup();f.req.criteria.find(c=>c.criterion_id==='closer')!.target!.name='NUS';
  for(const p of homes(replay(f)))assert.equal(p.text.match(/961 metres in a straight line/gi)?.length,1);
});

test('different observations for the same destination are not silently discarded',()=>{
  const f=setup();for(const row of f.rec.recommendations)row.criterion_results.find(r=>r.criterion_id==='closer')!.observed_value=1060;
  for(const p of homes(replay(f))){assert.match(p.text,/961 metres in a straight line/i);assert.match(p.text,/1,060 metres in a straight line/i);}
});

test('English recovery also describes the same destination measurement only once',()=>{
  for(const p of homes(replay(setup(),'Find homes within 3 km of NUS.'))){
    assert.equal(p.text.match(/961 metres in a straight line from National University of Singapore/g)?.length,1);
    assert(p.support_refs.includes(`condition:${p.listing_id}:university`));assert(p.support_refs.includes(`condition:${p.listing_id}:closer`));
  }
});

test('duplicate zero-distance observations describe being inside a URA area once',()=>{
  const f=setup();
  for(const c of f.req.criteria.filter(c=>['university','closer'].includes(c.criterion_id)))c.target={place_id:'URA:AREA:CLEMENTI',name:'CLEMENTI'};
  for(const row of f.rec.recommendations)for(const r of row.criterion_results)if(['university','closer'].includes(r.criterion_id))r.observed_value=0;
  for(const p of homes(replay(f)))assert.equal(p.text.match(/within Clementi/gi)?.length,1);
});

test('a known observation never removes an unknown requirement for the same place',()=>{
  const f=setup();
  for(const row of f.rec.recommendations){const r=row.criterion_results.find(r=>r.criterion_id==='university')!;r.status='unknown';r.observed_value=null;}
  for(const p of homes(replay(f))){assert.equal(p.text.match(/961 metres in a straight line/gi)?.length,1);assert.match(p.text,/Distance to National University of Singapore.*lack sufficient data/i);assert(p.support_refs.includes(`condition:${p.listing_id}:university`));}
});
