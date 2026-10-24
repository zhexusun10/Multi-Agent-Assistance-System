import test from 'node:test';
import assert from 'node:assert/strict';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {modelIntakeSchema} from '../src/agents/requirement/contracts.js';
import {compileIntake} from '../src/agents/requirement/validation.js';
import {attribute,location,output,INTAKE_EXAMPLES,selectIntakeExamples} from '../src/agents/requirement/few-shot.js';
import {extractRequirements,mergeRequirements,criterion} from '../src/domain/requirements.js';
const empty={listing_type:'RENT' as const,criteria:[]},meta={attempts:1,example_ids:[]};
const mock=(outputs:unknown[],calls:any[]=[])=>({withStructuredOutput:(schema:unknown,options:unknown)=>{if((options as any)?.name==='audit_requirement_intake')return {invoke:async()=>({valid:true,issues:[]})};calls.push({schema,options});return {invoke:async(messages:any[])=>{calls.push(messages);const result=outputs.length>1?outputs.shift():outputs[0];if(result instanceof Error)throw result;return result;}};}}) as unknown as BaseChatModel;

test('model-first preserves a geographic relation missed by legacy rules; no rule-owned field suppression',async()=>{
  const q='南洋理工大学5公里内，周边1公里最好有MRT或公交站',calls:any[]=[];
  const proposal=output(q,{locations:[location('南洋理工大学',[],q,{operator:'lte',value:5,unit:'km',strength:'hard'}),location(null,['MRTStation','BusStop'],q,{operator:'lte',value:1,unit:'km'})]});
  const d=await extractRequirements(q,empty,mock([proposal],calls));
  assert.equal(d.location_intents?.length,2);assert.equal(d.location_intents?.[1].category,'PublicTransport');assert.equal(d.location_intents?.[1].radius_m,1000);assert.equal(d.location_intents?.[1].strength,'soft');
  assert.equal(calls[0].options.strict,true);assert.equal(calls[1].length,6);assert(!String(calls[1][0].content).includes('代码已确定的字面草案'));
  assert.equal(d.extraction?.mode,'model');assert.equal(d.extraction.example_ids.length,2);
});
test('strict schema rejects unknown fields, model-generated place IDs, extra keys and string numbers',()=>{
  const q='预算2000',a=attribute('price','lte',2000,q),good=output(q,{upsert:[a]});assert(modelIntakeSchema.safeParse(good).success);
  for(const bad of [{...good,listing_id:'123'},{...good,upsert:[{...a,field:'invented'}]},{...good,upsert:[{...a,place_id:'fake'}]},{...good,upsert:[{...a,value:'2000'}]}])assert.throws(()=>compileIntake(bad,q,empty,meta));
});
test('structured correction retries with original query and validation errors, without keyword fallback',async()=>{
  const q='别和别人共用卫生间',good=output(q,{upsert:[attribute('amenities','eq','private_bathroom',q)]}),calls:any[]=[];
  const d=await extractRequirements(q,empty,mock([{...good,upsert:[{...good.upsert[0],quote:'编造的引用'}]},good],calls));
  assert.equal(d.extraction?.attempts,2);assert.equal(d.upsert[0].value,'private_bathroom');assert.equal(calls[2].length,7);assert.match(String(calls[2].at(-1).content),/SOURCE_QUOTE/);
});
test('authentication failure stops immediately rather than silently replacing model semantics with rules',async()=>{
  const calls:any[]=[];await assert.rejects(()=>extractRequirements('预算2000',empty,mock([Error('401 Authentication failed')],calls)),/REQUIREMENT_EXTRACTION_FAILED/);assert.equal(calls.length,2);
});
test('missing model requires an explicit offline rules mode',async()=>{
  const previous=process.env.LLM_MODE;try{process.env.LLM_MODE='model';await assert.rejects(()=>extractRequirements('预算2000',empty),/REQUIREMENT_MODEL_REQUIRED/);process.env.LLM_MODE='rules';assert.equal((await extractRequirements('预算2000',empty)).upsert[0].value,2000);}finally{if(previous===undefined)delete process.env.LLM_MODE;else process.env.LLM_MODE=previous;}
});
test('sqm and kilometre conversions are deterministic after semantic extraction',()=>{
  const q='50平方米起，距NUS两公里以内';const d=compileIntake(output(q,{upsert:[attribute('floor_area_sqft','gte',50,q,{unit:'sqm'})],locations:[location('NUS',[],q,{operator:'lte',value:2,unit:'km',strength:'hard'})]}),q,empty,meta);
  assert(Math.abs(Number(d.upsert[0].value)-538.195520835)<.00001);assert.equal(d.location_intents?.[0].radius_m,2000);
});
test('OR becomes one transport union while AND keeps both independent conditions',()=>{
  const q='地铁或公交站一公里';const a=location(null,['MRTStation','BusStop'],q,{operator:'lte',value:1,unit:'km'});
  assert.equal(compileIntake(output(q,{locations:[a]}),q,empty,meta).location_intents?.[0].category,'PublicTransport');
  assert.deepEqual(compileIntake(output(q,{locations:[{...a,relation:'all_of'}]}),q,empty,meta).location_intents?.map(l=>l.category),['MRTStation','BusStop']);
});
test('cancellation references a real old criterion and cannot remove unrelated amenity requirements',()=>{
  const base={listing_type:'RENT' as const,criteria:[{...criterion('amenities','eq','private_bathroom'),criterion_id:'private'},{...criterion('amenities','eq','pet_policy'),criterion_id:'pets'}]},q='不强求独卫了';
  const d=compileIntake(output(q,{removals:[{field:'amenities',criterion_id:'private',quote:q}]}),q,base,meta);
  assert.deepEqual(mergeRequirements(base,d).criteria.map(c=>c.criterion_id),['pets']);
  assert.throws(()=>compileIntake(output(q,{removals:[{field:'amenities',criterion_id:'fake',quote:q}]}),q,base,meta),/REMOVAL/);
});

test('a short location replacement removes the prior destination while retaining budget, layout and transit preference',async()=>{
 const example=INTAKE_EXAMPLES.find(e=>e.id==='short-replace-neighbourhood')!;
 assert(selectIntakeExamples(example.query).some(e=>e.id===example.id));
 const draft=await extractRequirements(example.query,example.base,mock([example.output]));
 const intent=draft.location_intents![0];assert.equal(intent.name,'荷兰村');assert.equal(intent.explicit_station,false);
 // The directory, rather than the model, supplies this real neighbourhood ID.
 draft.upsert.push(criterion('place_proximity','lte',1000,'m',true,{target:{place_id:'OSM:node:8777049478',name:'Holland Village'},distance_type:'straight_line',distance_preference:'nearer'}));
 const next=mergeRequirements(example.base,draft);
 assert(!next.criteria.some(c=>c.target?.place_id==='LTA:MRT:CHINATOWN'));
 assert.deepEqual(next.criteria.filter(c=>c.field!=='place_proximity'),example.base.criteria.filter(c=>c.field!=='place_proximity'));
 assert(next.criteria.some(c=>c.target?.category==='PublicTransport'));
});
test('explicit route semantics stay distinct from reported station time and vague nearby has no hard radius',()=>{
  const q='附近最好有MRT，步行去NUS半小时内';const d=compileIntake(output(q,{locations:[location(null,['MRTStation'],q)],upsert:[attribute('commute_time','lte',30,q,{target_name:'NUS',transport_mode:'walking'})]}),q,empty,meta);
  assert.equal(d.location_intents?.[0].radius_m,undefined);assert.equal(d.location_intents?.[0].strength,'soft');assert.equal(d.upsert[0].field,'commute_time');assert.equal(d.upsert[0].transport_mode,'walking');
});
test('every curated example itself satisfies the strict wire and application schemas',()=>{
  for(const e of INTAKE_EXAMPLES)assert.doesNotThrow(()=>compileIntake(e.output,e.query,e.base,meta),e.id);
  assert.equal(selectIntakeExamples('MRT或公交站在一公里内').length,2);
});
