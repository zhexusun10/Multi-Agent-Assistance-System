import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {explanationModelInput} from '../src/agents/explanation/model-input.js';
import {recoverExplanation} from '../src/agents/explanation/recovery.js';
import {describeCohort} from '../src/agents/explanation/cohort.js';
import {validateExplanation} from '../src/agents/explanation/validation.js';

const cases=JSON.parse(readFileSync(new URL('./fixtures/explanation-style-regression.json',import.meta.url),'utf8'));
const bundle=(f:any)=>addDecisionSupport(makeExplanationBundle(f.requirements,f.recommendation,f.query),f.requirements,f.recommendation,f.enrichment);

test('the real NUS case has one shared distance comparison with correctly tied nearest homes',()=>{
  const f=cases[0],before=JSON.stringify(f),b=bundle(f),cohorts=b.facts.filter(f=>f.cohort?.field==='place_proximity');
  assert.equal(cohorts.length,5);
  const first=cohorts.find(f=>f.subject_listing_id==='500281070')!,second=cohorts.find(f=>f.subject_listing_id==='60094463')!;
  assert.equal(first.cohort!.position,'tied_lowest');assert.equal(second.cohort!.position,'tied_lowest');
  assert(first.derived_from!.includes('condition:500281070:place_proximity:OSM:way:54519165'));
  assert(first.derived_from!.includes('condition:500281070:place_proximity:OSM:way:54519165-preference'));
  const packed=explanationModelInput(b);assert.equal(packed.input.facts.filter((f:any)=>f.cohort?.field==='place_proximity').length,5);
  assert.equal(JSON.stringify(f),before);
});

test('Clementi MRT details may record bathrooms while native conditions remain unknown',()=>{
  const f=cases[1],b=bundle(f),first=b.decision_support!.profiles[0],id=first.listing_id;
  const bathroom=f.requirements.criteria.find((c:any)=>c.field==='bathrooms');assert(first.caution_refs.includes(`condition:${id}:${bathroom.criterion_id}`));
  const detail=b.facts.find(f=>f.ref===`profile:${id}:bathrooms`)!;assert.equal(detail.numbers[0],2);
  assert.match(detail.text,/upgrade an unverified recommendation condition/i);
  assert(b.facts.some(f=>f.cohort?.field==='place_proximity'&&f.subject_listing_id===id&&f.cohort.position==='lowest'));
});

test('construction years remain exact even though measured distances allow rounding',()=>{
  const f=cases[0],b=bundle(f),out=recoverExplanation(b,f.requirements,f.recommendation),p=out.paragraphs.find(p=>p.listing_id==='500228649')!;
  p.support_refs.push('profile:500228649:build_year');p.text+='这栋楼1975年建成。';validateExplanation(out,b);
  p.text=p.text.replace('1975年建成','1978年建成');assert.throws(()=>validateExplanation(out,b),/construction year/i);
});

test('mixed rent, area and distance positions cannot all be described as middle',()=>{
  const f=cases[1],b=bundle(f),out=recoverExplanation(b,f.requirements,f.recommendation),p=out.paragraphs.find(p=>p.listing_id==='500215736')!;
  const profile=b.decision_support!.profiles.find(x=>x.listing_id===p.listing_id)!;p.support_refs.push(...profile.highlight_refs);
  p.text+='房源面积721平方英尺、月租1,450新币、直线约873米，都处在中间位置。';
  assert.throws(()=>validateExplanation(out,b),/Monthly rent has cohort position/);
  p.text=p.text.replace('都处在中间位置','面积和距离都处在中间位置，月租则并列最高');validateExplanation(out,b);
});

test('saved cases recover with several useful amenities and integrated price/space tradeoffs, without duplicated distance or source mutation',()=>{
  for(const f of cases){
    const before=JSON.stringify(f),b=bundle(f),out=recoverExplanation(b,f.requirements,f.recommendation);
    for(const p of out.paragraphs.filter(p=>p.listing_id)){
      assert.equal(p.text.match(/metres in a straight line from (?:National University of Singapore|CLEMENTI MRT STATION)/gi)?.length,1);
      assert(p.support_refs.filter(r=>r.includes(':nearby:')).length>=2);
      assert(!/straight line[^.]+[.]It is[^.]+straight line/i.test(p.text));
    }
    assert(out.paragraphs.some(p=>/leaving more of your budget|space you need/i.test(p.text)));
    assert.equal(JSON.stringify(f),before);
  }
});

test('legitimate zero area distances are comparable, without treating zero rent/area placeholders as facts',()=>{
  const observations=[{listing_id:'a',value:0,ref:'a'},{listing_id:'b',value:0,ref:'b'},{listing_id:'c',value:250,ref:'c'}];
  assert.equal(describeCohort('a',['a','b','c'],observations,{allowZero:true})!.position,'tied_lowest');
  assert.equal(describeCohort('c',['a','b','c'],observations,{allowZero:true})!.position,'highest');
  assert.equal(describeCohort('a',['a','b','c'],observations),null);
});
