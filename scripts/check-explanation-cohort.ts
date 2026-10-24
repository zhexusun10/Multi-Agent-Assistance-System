/** Default is local-only. --live adds one explanation case with at most THREE
 * writer/reviewer requests, zero SDK retries and persistent rerun protection. */
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {createModel} from '../src/model.js';
import {criterion} from '../src/domain/requirements.js';
import {recommendationRequestSchema} from '../src/contracts/recommendation-v1.js';
import {nativeRequest,projectNative} from '../src/adapters/native-recommendation.js';
import {DataHttpAdapter} from '../src/adapters/data-http.js';
import {ToolExecutor,type ToolContext} from '../src/tools/executor.js';
import {ExplanationEnricher} from '../src/agents/explanation/enrichment.js';
import {generateExplanation} from '../src/agents/explanation/generator.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {displayedCandidates} from '../src/domain/selection.js';

const live=process.argv.includes('--live'),reportPath=live?'evaluation/reports/explanation31-live.json':'evaluation/reports/explanation31-local.json';
let calls=0;const report:any={status:'running',model_calls:0,model_request_cap:live?3:0,checks:[]};
const persist=async()=>{report.model_calls=calls;await writeFile(reportPath,JSON.stringify(report,null,2)+'\n');};
await mkdir('evaluation/reports',{recursive:true});await mkdir('data/.runtime',{recursive:true});
if(live&&(await readFile(reportPath,'utf8').then(JSON.parse).catch(()=>null))?.model_calls)throw Error('Do not automatically repeat this paid representative');
try{
 const request=recommendationRequestSchema.parse({contract_version:'1.0',request_id:randomUUID(),conversation_id:randomUUID(),requirements_version:1,result_limit:5,requirements:{listing_type:'RENT',criteria:[
  criterion('price','lte',3000,'SGD/month'),criterion('bedrooms','eq',1,'count'),
  criterion('place_proximity','lte',1000,'m',true,{target:{name:'Chinatown',place_id:'OSM:node:2511049756'},distance_type:'straight_line',distance_preference:'nearer'}),
 ]}});
 const input=resolve('data/.runtime/top5-input.json'),output=resolve('data/.runtime/top5-output.json');
 await writeFile(input,JSON.stringify(nativeRequest(request).request));
 await promisify(execFile)(resolve('.venv/Scripts/python.exe'),['-m','scripts.check_top5_data',input,output],{cwd:process.cwd(),timeout:120000,maxBuffer:100000});
 const envelope=JSON.parse(await readFile(output,'utf8')),ctx:ToolContext={sessionId:request.conversation_id,operationId:randomUUID(),agent:'b',dataset:process.env.DATASET_VERSION||'2026-10-09',deadline:Date.now()+240000,calls:0};
 const saved=new Map<string,unknown>(),save=async(_ctx:ToolContext,body:unknown)=>{const id=randomUUID();saved.set(id,body);return id;};
 const rec=await projectNative(request,ctx,envelope,save);assert.equal(rec.recommendations.length,5);
 const before=JSON.stringify(rec),data=new DataHttpAdapter(process.env.DATA_API_URL||'http://127.0.0.1:8089',new ToolExecutor(),save);
 const enrichment=await new ExplanationEnricher(data,new ToolExecutor(),save).load(rec,{...ctx,agent:'c'});
 const question='帮我介绍牛车水附近这几套房子，各有什么值得考虑的地方？';
 const bundle=addDecisionSupport(makeExplanationBundle(request.requirements,rec,question,saved.get(rec.native!.exchange_ref)),request.requirements,rec,enrichment);
 assert.equal(displayedCandidates(rec).length,5);assert.equal(bundle.listings.length,5);assert.equal(enrichment.length,5);
 assert(!bundle.facts.some(f=>f.ref.startsWith('unit-rent:')));
 report.checks.push({check:'single_top5_presentation_scope',passed:true,raw_formal:rec.recommendations.length,raw_pending:rec.unverified_candidates.length,visible:bundle.listings.length,enriched:enrichment.length});
 assert.deepEqual(bundle.decision_support!.target_listing_ids,rec.recommendations.map(r=>r.listing.listing_id));
 report.checks.push({check:'real_data_top5_and_overall_profiles',passed:true,native_model_calls:envelope.response.execution.model_calls,ids:rec.recommendations.map(r=>r.listing.listing_id),profiles:bundle.decision_support!.profiles.slice(0,5).map(p=>({title:p.title,core_refs:p.core_refs,highlights:p.highlight_refs}))});
 if(live){
  const actual=await createModel(undefined,{maxTokens:4096,temperature:0,maxRetries:0});
  const model={withStructuredOutput:(schema:unknown,options:any)=>{const runnable=actual.withStructuredOutput(schema as any,options);return {invoke:async(...args:any[])=>{
   if(calls>=3)throw Error('EXPLANATION31_TEST_BUDGET_EXHAUSTED');calls++;await persist();return (runnable.invoke as any)(...args);
  }};}} as unknown as BaseChatModel;
  const generated=await generateExplanation(request.requirements,rec,ctx.dataset,question,model,await readFile('skills/evidence-based-explanation/SKILL.md','utf8'),saved.get(rec.native!.exchange_ref),enrichment);
  assert.equal(generated.generation.semantic_audit,'passed');
  assert.equal(generated.narrative.focus_listing_ids.length,5);
  assert(!/已收录|挂牌标注/.test(generated.summary));
  report.checks.push({check:'one_real_writer_and_review_case',passed:true,summary:generated.summary,generation:generated.generation});
 }
 assert.equal(JSON.stringify(rec),before);report.status='passed';
}catch(error){report.status='failed';report.error=(error as any)?.status===401?'401 authentication failed':error instanceof Error?error.message:String(error);if((error as any)?.diagnostic)report.diagnostic=(error as any).diagnostic;throw error;}
finally{await persist();console.log(JSON.stringify({status:report.status,model_calls:calls,report:reportPath}));}
