import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { criterion,extractRules,extractRequirements,mergeRequirements,pendingQuestions } from '../src/domain/requirements.js';
import type { BaseChatModel } from '@langchain/core/language_models/chat_models';
import { criterionSchema,recommendationRequestSchema,validateResponse,compare,type Requirements } from '../src/contracts/recommendation-v1.js';
import { observed,classify,geodesic } from '../src/adapters/recommendation.js';
import { ToolExecutor,type ToolContext } from '../src/tools/executor.js';
import { DataHttpAdapter } from '../src/adapters/data-http.js';
import { hash } from '../src/stores/domain.js';
import { renderSummary } from '../src/domain/explanation.js';
import { planIntent } from '../src/domain/intents.js';
import {describeAgentFailure} from '../src/domain/errors.js';

const empty:Requirements={listing_type:'RENT',criteria:[]};
const screenshotQuery='我要找金文泰附近，预算2500每月的房子，要求有独立卫浴，离新国立大学通勤30分钟以内';
test('nearby is a location preference, preserving budget, private bathroom and commute without a guessed hard radius',()=>{
  const d=extractRules(screenshotQuery,empty);
  assert.equal(d.upsert.find(c=>c.field==='price')?.value,2500);
  const commute=d.upsert.find(c=>c.field==='commute_time')!;assert.equal(commute.value,30);assert.equal(commute.unit,'min');assert.equal(commute.target?.name,'新国立大学');assert.equal(commute.transport_mode,'unspecified');
  assert(d.upsert.some(c=>c.field==='amenities'&&c.value==='private_bathroom'));assert(!d.upsert.some(c=>c.field==='bathrooms'||c.field==='place_proximity'));assert.equal(d.clarifications.length,0);assert.equal(d.location_intents?.[0].name,'金文泰');assert.equal(d.location_intents?.[0].strength,'soft');assert.equal(d.location_intents?.[0].radius_m,undefined);
  assert.equal(observed({bathrooms:2},d.upsert.find(c=>c.field==='amenities')!),null);
});
test('unanswered location clarification survives unrelated budget edits and resolves with an explicit district',()=>{
  const pending={...extractRules(screenshotQuery,empty),clarifications:['请明确“金文泰附近”的参照地点、范围（米或公里）。']},base=mergeRequirements(empty,pending);
  const budget=extractRules('预算改为3000新币',base);assert(pendingQuestions(pending,budget).length>0);
  const region=extractRules('位置改为D05，其余已暂存条件保留',base);assert.deepEqual(pendingQuestions(pending,region),[]);
  const final=mergeRequirements(base,region);assert(final.criteria.some(c=>c.field==='amenities'&&c.value==='private_bathroom'));assert(final.criteria.some(c=>c.field==='commute_time'&&c.value===30));
});
test('independent amenities coexist and a private bathroom is not inferred from a bathroom count',()=>{
  const draft=extractRules('独立卫浴，最好有泳池',empty),r=mergeRequirements(empty,draft);assert.equal(r.criteria.filter(c=>c.field==='amenities').length,2);assert.equal(new Set(r.criteria.map(c=>c.criterion_id)).size,r.criteria.length);
  assert(r.criteria.some(c=>c.field==='amenities'&&c.value==='private_bathroom'&&c.strength==='hard'));
});
test('public agent failure does not expose raw Zod or SDK diagnostics',()=>{
  const failure=describeAgentFailure('a','[{"code":"custom","message":"Expected unit m","path":["upsert",0]}]');
  assert.equal(failure.code,'REQUIREMENT_STAGE_FAILED');assert(!failure.message.includes('Expected unit'));assert(!failure.message.includes('upsert'));assert(failure.message.includes('尚未进入房源推荐'));
});
test('unspecified soft weight is normalized by code; invalid explicit weight still fails',async()=>{
  const proposal={upsert:[{criterion_id:'near-supermarket',field:'place_proximity',operator:'lte',value:800,unit:'m',strength:'soft',target:{category:'Supermarket'},distance_type:'straight_line'}],remove_fields:[],clarifications:[],sources:[{field:'place_proximity',quote:'最好超市直线距离800米以内'}],supported:true};
  const mock=(output:unknown)=>({withStructuredOutput:()=>({invoke:async()=>output})}) as unknown as BaseChatModel;
  const d=await extractRequirements('最好超市直线距离800米以内',empty,mock(proposal));assert.equal(d.upsert[0].weight,1);
  const wrongStrength=await extractRequirements('最好超市直线距离800米以内',empty,mock({...proposal,upsert:[{...proposal.upsert[0],strength:'hard'}]}));assert.equal(wrongStrength.upsert[0].strength,'soft');
  await assert.rejects(()=>extractRequirements('最好家具齐全',empty,mock({...proposal,upsert:[{criterion_id:'furnishing',field:'furnishing',operator:'eq',value:'fully_furnished',strength:'soft',weight:-1}],sources:[{field:'furnishing',quote:'最好家具齐全'}]})));
  const ambiguous=await extractRequirements('预算3000，预算3500以内也可以',empty,mock({upsert:[],remove_fields:[],sources:[],clarifications:[],supported:true}));assert(ambiguous.clarifications.length>0);
});
test('compound search and summary preserves dependency order; existing-result summary stays direct',()=>{
  const result=(intent:string,intent_group:string,requested_tasks:string[]=[])=>({intent,intent_group,requested_tasks,confidence:0.9} as Parameters<typeof planIntent>[0]);
  assert.deepEqual(planIntent(result('property_search','search',['search','summary'])),{kind:'search',agent:'a',deferred_summary:true});
  assert.equal(planIntent(result('recommendation_summary','followup')).kind,'summary');
  assert.equal(planIntent(result('requirement_update','search',['search','explain'])).kind,'search');
  assert.equal(planIntent(result('recommendation_explanation','explanation')).kind,'explain');
  assert.equal(planIntent(result('followup_status','followup')).kind,'status');
});
test('budget-only changes preserve bedrooms/district and stable criterion IDs',()=>{
  const one=mergeRequirements(empty,extractRules('租房，预算不超过5000，至少两房，D05',empty));
  const next=mergeRequirements(one,extractRules('预算改为4500',one));
  assert.equal(next.criteria.find(c=>c.field==='price')?.value,4500);
  assert.equal(next.criteria.find(c=>c.field==='bedrooms')?.value,2);
  assert.equal(next.criteria.find(c=>c.field==='district')?.value,'D05');
  assert.equal(one.criteria.find(c=>c.field==='price')?.criterion_id,next.criteria.find(c=>c.field==='price')?.criterion_id);
  assert.equal(hash(next),hash(mergeRequirements(next,extractRules('预算改为4500',next))));
});
test('explicit removal deletes only the chosen criterion',()=>{
  const base=mergeRequirements(empty,extractRules('预算5000，至少2房，D05',empty));
  const next=mergeRequirements(base,extractRules('区域不限',base));assert(!next.criteria.some(c=>c.field==='district'));assert.equal(next.criteria.length,2);
});
test('ambiguous room count and conflicting budgets require clarification',()=>{
  assert(extractRules('租房，预算3000，一定两房',empty).clarifications.length);
  assert(extractRules('预算3000，预算3500以内也可以',empty).clarifications.length);
  assert.equal(extractRules('预算5000，恰好2房',empty).upsert.find(c=>c.field==='bedrooms')?.operator,'eq');
});
test('soft preferences do not turn the preceding hard budget into a preference',()=>{
  const d=extractRules('预算5000，最好超市直线距离800米以内',empty);
  assert.equal(d.upsert.find(c=>c.field==='price')?.strength,'hard');
  assert.equal(d.upsert.find(c=>c.field==='place_proximity')?.strength,'soft');
});
test('currency, negative amounts and missing area units are not silently guessed',()=>{
  for(const q of ['预算3000美元','预算-100','面积至少80'])assert(extractRules(q,empty).clarifications.length,q);
  const area=extractRules('面积至少80平方米',empty).upsert.find(c=>c.field==='floor_area_sqft');assert(Math.abs(Number(area?.value)-861.1128333368)<1e-8);
});
test('ranges, district exclusion and leading-zero postal codes survive normalization',()=>{
  const range=extractRules('预算3000到4000',empty).upsert.find(c=>c.field==='price');assert.deepEqual(range?.value,[3000,4000]);assert.equal(range?.operator,'between');
  const district=extractRules('不要D05',empty).upsert.find(c=>c.field==='district');assert(district?.operator==='in'&&!(district.value as string[]).includes('D05'));
  assert.equal(extractRules('邮编012345',empty).upsert[0].value,'012345');
});
test('unsupported business requirements are preserved, without confusing tenure with lease length',()=>{
  const d=extractRules('必须整租，租期一年，家具齐全，现在可租',empty);assert(d.upsert.some(c=>c.field==='lease_months'&&c.value===12));assert(d.upsert.some(c=>c.field==='rental_scope'));
  assert.equal(observed({tenure_category:'99-year Leasehold'},criterion('lease_months','eq',12,'month')),null);
  assert.equal(observed({is_verified:true,detail_fetched:true},criterion('current_availability','eq',true)),null);
});
test('hard failures dominate unknown values and missing soft facts never exclude',()=>{
  const hard=criterion('price','lte',3000,'SGD/month'),unknown=criterion('furnishing','eq','fully_furnished');
  const r=(id:string,status:'pass'|'fail'|'unknown')=>({criterion_id:id,status,observed_value:null,evidence_ids:['e']});
  assert.equal(classify([r(hard.criterion_id,'fail'),r(unknown.criterion_id,'unknown')],[hard,unknown]),'excluded');
  assert.equal(classify([r(hard.criterion_id,'pass'),r(unknown.criterion_id,'unknown')],[hard,unknown]),'unverified');
  assert.equal(classify([r('soft','unknown')],[{...unknown,criterion_id:'soft',strength:'soft',weight:3}]),'eligible');
});
test('negative bedroom sentinel and foreign currency are unknown; threshold is inclusive',()=>{
  assert.equal(observed({bedrooms:-1},criterion('bedrooms','gte',2,'count')),null);
  assert.equal(observed({price:2800,currency:'USD',listing_type:'RENT'},criterion('price','lte',3000,'SGD/month')),null);
  assert(compare(3000,criterion('price','lte',3000,'SGD/month')));
  assert(!compare(3000.01,criterion('price','lte',3000,'SGD/month')));
});
test('geodesic has zero at equal coordinates and known Singapore scale; walking remains distinct',()=>{
  assert.equal(geodesic(1.3,103.8,1.3,103.8),0);
  assert(Math.abs(geodesic(1.3,103.8,1.31,103.8)-1111.950802)<0.01);
  const d=extractRules('必须超市步行距离800米以内',empty);assert.equal(d.upsert[0].distance_type,'walking');
});
test('malformed values and dimensions fail before recommendation execution',()=>{
  for(const bad of [{...criterion('price','lte',3000,'SGD/month'),unit:'USD/month'},{...criterion('bedrooms','gte',2,'count'),value:2.2},{...criterion('price','lte',3000,'SGD/month'),value:Infinity},{...criterion('price','lte',3000,'SGD/month'),operator:'in'}])assert(!criterionSchema.safeParse(bad).success);
  assert(!recommendationRequestSchema.safeParse({contract_version:'1.0',request_id:'x',conversation_id:'s',requirements_version:1,requirements:{...empty,criteria:[criterion('price','lte',3000,'SGD/month'),criterion('price','lte',5000,'SGD/month')]},result_limit:5}).success);
});
test('tool permission is checked before touching a valid cache',async()=>{
  const tools=new ToolExecutor();const ctx:ToolContext={sessionId:'s',operationId:'o',agent:'b',dataset:'d',deadline:Date.now()+10000,calls:0};
  assert.equal(await tools.run(ctx,'listings',{},async()=>42,10000),42);
  await assert.rejects(tools.run({...ctx,agent:'a'},'listings',{},async()=>99,10000),/TOOL_FORBIDDEN/);
});
test('repeated cursors are rejected, not reported as complete data coverage',async()=>{
  const adapter=new DataHttpAdapter('http://127.0.0.1:8089',new ToolExecutor(),async()=>'ref');
  adapter.request=async()=>({items:[{entity_id:'Listing:1',label:'Listing',properties:{listing_id:1}}],next_cursor:'repeated',has_more:true,page_size:1000,source:{store:'neo4j',role:'rebuildable_projection',route_verified:false},warnings:[]});
  await assert.rejects(adapter.all({sessionId:'s',operationId:'o',agent:'b',dataset:'d',deadline:Date.now()+10000,calls:0},'listings'),/REPEATED_CURSOR/);
});
test('source URL adapter forbids arbitrary URLs and credentials',()=>{assert.throws(()=>new DataHttpAdapter('https://user:password@example.com',new ToolExecutor(),async()=>''));});
test('canonical hashes survive JSONB key reordering',()=>assert.equal(hash({z:1,a:{x:2,b:3}}),hash({a:{b:3,x:2},z:1})));

test('hard caps and soft price preferences remain separate',()=>{
  const base=mergeRequirements(empty,extractRules('预算5000',empty));
  const next=mergeRequirements(base,{upsert:[criterion('price','lte',4000,'SGD/month',true)],remove_fields:[],sources:[],clarifications:[],supported:true});
  assert.equal(next.criteria.length,2);assert(next.criteria.some(c=>c.strength==='hard'&&c.value===5000));
});
test('authoritative response validator rejects wrong facts, stale IDs, unknown hard passes, ranks and totals',async()=>{
  const raw=JSON.parse(await readFile(new URL('./fixtures/recommendation-response.json',import.meta.url),'utf8'));
  const request=recommendationRequestSchema.parse({contract_version:'1.0',request_id:raw.request_id,conversation_id:raw.conversation_id,requirements_version:1,requirements:{listing_type:'RENT',criteria:[criterion('price','lte',3000,'SGD/month'),criterion('bedrooms','gte',2,'count'),{criterion_id:'preferred-district',field:'district',operator:'eq',value:'D05',strength:'hard'},criterion('place_proximity','lte',800,'m',true,{criterion_id:'near-supermarket',weight:3,target:{category:'Supermarket'},distance_type:'straight_line'})]},feedback:[],result_limit:5});
  validateResponse(raw,request);
  const changes:((r:any)=>void)[]=[r=>r.request_id='obsolete',r=>r.recommendations[0].criterion_results.pop(),r=>r.recommendations[0].rank=2,r=>r.recommendations.push(structuredClone(r.recommendations[0])),r=>r.state.counts.excluded=5,r=>r.recommendations[0].listing.price=9000,r=>r.recommendations[0].score_breakdown[0].contribution=.9,r=>r.recommendations[0].explanation_evidence.strengths[0].evidence_ids=['missing'],r=>{r.evidence[0].value=null;r.recommendations[0].criterion_results[0].observed_value=null;},r=>{r.evidence[0].value=4000;r.recommendations[0].criterion_results[0].observed_value=4000;r.recommendations[0].listing.price=4000;}];
  for(const change of changes){const copy=structuredClone(raw);change(copy);assert.throws(()=>validateResponse(copy,request));}
  const summary=renderSummary(request.requirements,raw,'fixture-only');assert(summary.html.includes('fixture-only'));assert(summary.markdown.includes('当前可租'));
});
