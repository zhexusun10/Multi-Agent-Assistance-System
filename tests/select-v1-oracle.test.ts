import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {selectV1Violations,scoreOrder,linearPercentile,singleBrowsingReferenceScores,oracleRow,oracleSettings,type SelectV1Native} from './helpers/select-v1-oracle.js';

// The live tests (native-recommendation-live, native-geography-live, advanced-live) cannot run in the generic
// gate. Their shared select-v1 oracle is checked here offline: it must accept the committed ten-results
// envelope against an order O computed independently from recommendation/examples/listings.json, and reject
// every way an exact-prefix or out-of-window answer can violate select-v1.
const read=async(path:string)=>JSON.parse(await readFile(path,'utf8'));

async function fixture(){
  const ten=await read('tests/fixtures/recommendation-v2/ten-results.json'),listings=await read('recommendation/examples/listings.json');
  const native=ten.envelope.response,soft=ten.native_body.requirements.criteria[0];
  assert.equal(soft.field,'district_code');assert.equal(soft.strength,'soft');
  // Independent fused-v2 score with one soft threshold criterion and no feedback: soft_weight x (district in values).
  const softWeight=native.recommendations[0].scores.soft_weight;
  const scores=new Map<string,number>(listings.map((l:any)=>[l.listing_id,softWeight*Number(soft.value.includes(l.district_code))]));
  return {native,scores,order:scoreOrder(scores),limit:ten.native_body.result_limit as number};
}
const clone=<T>(x:T):T=>JSON.parse(JSON.stringify(x));

test('select-v1 oracle accepts the committed ten-results envelope against an independently computed O',async()=>{
  const {native,scores,order,limit}=await fixture();
  assert.equal(native.selection.mode,'cold_start_exploration');
  assert.deepEqual(selectV1Violations(native,order,limit,scores),[]);
});

test('select-v1 oracle rejects exact-prefix assumptions broken by the response: wrong leaders, out-of-window draws, bad ranks, duplicates',async()=>{
  const {native,scores,order,limit}=await fixture();
  const swapped=clone(native);[swapped.recommendations[0],swapped.recommendations[1]]=[swapped.recommendations[1],swapped.recommendations[0]];swapped.recommendations.forEach((c:any,i:number)=>c.rank=i+1);
  assert(selectV1Violations(swapped,order,limit,scores).some(v=>v.includes('leaders')||v.includes('O order')));
  const outside=clone(native);outside.recommendations.at(-1).listing.listing_id='fixture-12';outside.recommendations.at(-1).score_rank=12;outside.selection.exploration.candidate_count=5;
  assert(selectV1Violations(outside,order,limit,scores).some(v=>v.includes('drawn items must come from')));
  const rank=clone(native);rank.recommendations[3].score_rank=40;
  assert(selectV1Violations(rank,order,limit,scores).some(v=>v.includes('score_rank')));
  const dup=clone(native);dup.recommendations[9].listing.listing_id=dup.recommendations[8].listing.listing_id;
  assert(selectV1Violations(dup,order,limit,scores).some(v=>v.includes('duplicate')));
  const reason=clone(native);reason.recommendations[7].selection_reason='tie_group_sample';
  assert(selectV1Violations(reason,order,limit,scores).some(v=>v.includes('reasons')));
  const leaders=clone(native);leaders.selection.leaders=4;
  assert(selectV1Violations(leaders,order,limit,scores).some(v=>v.includes('tie-group start')||v.includes('reasons')));
  assert(selectV1Violations(native,order,9,scores).some(v=>v.includes('result_limit')));
  assert(selectV1Violations({...native,selection:{...native.selection,algorithm_version:'ranked-prefix-v1'}},order,limit,scores).some(v=>v.includes('algorithm_version')));
});

test('select-v1 oracle: ranked, near_tie_sampling, small_pool and empty modes',()=>{
  const item=(id:string,rank:number,score_rank:number,selection_reason:string)=>({listing:{listing_id:id},rank,score_rank,selection_reason});
  const order=['a','b','c','d','e','f'],selection=(mode:string,leaders:number,size:number)=>({mode,algorithm_version:'select-v1',result_limit:3,leaders,tie_group:{size,epsilon:0.01},exploration:null});
  const ranked:SelectV1Native={recommendations:[item('a',1,1,'ranked'),item('b',2,2,'ranked'),item('c',3,3,'ranked')],selection:selection('ranked',2,1)};
  assert.deepEqual(selectV1Violations(ranked,order,3),[]);
  assert(selectV1Violations({...ranked,recommendations:[item('a',1,1,'ranked'),item('b',2,2,'ranked'),item('d',3,4,'ranked')]},order,3).length>0);
  const tie:SelectV1Native={recommendations:[item('a',1,1,'leader'),item('c',2,3,'tie_group_sample'),item('e',3,5,'tie_group_sample')],selection:selection('near_tie_sampling',1,4)};
  assert.deepEqual(selectV1Violations(tie,order,3),[]);
  assert(selectV1Violations({...tie,recommendations:[item('a',1,1,'leader'),item('c',2,3,'tie_group_sample'),item('f',3,6,'tie_group_sample')]},order,3).some(v=>v.includes('drawn items')));
  const scores=new Map([['a',0.9],['b',0.5],['c',0.5],['d',0.5],['e',0.5],['f',0.1]]);
  assert.deepEqual(selectV1Violations(tie,order,3,scores),[]);
  assert(selectV1Violations({...tie,selection:selection('near_tie_sampling',1,5)},order,3,scores).some(v=>v.includes('tie group end')));
  const small:SelectV1Native={recommendations:order.map((id,i)=>item(id,i+1,i+1,'small_pool')),selection:{...selection('small_pool',0,1),result_limit:10,tie_group:null}};
  assert.deepEqual(selectV1Violations(small,order,10),[]);
  assert(selectV1Violations({...small,recommendations:small.recommendations.slice(0,5)},order,10).length>0);
  assert.deepEqual(selectV1Violations({recommendations:[],selection:{...selection('empty',0,1),tie_group:null}},[],3),[]);
});

test('scoreOrder sorts by score then code-point listing_id; linearPercentile matches numpy linear',()=>{
  assert.deepEqual(scoreOrder(new Map([['10',0.5],['9',0.5],['2',0.7]])),['2','10','9']);
  assert.equal(linearPercentile([1,2,3,4],50),2.5);assert.equal(linearPercentile([5,1,NaN,3],5),1.2);assert.equal(linearPercentile([10,20,30,40,50],95),48);
  assert(Number.isNaN(linearPercentile([NaN],50)));
});

test('fused-v2 single-reference oracle reproduces the native scores of the committed single-reference fixture (self pair included, duration cancels, interest gate)',async()=>{
  const fx=await read('tests/fixtures/recommendation-v2/single-reference.json'),listings=(await read('recommendation/examples/listings.json')).map(oracleRow);
  // behavior_context (main contract): only interest_eligible summaries with positive cumulative seconds are references.
  const native=fx.response,usable=fx.native_body.behavior_context.listing_view_durations.filter((v:any)=>v.interest_eligible&&v.duration_seconds>0);
  assert.equal(usable.length,1);const reference=usable[0].listing_id;
  // Hard budget <= 6000 keeps all 12 fixture listings; hard conditions no longer suppress price.
  const pool=listings.filter((r:any)=>r.price!==null&&r.price<=fx.native_body.requirements.criteria[0].value);assert.equal(pool.length,native.pool.size);
  const scores=singleBrowsingReferenceScores(pool,reference,fx.oracle_settings,[]);
  for(const c of native.recommendations){
    assert(Math.abs(c.scores.combined_score-scores.get(c.listing.listing_id)!)<1e-9,`${c.listing.listing_id}: native ${c.scores.combined_score} vs oracle ${scores.get(c.listing.listing_id)}`);
    assert.deepEqual(c.scores.suppressed_features,[]);
    if(c.listing.listing_id===reference)assert.equal(c.scores.browsing_score,1);
  }
  assert.equal(scores.get(reference),fx.oracle_settings.behavior_weight*fx.oracle_settings.browsing_weight,'identity similarity 1');
  assert.deepEqual(selectV1Violations(native,scoreOrder(scores),fx.native_body.result_limit,scores),[]);
  // The oracle settings embedded in the fixture are the shape oracleSettings() reads from the committed YAML.
  assert.deepEqual(Object.keys(oracleSettings(await readFile('recommendation/settings/recommendation.yaml','utf8'))).sort(),Object.keys(fx.oracle_settings).sort());
});
