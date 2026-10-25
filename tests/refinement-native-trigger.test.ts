import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {refinementTrigger} from '../src/agents/requirement/refinement/trigger.js';
import {RequirementRefinementService} from '../src/agents/requirement/refinement/service.js';
import {nativeResponseSchema} from '../src/contracts/recommendation-native.js';
import {explanationFixture} from '../evaluation/explanation-cases.js';
import {refinementNative} from './helpers/refinement-native.js';

function state(active=true,shape:'mpd'|'legacy'='mpd'){
 const f=explanationFixture('stable'),rec=f.rec;
 rec.native={...rec.native!,...refinementNative(active,shape)};
 return {session_id:'test',requirements:f.req,requirements_version:rec.requirements_version,latest_request_id:rec.request_id,active_operation_id:'op',recommendation:rec};
}

test('the exact teammate round-3 file passes contract 2.0 and its active=false suppresses questions',()=>{
 const raw=JSON.parse(readFileSync(new URL('./fixtures/recommendation-v2/teammate-round-3.response.json',import.meta.url),'utf8').replace(/^\uFEFF/,''));
 const native=nativeResponseSchema.parse(raw);assert.equal(native.selection!.cold_start.active,false);
 const cold=native.selection!.cold_start as any;assert.equal(cold.exit_conditions_met,true);assert(!('mean_pairwise_difference' in cold));// stored as is, not read as D
 assert.equal(refinementTrigger(state(false).recommendation),null);
 assert.equal(refinementTrigger(state(false,'legacy').recommendation),null);
 assert.equal(refinementTrigger(state(true,'legacy').recommendation)?.trigger,'native_cold_start');// a stored legacy active record keeps its own flag
});

test('only a valid active native signal can trigger; no threshold or legacy mock approximation',()=>{
 const s=state();assert.equal(refinementTrigger(s.recommendation)?.trigger,'native_cold_start');
 for(const mutate of [(r:any)=>delete r.native,(r:any)=>delete r.native.contract,(r:any)=>delete r.native.selection,(r:any)=>r.native.selection.cold_start.active='true',(r:any)=>r.native.selection.cold_start.evaluated=false,(r:any)=>r.native.pool.pool_key='stale',
   (r:any)=>r.native.selection.cold_start.coverage_c=1,// mixed legacy + mpd-v1 keys
   (r:any)=>r.native.selection.cold_start.mean_pairwise_difference=0.05,// active although D >= threshold
   (r:any)=>r.native.selection.cold_start.detector='gini']){
  const rec=structuredClone(s.recommendation);mutate(rec);assert.equal(refinementTrigger(rec),null);
 }
});

test('card generation saves the native signal once, while inactive responses retire any open card',async()=>{
 for(const active of [true,false]){
  const s=state(active),writes:any[]=[],tx={query:async(sql:string,args:any[])=>{writes.push({sql,args});return {rows:[],rowCount:0};}};
  const service=new RequirementRefinementService({} as any,s.recommendation.state.coverage.dataset_version),id=await service.generate(tx as any,s as any);
  if(active){assert(id);const write=writes.find(w=>w.sql.startsWith('INSERT'));const body=JSON.parse(write.args[5]);assert.equal(body.trigger,'native_cold_start');assert.equal(body.cold_start.active,true);assert(body.questions.length>0);}
  else{assert.equal(id,null);assert(!writes.some(w=>w.sql.startsWith('INSERT')));assert(writes.some(w=>w.sql.includes("status='STALE'")));}
 }
});

test('an active signal cannot force repeated/exhausted questions or adopt a stale source',async()=>{
 const s=state(),service=new RequirementRefinementService({} as any,s.recommendation.state.coverage.dataset_version);
 const history=[{status:'SUBMITTED',source_request_id:'old',body:{questions:['budget','bedrooms','property_type','area','ideal_rent'].map(id=>({id}))}}];
 const tx={query:async(sql:string)=>{assert(sql.startsWith('SELECT'));return {rows:history};}};
 assert.equal(await service.generate(tx as any,s as any),null);
 s.latest_request_id='new';assert.equal(await service.generate(tx as any,s as any),null);
});
