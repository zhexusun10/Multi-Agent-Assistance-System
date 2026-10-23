import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {planRefinement} from '../src/agents/requirement/refinement/planner.js';
import {compileRefinement} from '../src/agents/requirement/refinement/answers.js';
import {criterion} from '../src/domain/requirements.js';
import type {Requirements,RecommendationResponse} from '../src/contracts/recommendation-v1.js';
import type {RefinementQuestion} from '../src/contracts/requirement-refinement.js';

const empty:Requirements={listing_type:'RENT',criteria:[]};
function rec(n=8):RecommendationResponse{return {status:'success',recommendations:Array.from({length:n},(_,i)=>({listing:{listing_id:String(i+1),price:1200+i*100,bedrooms:i%3+1,floor_area_sqft:200+i*100,property_type:i%2?'Apartment':'HDB Flat'},criterion_results:[]})),unverified_candidates:[]} as unknown as RecommendationResponse;}
const ids=(r:Requirements,x=rec(),answered=new Set<string>())=>planRefinement(r,x,answered).map(q=>q.id);
const core={...empty,criteria:[criterion('price','lte',3000,'SGD/month'),criterion('bedrooms','gte',1,'count')]};
const submit=(questions:RefinementQuestion[],answers:unknown[],base:Requirements=empty)=>compileRefinement(questions,{submission_id:randomUUID(),answers},base);

test('mock planner gives at most three useful questions and excludes unsupported fields',()=>{
  assert.deepEqual(ids(empty),['budget','bedrooms','property_type']);
  const q=planRefinement(core,rec());assert.deepEqual(q.map(q=>q.id),['property_type','area','ideal_rent']);
  assert(!q.some(q=>/卫浴|通勤|家具|产权/.test(q.title)));assert.match(q.find(q=>q.id==='area')!.help!,/不代表单独卧室/);
});
test('75% valid coverage is required, with real differences rather than just populated fields',()=>{
  const base={...core,criteria:[...core.criteria,criterion('floor_area_sqft','gte',100,'sqft')]};
  const x=rec();x.recommendations[6].listing.property_type=null;x.recommendations[7].listing.property_type=null;
  assert(ids(base,x).includes('property_type'));
  x.recommendations[5].listing.property_type=null;assert(!ids(base,x).includes('property_type'));
  for(const c of x.recommendations)c.listing.property_type='Apartment';assert(!ids(base,x).includes('property_type'));
  for(const c of x.recommendations)c.listing.floor_area_sqft=300;assert(!ids(core,x).includes('area'));
});
test('unknown data stays unknown and answered or skipped questions are exhausted without looping',()=>{
  const x=rec();for(const c of x.recommendations){c.listing.floor_area_sqft=0;c.listing.property_type=null;c.listing.bedrooms=0;}
  assert.deepEqual(ids(core,x),['ideal_rent']);
  assert.deepEqual(ids(empty,rec(),new Set(['budget','bedrooms','property_type','area'])),[]);
  assert.deepEqual(ids(core,rec(),new Set(['property_type','area','ideal_rent'])),[]);
  assert.deepEqual(ids(empty,{...rec(),status:'error'}),[]);
});
test('explicit room requests do not mistake whole-listing area for bedroom area',()=>{
  const rooms={...core,criteria:[...core.criteria,criterion('rental_scope','eq','room')]};
  assert(!ids(rooms).includes('area'));
});
test('successive mock rounds offer remaining questions and terminate without repeating handled fields',()=>{
  let requirements=empty;const handled=new Set<string>(),rounds:string[][]=[];
  for(let n=0;n<4;n++){
    const questions=planRefinement(requirements,rec(),handled);if(!questions.length)break;
    rounds.push(questions.map(q=>q.id));
    const answers=questions.map(q=>q.kind==='budget'?{question_id:q.id,action:'set',max:2000}:q.kind==='bedrooms'?{question_id:q.id,action:'set',count:1,operator:'gte',strength:'hard'}:q.kind==='property_type'?{question_id:q.id,action:'set',values:['Apartment'],strength:'soft'}:q.kind==='area'?{question_id:q.id,action:'set',min:20,unit:'sqm',strength:'soft'}:{question_id:q.id,action:'set',max:1500});
    requirements=submit(questions,answers,requirements).requirements;questions.forEach(q=>handled.add(q.id));
  }
  assert.deepEqual(rounds,[['budget','bedrooms','property_type'],['area','ideal_rent']]);
  assert.equal(planRefinement(requirements,rec(),handled).length,0);
});
test('existing destination preference is not asked again; a new closer preference has no invented cutoff',()=>{
  const near=criterion('place_proximity','lte',5000,'m',false,{target:{place_id:'OSM:1',name:'NUS'},distance_type:'straight_line',distance_preference:'bounded'});
  const base={...core,criteria:[...core.criteria,near]},x=rec();for(const [i,c] of x.recommendations.entries())c.criterion_results=[{criterion_id:near.criterion_id,status:'pass',observed_value:500+i*100,evidence_ids:[]}];
  const done=new Set(['property_type','area','ideal_rent']),q=planRefinement(base,x,done);assert.equal(q.length,1);assert.equal(q[0].kind,'closer');
  const r=submit(q,[{question_id:q[0].id,action:'set'}],base);assert.equal(r.requirements.criteria.find(c=>c.strength==='soft')!.distance_preference,'nearer');
  assert.equal(r.requirements.criteria.find(c=>c.strength==='hard'&&c.field==='place_proximity')!.value,5000);
  assert.equal(planRefinement(r.requirements,x,done).length,0);
});
test('structured choices preserve old hard conditions and distinguish exact/minimum and soft goals',()=>{
  const questions:RefinementQuestion[]=[{id:'bedrooms',kind:'bedrooms',title:'卧室'},{id:'ideal_rent',kind:'ideal_rent',title:'理想月租',upper_bound:3000}];
  const base={...empty,criteria:[criterion('price','lte',3000,'SGD/month'),criterion('district','eq','D05')]},before=structuredClone(base);
  const result=submit(questions,[{question_id:'bedrooms',action:'set',count:2,operator:'gte',strength:'hard'},{question_id:'ideal_rent',action:'set',max:2500}],base);
  assert(result.requirements.criteria.some(c=>c.field==='price'&&c.strength==='hard'&&c.value===3000));
  assert(result.requirements.criteria.some(c=>c.field==='price'&&c.strength==='soft'&&c.value===2500));
  assert(result.requirements.criteria.some(c=>c.field==='bedrooms'&&c.operator==='gte'&&c.value===2));
  assert(result.requirements.criteria.some(c=>c.field==='district'&&c.value==='D05'));assert.deepEqual(base,before);
});
test('area units are converted deterministically and any/skip create no false or negative constraints',()=>{
  const q:RefinementQuestion[]=[{id:'area',kind:'area',title:'面积'}];
  const r=submit(q,[{question_id:'area',action:'set',min:30,unit:'sqm',strength:'soft'}]);
  assert(Math.abs(Number(r.draft.upsert[0].value)-322.917313)<.00001);assert.equal(r.draft.upsert[0].strength,'soft');
  for(const action of ['any','skip'])assert.deepEqual(submit(q,[{question_id:'area',action}],core).requirements.criteria.map(c=>c.criterion_id).sort(),core.criteria.map(c=>c.criterion_id).sort());
  assert.equal(submit(q,[]).answers[0].action,'skip');
});
test('forged, duplicate, inverted, incomplete and out-of-plan answers are rejected before recommendation',()=>{
  const questions:RefinementQuestion[]=[{id:'budget',kind:'budget',title:'预算'},{id:'area',kind:'area',title:'面积'},{id:'property_type',kind:'property_type',title:'类型',options:[{value:'Apartment',label:'公寓'}]}];
  for(const answers of [
    [{question_id:'private_bathroom',action:'set'}],
    [{question_id:'budget',action:'skip'},{question_id:'budget',action:'skip'}],
    [{question_id:'budget',action:'set',min:3000,max:2000}],
    [{question_id:'budget',action:'any',max:3000}],
    [{question_id:'area',action:'set',min:30,unit:'sqm'}],
    [{question_id:'property_type',action:'set',values:['invented'],strength:'hard'}],
    [{question_id:'budget',action:'set',max:2000,strength:'soft'}],
  ])assert.throws(()=>submit(questions,answers));
  assert.throws(()=>submit([{id:'ideal_rent',kind:'ideal_rent',title:'理想月租',upper_bound:2000}],[{question_id:'ideal_rent',action:'set',max:2500}]));
});
