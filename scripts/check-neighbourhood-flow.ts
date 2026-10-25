/** Default: real local data, no model. --live: ONE case, at most three model
 * requests across intake/writing/review, no SDK retry, no resumable paid suite. */
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import pg from 'pg';
import {SystemMessage,HumanMessage,AIMessage} from '@langchain/core/messages';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {createModel} from '../src/model.js';
import {ToolExecutor,type ToolContext} from '../src/tools/executor.js';
import {DataHttpAdapter} from '../src/adapters/data-http.js';
import {LocationService} from '../src/adapters/locations.js';
import {callNative} from '../src/adapters/native-recommendation.js';
import {recommendationRequestSchema,type Requirements} from '../src/contracts/recommendation-v1.js';
import {criterion,mergeRequirements} from '../src/domain/requirements.js';
import {WorkflowService} from '../src/domain/workflow.js';
import {INTAKE_EXAMPLES,selectIntakeExamples} from '../src/agents/requirement/few-shot.js';
import {modelIntakeSchema} from '../src/agents/requirement/contracts.js';
import {compileIntake} from '../src/agents/requirement/validation.js';
import {intakeSystemPrompt} from '../src/agents/requirement/prompt.js';
import {ExplanationEnricher} from '../src/agents/explanation/enrichment.js';
import {generateExplanation} from '../src/agents/explanation/generator.js';
import {loadNeighbourhoods} from '../src/adapters/neighbourhoods.js';
import {geodesic} from '../src/domain/spatial.js';

const live=process.argv.includes('--live'),path=live?'evaluation/reports/neighbourhood-live.json':'evaluation/reports/neighbourhood-local.json';
const continueExplanation=process.argv.includes('--continue-explanation');
let modelCalls=0;const report:any={status:'running',model_calls:0,model_request_cap:live?3:0,checks:[]};
const saveReport=async()=>{report.model_calls=modelCalls;await writeFile(path,JSON.stringify(report,null,2)+'\n');};
await mkdir('evaluation/reports',{recursive:true});
if(live){
 const old=await readFile(path,'utf8').then(JSON.parse).catch(()=>null);
 if(old?.model_calls){
  if(!continueExplanation||old.model_calls!==1||!/^401/.test(old.error||''))throw Error('Paid representative already executed; do not rerun automatically');
  modelCalls=1;report.previous_attempt={requests:1,error_code:401,reason:'parent shell credential differed from project configuration'};
 }
}
const pool=new pg.Pool({connectionString:process.env.DATABASE_URL});
try{
 const source=(await pool.query("SELECT o.query,s.requirements FROM domain_operations o JOIN session_domain_state s USING(session_id) WHERE o.query='那改为：帮我找荷兰村附近的房子' ORDER BY o.created_at DESC LIMIT 1")).rows[0];
 const example=INTAKE_EXAMPLES.find(e=>e.id==='short-replace-neighbourhood')!;
 const query=source?.query||example.query,base:Requirements=source?.requirements||example.base;
 const context:ToolContext={sessionId:randomUUID(),operationId:randomUUID(),agent:'a',dataset:process.env.DATASET_VERSION||'2026-10-09',deadline:Date.now()+240000,calls:0};
 const payloads=new Map<string,unknown>(),save=async(_ctx:ToolContext,body:unknown)=>{const id=randomUUID();payloads.set(id,body);return id;};
 const tools=new ToolExecutor(),data=new DataHttpAdapter(process.env.DATA_API_URL||'http://127.0.0.1:8089',tools,save),locations=new LocationService(data,tools,save);
 let model:BaseChatModel|undefined;
 if(live){
  const actual=await createModel(undefined,{maxTokens:4096,temperature:0,maxRetries:0});
  model={withStructuredOutput:(schema:unknown,options:any)=>{
   const runnable=actual.withStructuredOutput(schema as any,options);
   return {invoke:async(...args:any[])=>{
    if(modelCalls>=3)throw Error('REPRESENTATIVE_MODEL_BUDGET_EXHAUSTED');
    modelCalls++;await saveReport();
    return (runnable.invoke as any)(...args);
   }};
  }} as unknown as BaseChatModel;
 }
 let proposal:unknown;
 if(model&&!continueExplanation){
  const skill=await readFile('skills/rental-requirement-intake/SKILL.md','utf8'),examples=selectIntakeExamples(query);
  proposal=await model.withStructuredOutput(modelIntakeSchema,{name:'submit_requirement_intake_v2',method:'functionCalling',strict:true}).invoke([
   new SystemMessage(intakeSystemPrompt(base,skill)),...examples.flatMap(e=>[new HumanMessage(JSON.stringify({query:e.query,base_requirements:e.base})),new AIMessage(JSON.stringify(e.output))]),new HumanMessage(JSON.stringify({query,base_requirements:base})),
  ],{signal:AbortSignal.timeout(30000)});
 }else{
  // An offline semantic proposal, explicitly marked as a fixture, not LLM proof.
  proposal={...example.output,removals:base.criteria.filter(c=>c.field==='place_proximity'&&c.target?.place_id).map(c=>({field:'place_proximity',criterion_id:c.criterion_id,quote:query}))};
 }
 const draft=compileIntake(proposal,query,base,{attempts:1,example_ids:selectIntakeExamples(query).map(e=>e.id)});
 await (WorkflowService.prototype as any).resolveRequirementLocations.call({locations},draft,base,context);
 assert.equal(draft.clarifications.length,0);
 const effective=mergeRequirements(base,draft);
 assert(effective.criteria.some(c=>c.target?.place_id==='OSM:node:8777049478'));
 assert(!effective.criteria.some(c=>c.target?.place_id?.startsWith('LTA:')));
 assert(!effective.criteria.some(c=>c.target?.place_id==='OSM:node:2511049756'));
 assert.deepEqual(effective.criteria.filter(c=>c.field!=='place_proximity'),base.criteria.filter(c=>c.field!=='place_proximity'));
 report.checks.push({check:'actual_location_replacement',passed:true,model_proposed:live&&!continueExplanation,requirements:effective});
 const catalogue=await loadNeighbourhoods();
 for(const e of catalogue.entities){
  const aContext={...context,agent:'a' as const},alias=String(e.properties.name_local);
  assert.equal((await locations.resolve(aContext,alias)).entity?.entity_id,e.entity_id);
  assert.equal((await locations.resolve(aContext,String(e.properties.name))).entity?.entity_id,e.entity_id);
  const requirements:Requirements={listing_type:'RENT',criteria:[criterion('price','lte',3000,'SGD/month'),criterion('bedrooms','eq',1,'count'),criterion('place_proximity','lte',1000,'m',true,{target:{place_id:e.entity_id,name:String(e.properties.name)},distance_type:'straight_line',distance_preference:'nearer'})]};
  const req=recommendationRequestSchema.parse({contract_version:'1.0',conversation_id:randomUUID(),request_id:randomUUID(),requirements_version:1,result_limit:4,requirements});
  const ctx={...context,sessionId:req.conversation_id,agent:'b' as const};
  const rec=await callNative(req,ctx,process.env.RECOMMENDATION_API_URL||'http://127.0.0.1:8102',save);
  assert.equal(rec.native?.status,'success');assert.equal(rec.recommendations.length,4);
  const distances=rec.recommendations.map(row=>{
   const observation=row.criterion_results.find(r=>r.criterion_id.startsWith('place_proximity'))!;
   assert.notEqual(observation.status,'unknown');assert.equal(typeof observation.observed_value,'number');return Number(observation.observed_value);
  });
  assert(distances.every((d,i)=>i===0||d>=distances[i-1]));
  for(const row of rec.recommendations){
   const p=(await data.listing(ctx,row.listing.listing_id)).value;
   const d=geodesic(Number(p.latitude),Number(p.longitude),Number(e.properties.latitude),Number(e.properties.longitude));
   const observed=Number(row.criterion_results.find(r=>r.criterion_id.startsWith('place_proximity'))!.observed_value);
   assert(Math.abs(d-observed)<1,'independent straight-line distance disagrees');
  }
  report.checks.push({check:'native_neighbourhood_ranking',place_id:e.entity_id,passed:true,ids:rec.recommendations.map(r=>r.listing.listing_id),distances_m:distances});
  if(live&&e.entity_id==='OSM:node:8777049478'){
   const before=JSON.stringify(rec),cContext={...context,sessionId:req.conversation_id,agent:'c' as const};
   const enrichment=await new ExplanationEnricher(data,tools,save).load(rec,cContext);
   const explanation=await generateExplanation(requirements,rec,context.dataset,query,model,await readFile('skills/evidence-based-explanation/SKILL.md','utf8'),payloads.get(rec.native!.exchange_ref),enrichment);
   assert.equal(JSON.stringify(rec),before);
   report.checks.push({check:'real_explanation_writer_and_semantic_review',passed:true,summary:explanation.summary,generation:explanation.generation});
  }
 }
 report.status='passed';
}catch(error){report.status='failed';report.error=(error as any)?.status===401?'401 authentication failed':error instanceof Error?error.message:String(error);throw error;}
finally{await saveReport();await pool.end();console.log(JSON.stringify({status:report.status,model_calls:modelCalls,report:path}));}
