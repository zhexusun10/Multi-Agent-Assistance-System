import {randomUUID,createHash} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {createModel} from '../src/model.js';
import {WorkflowService} from '../src/domain/workflow.js';
import {extractRequirements,mergeRequirements,criterion} from '../src/domain/requirements.js';
import type {Requirements} from '../src/contracts/recommendation-v1.js';
import type {IntakeAttempt} from '../src/agents/requirement/extractor.js';

const [failure]=JSON.parse(await readFile('evaluation/reports/requirement-update-failure-before.json','utf8'));
const base:Requirements=failure.input.requirements,model=await createModel(),skill=await readFile('skills/rental-requirement-intake/SKILL.md','utf8');
// Isolated extraction assessment: real location tools/catalogues, no workflow-state writes.
const workflow=new WorkflowService({pool:{query:async()=>({rows:[]})},savePayload:async()=>randomUUID()} as any,process.env.DATASET_VERSION!,model);
const empty={upsert:[],remove_fields:[],sources:[],clarifications:[],supported:true};
const canonical=(r:Requirements)=>mergeRequirements(r,empty);
const same=(actual:Requirements,expected=base)=>assert.deepEqual(actual,canonical(expected));
const geo=(r:Requirements,id:string)=>r.criteria.filter(c=>c.target?.place_id===id);
const price=(value:number)=>({...base,criteria:base.criteria.map(c=>c.field==='price'?{...c,value}:c)});
const cases:{id:string;query:string;base?:Requirements;check:(r:Requirements)=>void}[]=[
  {id:'initial-empty-base',base:{listing_type:'RENT',criteria:[]},query:'你好，我要租裕廊东附近5公里内的房子，预算在2500新币每月，房子附近1公里内最好有MRT或公交站，要求房型为1B1B',check:r=>same(r)},
  ...[1,2,3].map(i=>({id:'exact-replay-'+i,query:failure.query,check:(r:Requirements)=>same(r)})),
  {id:'restatement-order',query:'交通方面最好离MRT或者巴士站一公里以内。月租2500新币以内，房子在裕廊东五公里范围内，其他条件照旧。',check:r=>same(r)},
  {id:'budget-only',query:'预算上限改成2600新币，其他条件一个都不改。',check:r=>same(r,price(2600))},
  {id:'cancel-layout',query:'不用1B1B了，卧室和卫生间数量都不作要求，其他照旧。',check:r=>same(r,{...base,criteria:base.criteria.filter(c=>!['bedrooms','bathrooms'].includes(c.field))})},
  {id:'cancel-transport',query:'不用考虑MRT或公交站的距离了，其余不变。',check:r=>same(r,{...base,criteria:base.criteria.filter(c=>c.target?.category!=='PublicTransport')})},
  {id:'cancel-closer-only',query:'只保留裕廊东5公里范围，不用按离裕廊东远近排序，其他要求不变。',check:r=>same(r,{...base,criteria:base.criteria.filter(c=>c.criterion_id!=='place_proximity:URA:AREA:JURONG_EAST-preference')})},
  {id:'transport-radius',query:'把MRT或公交站的偏好距离从1公里收紧到700米，其余条件保留。',check:r=>same(r,{...base,criteria:base.criteria.map(c=>c.target?.category==='PublicTransport'?{...c,value:700}:c)})},
  {id:'replace-area',query:'不找裕廊东了，改找南洋理工大学附近3公里内的房子，其他要求不变。',check:r=>{
    assert.equal(geo(r,'URA:AREA:JURONG_EAST').length,0);
    assert(geo(r,'OSM:way:115105262').some(c=>c.strength==='hard'&&c.value===3000));
    for(const c of base.criteria.filter(c=>!c.target?.place_id))assert.deepEqual(r.criteria.find(x=>x.criterion_id===c.criterion_id),c);
  }},
  {id:'transport-and',query:'原来MRT或公交站二选一的偏好取消，改成地铁站和公交站都必须在800米以内，其他照旧。',check:r=>{
    assert(!r.criteria.some(c=>c.target?.category==='PublicTransport'));
    for(const cat of ['MRTStation','BusStop'])assert(r.criteria.some(c=>c.target?.category===cat&&c.strength==='hard'&&c.value===800));
    for(const c of base.criteria.filter(c=>!c.target?.category))assert.deepEqual(r.criteria.find(x=>x.criterion_id===c.criterion_id),c);
  }},
  {id:'cancel-one-amenity',query:'独卫不强求了，其他要求都保留。',base:{...base,criteria:[...base.criteria,criterion('amenities','eq','private_bathroom',undefined,false,{criterion_id:'private'}),criterion('amenities','eq','pet_policy',undefined,false,{criterion_id:'pets'})]},check:r=>same(r,{...base,criteria:[...base.criteria,criterion('amenities','eq','pet_policy',undefined,false,{criterion_id:'pets'})]})},
  {id:'explicit-reset',query:'之前所有条件都取消，重新来：只要求每月租金不超过1800新币。',check:r=>same(r,{listing_type:'RENT',criteria:[criterion('price','lte',1800,'SGD/month')]})},
];
const files=['src/agents/requirement/extractor.ts','src/agents/requirement/audit-context.ts','src/agents/requirement/prompt.ts','src/agents/requirement/few-shot.ts','skills/rental-requirement-intake/SKILL.md','src/domain/workflow.ts'];
const hashes=Object.fromEntries(await Promise.all(files.map(async f=>[f,createHash('sha256').update(await readFile(f)).digest('hex')])));
const results:any[]=[];let next=0;
async function worker(){while(next<cases.length){
  const c=cases[next++],before=structuredClone(c.base||base),events:IntakeAttempt[]=[],started=Date.now();
  const ctx=workflow.context(randomUUID(),{operation_id:randomUUID(),agent:'a'} as any);
  try{
    const draft=await extractRequirements(c.query,before,model,skill,{
      prepareDraft:async draft=>{await (workflow as any).resolveRequirementLocations(draft,before,ctx);return {draft,effective_requirements:mergeRequirements(before,draft)};},
      onAttempt:async e=>{events.push(structuredClone(e));},
    });
    assert.equal(draft.clarifications.length,0);const effective=mergeRequirements(before,draft);c.check(effective);assert.deepEqual(before,c.base||base);
    results.push({id:c.id,query:c.query,passed:true,draft,effective,events,duration_ms:Date.now()-started});
  }catch(e){results.push({id:c.id,query:c.query,passed:false,error:e instanceof Error?e.message:String(e),events,duration_ms:Date.now()-started});}
  console.log(JSON.stringify({id:c.id,passed:results.at(-1).passed,attempts:events.length}));
}}
await Promise.all([worker(),worker()]);
for(const f of files)assert.equal(createHash('sha256').update(await readFile(f)).digest('hex'),hashes[f],'Source changed during evaluation');
const report={status:results.every(r=>r.passed)?'passed':'failed',checked_at:new Date().toISOString(),model:process.env.CHAT_MODEL,hashes,cases:results.length,passed:results.filter(r=>r.passed).length,results};
const reportName=process.argv[2]||'requirement-update-model';
await writeFile(`evaluation/reports/${reportName}.json`,JSON.stringify(report,null,2));
console.log(JSON.stringify({status:report.status,cases:report.cases,passed:report.passed}));
if(report.status!=='passed')process.exitCode=1;
