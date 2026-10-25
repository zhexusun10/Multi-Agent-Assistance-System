import test from 'node:test';
import assert from 'node:assert/strict';
import {extractRules,extractRequirements,mergeRequirements,criterion,pendingQuestions} from '../src/domain/requirements.js';
import {observed,classify} from '../src/adapters/recommendation.js';
import {unknownReason} from '../src/domain/field-capabilities.js';
import {criterionText} from '../src/domain/explanation.js';
import {rentalNumber} from '../src/domain/rental-language.js';
import {attribute,output} from '../src/agents/requirement/few-shot.js';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
const empty={listing_type:'RENT' as const,criteria:[]};
test('1B1B is exactly one bedroom and one bathroom; it proves neither private use nor whole-unit rental',()=>{
 for(const q of ['1B1B','一室一卫','one bedroom and one bathroom']){const d=extractRules(q,empty);assert.equal(d.clarifications.length,0);assert.deepEqual(d.upsert.map(c=>[c.field,c.operator,c.value]).sort(),[['bathrooms','eq',1],['bedrooms','eq',1]]);}
 const p={bedrooms:1,bathrooms:1};assert.equal(observed(p,criterion('amenities','eq','private_bathroom')),null);assert.equal(observed(p,criterion('rental_scope','eq','whole_unit')),null);
});
test('oral Chinese money, bounded dimensions and distinct hard/soft prices retain their meaning',()=>{
 for(const [word,n] of [['两千五',2500],['三千',3000],['2.5k',2500],['八十',80]] as const)assert.equal(rentalNumber(word),n);
 const d=extractRules('预算3000，最好租金2500以内，面积不超过900sqft',empty);assert(d.upsert.some(c=>c.field==='price'&&c.strength==='hard'&&c.value===3000));assert(d.upsert.some(c=>c.field==='price'&&c.strength==='soft'&&c.value===2500));assert(d.upsert.some(c=>c.field==='floor_area_sqft'&&c.operator==='lte'));
 assert.equal(extractRules('土地面积至少2000sqft',empty).upsert[0].field,'land_area_sqft');
});
test('negating a private bathroom never adds it or removes the bathroom count',()=>{
 const base=mergeRequirements(empty,extractRules('1B1B，独立卫浴',empty)),next=mergeRequirements(base,extractRules('不要求独立卫浴',base));assert(next.criteria.some(c=>c.field==='bathrooms'&&c.value===1));assert(!next.criteria.some(c=>c.field==='amenities'));assert.equal(extractRules('预算2500，没有泳池也可以',empty).clarifications.length,0);
});
test('MRT platform annotations and actual commute remain separate; HDB room labels do not become bedroom counts',()=>{
 const d=extractRules('地铁站步行10分钟内，去NUS公交30分钟以内',empty);assert(d.upsert.some(c=>c.field==='mrt_walking_mins'&&c.value===10&&c.distance_type==='platform_reported'));assert(d.upsert.some(c=>c.field==='commute_time'&&c.value===30&&c.target?.name==='NUS'));assert(!d.upsert.some(c=>c.field==='price'));
 const hdb=extractRules('HDB 3-room flat',empty);assert(hdb.clarifications.length);assert(!hdb.upsert.some(c=>c.field==='bedrooms'));
});
test('semantic audit returns an invented rental scope to the model, while arithmetic stays deterministic',async()=>{
 const q='面积至少80平方米，不和房东同住';let calls=0,audits=0;
 const bad=output(q,{upsert:[attribute('floor_area_sqft','gte',80,'面积至少80平方米',{unit:'sqm'}),attribute('rental_scope','eq','whole_unit','不和房东同住')]}),good=output(q,{upsert:[attribute('floor_area_sqft','gte',80,'面积至少80平方米',{unit:'sqm'}),attribute('amenities','eq','no_live_in_landlord','不和房东同住')]});
 const mock={withStructuredOutput:(_s:unknown,o:any)=>({invoke:async()=>o.name==='audit_requirement_intake'?(++audits===1?{valid:false,issues:[{quote:'不和房东同住',reason:'不能推断整租'}]}:{valid:true,issues:[]}):(++calls===1?bad:good)})} as unknown as BaseChatModel;
 const draft=await extractRequirements(q,empty,mock);assert.equal(draft.extraction?.attempts,2);assert.equal(draft.clarifications.length,0);assert(Math.abs(Number(draft.upsert.find(c=>c.field==='floor_area_sqft')?.value)-861.1128333368)<.00001);assert(!draft.upsert.some(c=>c.field==='rental_scope'));assert(draft.upsert.some(c=>c.field==='amenities'&&c.value==='no_live_in_landlord'));
});
test('missing data, absent capability and unconnected routes have distinct user-readable reasons',()=>{
 const privateBath=criterion('amenities','eq','private_bathroom');assert((unknownReason(privateBath)).toLowerCase().includes("data unavailable"));assert((unknownReason(criterion('commute_time','lte',30,'min'))).toLowerCase().includes("route tools are not connected"));assert((unknownReason(criterion('floor_area_sqft','gte',80,'sqft'))).toLowerCase().includes("recorded value is missing"));assert(!criterionText(privateBath).includes('amenities'));assert(!(criterionText(privateBath)).toLowerCase().includes("equals"));
 const r={criterion_id:privateBath.criterion_id,status:'unknown' as const,observed_value:null,evidence_ids:['e']};assert.equal(classify([r],[privateBath]),'unverified');
});
test('canceling one amenity preserves other amenities and bathroom counts',()=>{
 const base=mergeRequirements(empty,extractRules('1B1B，独立卫浴，最好有泳池',empty)),draft=extractRules('取消独立卫浴要求',base),next=mergeRequirements(base,draft);
 assert(next.criteria.some(c=>c.field==='amenities'&&c.value==='swimming_pool'));assert(!next.criteria.some(c=>c.field==='amenities'&&c.value==='private_bathroom'));assert(next.criteria.some(c=>c.field==='bathrooms'));assert(draft.remove_criterion_ids?.length);
 const air=mergeRequirements(empty,extractRules('预算2500，需要空调',empty)),removed=mergeRequirements(air,extractRules('不需要空调了',air));assert(!removed.criteria.some(c=>c.field==='amenities'));assert(removed.criteria.some(c=>c.field==='price'));
});
test('market regions use actual data codes and URA geographic preferences remain separate',()=>{
 assert.equal(extractRules('CCR区域',empty).upsert[0].value,'CCR');const pending=extractRules('north-east region',empty);assert(pending.clarifications.length);assert.deepEqual(pendingQuestions(pending,extractRules('CCR区域',empty)),[]);const near=extractRules('东北大区附近',empty);assert.equal(near.clarifications.length,0);assert.equal(near.location_intents?.[0].strength,'soft');assert(!near.upsert.some(c=>c.field==='region_code'));
});
