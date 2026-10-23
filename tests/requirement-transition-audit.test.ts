import test from 'node:test';
import assert from 'node:assert/strict';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {extractRequirements,mergeRequirements,criterion} from '../src/domain/requirements.js';
import {attribute,location,output} from '../src/agents/requirement/few-shot.js';
import {WorkflowService} from '../src/domain/workflow.js';
import type {IntakeAttempt} from '../src/agents/requirement/extractor.js';
import type {Requirements} from '../src/contracts/recommendation-v1.js';
import {intakeAuditRequirements} from '../src/agents/requirement/audit-context.js';

const base:Requirements={listing_type:'RENT',criteria:[
  criterion('bedrooms','eq',1,'count'),criterion('bathrooms','eq',1,'count'),criterion('price','lte',2500,'SGD/month'),
  criterion('place_proximity','lte',1000,'m',true,{target:{category:'PublicTransport',name:'最近的MRT或公交站'},distance_type:'straight_line',distance_preference:'bounded',criterion_id:'transport'}),
  criterion('place_proximity','lte',5000,'m',false,{target:{place_id:'URA:AREA:JURONG_EAST',name:'JURONG EAST'},distance_type:'straight_line',distance_preference:'bounded',criterion_id:'area'}),
  criterion('place_proximity','lte',1000,'m',true,{target:{place_id:'URA:AREA:JURONG_EAST',name:'JURONG EAST'},distance_type:'straight_line',distance_preference:'nearer',criterion_id:'closer'}),
]};
const unchanged=()=>mergeRequirements(base,{upsert:[],remove_fields:[],sources:[],clarifications:[],supported:true});
const prepare=async(draft:any)=>{
  await (WorkflowService.prototype as any).resolveRequirementLocations.call({locations:{resolve:async()=>({entity:{properties:{place_id:'URA:AREA:JURONG_EAST',name:'JURONG EAST'}},note:'resolved'})}},draft,base,{});
  return {draft,effective_requirements:mergeRequirements(base,draft)};
};
test('semantic auditor sees the actual resolved and merged state, including retained layout and transport union',async()=>{
  const q='裕廊东附近5公里内，最好1公里内有MRT或公交站，预算2500新币';
  const proposal=output(q,{upsert:[attribute('price','lte',2500,q)],locations:[location('裕廊东',[],q,{operator:'lte',value:5,unit:'km',strength:'hard'}),location(null,['MRTStation','BusStop'],q,{operator:'lte',value:1,unit:'km'})]});
  const events:IntakeAttempt[]=[];let audits=0;
  const model={withStructuredOutput:(_schema:any,options:any)=>({invoke:async(messages:any[])=>{
    if(options.name==='submit_requirement_intake_v2')return proposal;
    audits++;const input=JSON.parse(String(messages.at(-1).content));
    assert.deepEqual(input.effective_requirements,intakeAuditRequirements(unchanged()));
    assert.equal(input.compiled_draft.location_intents[1].category,'PublicTransport');
    assert.match(String(messages[0].content),/评分尺度/);
    const closer=input.effective_requirements.criteria.find((c:any)=>c.criterion_id==='closer');
    assert.equal(closer.meaning,'prefer_closer');assert.equal(closer.distance_cutoff,null);assert(!('value' in closer));
    return {valid:true,issues:[]};
  }})} as unknown as BaseChatModel;
  const d=await extractRequirements(q,base,model,'',{prepareDraft:prepare,onAttempt:async e=>{events.push(structuredClone(e));}});
  assert.deepEqual(mergeRequirements(base,d),unchanged());assert.equal(audits,1);assert.equal(events.length,1);assert.equal(events[0].audit?.valid,true);
});

test('semantic projection leaves bounded transport distance intact and does not mutate the saved ranking criterion',()=>{
  const before=structuredClone(base),view=intakeAuditRequirements(base);
  const transport=view.criteria.find(c=>c.criterion_id==='transport')!;
  assert('value' in transport&&transport.value===1000);assert.deepEqual(base,before);
  const closer=view.criteria.find(c=>c.criterion_id==='closer')!;
  assert('meaning' in closer&&closer.meaning==='prefer_closer');assert(!('value' in closer));
  const hard=criterion('place_proximity','lte',1000,'m',false,{target:{place_id:'URA:AREA:JURONG_EAST',name:'JURONG EAST'},distance_type:'straight_line',distance_preference:'nearer'});
  assert.deepEqual(intakeAuditRequirements({listing_type:'RENT',criteria:[hard]}).criteria[0],hard);
});
test('remove then recreate an equivalent location is audited as its final state, not an omitted requirement',async()=>{
  const q='还要裕廊东5公里内，交通偏好不变';
  const proposal=output(q,{locations:[location('裕廊东',[],q,{operator:'lte',value:5,unit:'km',strength:'hard'})],removals:[{field:'place_proximity',criterion_id:'area',quote:q},{field:'place_proximity',criterion_id:'closer',quote:q}]});
  const model={withStructuredOutput:(_schema:any,options:any)=>({invoke:async(messages:any[])=>{
    if(options.name==='submit_requirement_intake_v2')return proposal;
    const input=JSON.parse(String(messages.at(-1).content));
    assert.equal(input.effective_requirements.criteria.filter((c:any)=>c.target?.place_id==='URA:AREA:JURONG_EAST').length,2);
    assert(input.effective_requirements.criteria.some((c:any)=>c.criterion_id==='transport'));
    return {valid:true,issues:[]};
  }})} as unknown as BaseChatModel;
  const d=await extractRequirements(q,base,model,'',{prepareDraft:prepare});assert.equal(d.extraction?.attempts,1);
});
test('genuine accidental removal remains rejected; each failed attempt is recorded and the base stays unchanged',async()=>{
  const q='预算2500新币，其他不变',before=structuredClone(base),events:IntakeAttempt[]=[];
  const proposal=output(q,{removals:[{field:'bathrooms',criterion_id:'bathrooms',quote:q}]});
  const model={withStructuredOutput:(_schema:any,options:any)=>({invoke:async(messages:any[])=>{
    if(options.name==='submit_requirement_intake_v2')return proposal;
    const input=JSON.parse(String(messages.at(-1).content));assert(!input.effective_requirements.criteria.some((c:any)=>c.field==='bathrooms'));
    return {valid:false,issues:[{quote:q,reason:'错误删除未修改的卫浴要求'}]};
  }})} as unknown as BaseChatModel;
  await assert.rejects(()=>extractRequirements(q,base,model,'',{prepareDraft:prepare,onAttempt:async e=>{events.push(structuredClone(e));}}),/SEMANTIC_AUDIT/);
  assert.equal(events.length,3);assert(events.every(e=>e.error));assert.deepEqual(base,before);
});
