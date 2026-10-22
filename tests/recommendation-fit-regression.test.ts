import test from 'node:test';
import assert from 'node:assert/strict';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {criterion, draftSchema, extractRules, extractRequirements, mergeRequirements, pendingQuestions} from '../src/domain/requirements.js';
import {parseLocationIntents} from '../src/domain/geo-intake.js';
import {LocationService} from '../src/adapters/locations.js';
import type {DataHttpAdapter} from '../src/adapters/data-http.js';
import type {ToolExecutor, ToolContext} from '../src/tools/executor.js';
import {output} from '../src/agents/requirement/few-shot.js';

const empty = {listing_type:'RENT' as const, criteria:[]};
const bedroomCases = [
  ['Find rental properties in D10 with at least 3 bedrooms and monthly rent no more than SGD 6500.', 'gte'],
  ['Monthly rent no more than SGD 6500 and at least 3 bedrooms in D10.', 'gte'],
  ['Find exactly 3 bedrooms with monthly rent no more than SGD 6500.', 'eq'],
  ['Find no more than 3 bedrooms and monthly rent no more than SGD 6500.', 'lte'],
  ['Find 3 bedrooms or more, monthly rent no more than SGD 6500.', 'gte'],
  ['Find 3 bedrooms, monthly rent no more than SGD 6500.', 'eq'],
] as const;
for (const [query, operator] of bedroomCases) test(`bedroom comparison stays local: ${query}`, () => {
  const draft = extractRules(query, empty);
  assert.equal(draft.upsert.find(c=>c.field==='bedrooms')?.operator, operator);
  assert.equal(draft.upsert.find(c=>c.field==='bedrooms')?.value, 3);
  assert.equal(draft.upsert.find(c=>c.field==='price')?.value, 6500);
});

const spokenComparisonCases = [
  ['至少有3间卧室，月租不超过6500新币', 'bedrooms', 'gte', 3],
  ['最多有3间卧室，月租不超过6500新币', 'bedrooms', 'lte', 3],
  ['至少要有3房，月租不超过6500新币', 'bedrooms', 'gte', 3],
  ['最多需要2个卫生间，月租不超过6500新币', 'bathrooms', 'lte', 2],
  ['面积最多为80平方米，至少2房，月租不超过6500新币', 'floor_area_sqft', 'lte', 80 * 10.76391041671],
  ['恰好有3间卧室，月租不超过6500新币', 'bedrooms', 'eq', 3],
] as const;
for (const [query, field, operator, value] of spokenComparisonCases) test(`spoken comparator preserves its attribute: ${query}`, () => {
  const draft = extractRules(query, empty);
  const parsed = draft.upsert.find(c=>c.field===field);
  assert.equal(parsed?.operator, operator);
  assert.equal(parsed?.value, value);
  assert.equal(draft.upsert.find(c=>c.field==='price')?.value, 6500);
  assert.deepEqual(draft.clarifications, []);
});

test('bathroom and area comparisons are independent in one English clause', () => {
  const draft = extractRules('At least 2 bathrooms and floor area no more than 900 sqft', empty);
  assert.equal(draft.upsert.find(c=>c.field==='bathrooms')?.operator, 'gte');
  assert.equal(draft.upsert.find(c=>c.field==='floor_area_sqft')?.operator, 'lte');
  const reverse = extractRules('At least 1200 sqft and no more than 3 bedrooms', empty);
  assert.equal(reverse.upsert.find(c=>c.field==='floor_area_sqft')?.operator, 'gte');
  assert.equal(reverse.upsert.find(c=>c.field==='bedrooms')?.operator, 'lte');
});

test('district preference does not become a hard filter', () => {
  const draft = extractRules('请找月租不超过2500新币，至少2房，优先D05，但其他邮区也可以。', empty);
  assert.equal(draft.upsert.find(c=>c.field==='district')?.strength, 'soft');
  assert.equal(draft.upsert.find(c=>c.field==='price')?.strength, 'hard');
  assert.equal(draft.upsert.find(c=>c.field==='bedrooms')?.strength, 'hard');
});

test('a hard bedroom phrase does not change a separate English district preference', () => {
  const draft = extractRules('Must have at least 2 bedrooms and preferably D05, other districts are acceptable.', empty);
  assert.equal(draft.upsert.find(c=>c.field==='district')?.strength, 'soft');
  assert.equal(extractRules('现在D05必须满足', empty).upsert.find(c=>c.field==='district')?.strength, 'hard');
});

test('explicit district cancellation is accepted with the original model evidence', async () => {
  const base = mergeRequirements(empty, extractRules('预算2500，至少2房，必须D05', empty));
  const query = '取消邮区限制，其他条件不变。';
  const proposal=output(query,{removals:[{field:'district',criterion_id:null,quote:query}],coverage:[{quote:query,disposition:'removed'}]});
  let audited=false;
  const model = {withStructuredOutput:(_schema:unknown,options:any)=>({invoke:async(messages:any[])=>{
    if(options.name==='submit_requirement_intake_v2')return proposal;
    const audit=JSON.parse(String(messages.at(-1).content));
    assert.equal(audit.query,query);assert.deepEqual(audit.compiled_draft.remove_fields,['district']);
    assert(audit.compiled_draft.sources.some((s:any)=>s.field==='district'&&s.quote===query));
    audited=true;return {valid:true,issues:[]};
  }})} as unknown as BaseChatModel;
  const next = mergeRequirements(base, await extractRequirements(query, base, model));
  assert(audited);
  assert(!next.criteria.some(c=>c.field==='district'));
  assert(next.criteria.some(c=>c.field==='price'&&c.value===2500&&c.operator==='lte'));
  assert(next.criteria.some(c=>c.field==='bedrooms'&&c.value===2&&c.operator==='gte'));
});

for (const query of ['不要取消邮区限制', '先不取消邮区限制', 'Do not remove district']) test(`negated cancellation retains district: ${query}`, () => {
  const base = mergeRequirements(empty, extractRules('必须D05', empty));
  const next = mergeRequirements(base, extractRules(query, base));
  assert(next.criteria.some(c=>c.field==='district'&&c.value==='D05'));
});

test('a model cannot authorize an unrelated district removal', async () => {
  const base = mergeRequirements(empty, extractRules('必须D05', empty));
  const query = '其他条件不变';
  const proposal=output(query,{removals:[{field:'district',criterion_id:null,quote:query}],coverage:[{quote:query,disposition:'removed'}]});
  const before=structuredClone(base);let audits=0;
  const model = {withStructuredOutput:(_schema:unknown,options:any)=>({invoke:async(messages:any[])=>{
    if(options.name==='submit_requirement_intake_v2')return proposal;
    const audit=JSON.parse(String(messages.at(-1).content));
    assert.equal(audit.query,query);assert.deepEqual(audit.compiled_draft.remove_fields,['district']);
    audits++;return {valid:false,issues:[{quote:query,reason:'removal is not explicitly authorized'}]};
  }})} as unknown as BaseChatModel;
  await assert.rejects(()=>extractRequirements(query,base,model), /removal is not explicitly authorized/);
  assert(audits>0);assert.deepEqual(base,before);
});

for (const [query, radius] of [
  ['找NUS国立大学直线距离3公里内的出租房，月租不超过3000新币。', 3000],
  ['到NUS国立大学的直线距离改为2公里，预算不变。', 2000],
] as const) test(`NUS place identity survives radius wording: ${radius}`, () => {
  const [intent] = parseLocationIntents(query);
  assert.equal(intent.name, 'NUS国立大学');
  assert.equal(intent.radius_m, radius);
  assert.equal(intent.distance_type, 'straight_line');
  assert.equal(intent.strength, 'hard');
});

test('the bilingual NUS alias resolves through the canonical directory query', async () => {
  let requested = '';
  const entity = {entity_id:'OSM:way:54519165',label:'Place',properties:{place_id:'OSM:way:54519165',name:'National University of Singapore'}};
  const data = {resolve:async (_ctx:ToolContext,name:string)=>{requested=name;return {items:[entity],ref:'source'};}} as unknown as DataHttpAdapter;
  const tools = {run:async (_ctx:ToolContext,_name:string,_input:unknown,fn:()=>Promise<unknown>)=>fn()} as unknown as ToolExecutor;
  const service = new LocationService(data,tools,async()=> 'receipt');
  const result = await service.resolve({} as ToolContext,'NUS国立大学');
  assert.equal(requested,'National University of Singapore');
  assert.equal(result.entity?.entity_id,'OSM:way:54519165');
});

test('resolving the same legacy NUS mention clears only its obsolete question', () => {
  const query='到NUS国立大学的直线距离改为2公里，预算不变。';
  const oldQuestion='地点目录暂未找到“NUS国立大学直线距离”，请确认名称或换一个常用名称。';
  const otherQuestion='地点目录暂未找到“Unknown Mall”，请确认名称或换一个常用名称。';
  const pending=draftSchema.parse({upsert:[criterion('price','lte',3000,'SGD/month')],remove_fields:[],sources:[],supported:true,clarifications:[oldQuestion,otherQuestion],location_intents:[{name:'NUS国立大学直线距离',quote:'找NUS国立大学直线距离3公里内',radius_m:3000,strength:'hard',distance_type:'straight_line',explicit_station:false}]});
  const draft=draftSchema.parse({upsert:[criterion('place_proximity','lte',2000,'m',false,{target:{place_id:'OSM:way:54519165',name:'National University of Singapore'},distance_type:'straight_line'})],remove_fields:[],sources:[{field:'place_proximity',quote:query}],supported:true,clarifications:[],location_intents:[{name:'NUS国立大学',quote:query,radius_m:2000,strength:'hard',distance_type:'straight_line',explicit_station:false}]});
  assert.deepEqual(pendingQuestions(pending,draft),[otherQuestion]);
  assert.deepEqual(pendingQuestions(pending,extractRules('租期6个月',empty)),[oldQuestion,otherQuestion]);
});

test('fully furnished phrasing preserves its meaning without claiming source evidence', () => {
  const draft=extractRules('还必须带齐家具，租期为6个月。',empty);
  assert(draft.upsert.some(c=>c.field==='furnishing'&&c.value==='fully_furnished'&&c.strength==='hard'));
  assert(draft.upsert.some(c=>c.field==='lease_months'&&c.value===6));
});
