/** Saved-result replay only. --live permits ONE writer + ONE reviewer request,
 * zero SDK retries, no recommendation/intake calls. --apply replaces only the
 * current fallback introduction after successful model review; sources stay intact. */
import pg from 'pg';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {createModel} from '../src/model.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {recoverExplanation} from '../src/agents/explanation/recovery.js';
import {generateExplanation} from '../src/agents/explanation/generator.js';
import {displayedCandidates} from '../src/domain/selection.js';
import {DomainStore} from '../src/stores/domain.js';
import {PostgresAgentTaskStore} from '../src/agent-task-store.js';
import {PostgresSessionEventStore} from '../src/session-store.js';
import type {ExplanationEnrichment} from '../src/agents/explanation/enrichment.js';

const operationId=process.argv[2],live=process.argv.includes('--live'),apply=process.argv.includes('--apply');
if(!/^[0-9a-f-]{36}$/.test(operationId||'')||apply&&!live)throw Error('Usage: check-explanation-style.ts <operation UUID> [--live [--apply]]');
const path=`evaluation/reports/explanation-style-${live?'live':'local'}.json`;
await mkdir('evaluation/reports',{recursive:true});
if(live&&(await readFile(path,'utf8').then(JSON.parse).catch(()=>null))?.model_calls)throw Error('Paid representative already attempted; do not automatically rerun');
const pool=new pg.Pool({connectionString:process.env.DATABASE_URL,max:2});
const store=new DomainStore(pool,new PostgresAgentTaskStore(pool),new PostgresSessionEventStore(pool));
let calls=0;const report:any={operation_id:operationId,status:'running',model_calls:0,model_request_cap:live?2:0,requests:[]};
const persist=()=>writeFile(path,JSON.stringify({...report,model_calls:calls},null,2)+'\n');
try{
 const op=(await pool.query('SELECT * FROM domain_operations WHERE id=$1',[operationId])).rows[0];if(!op)throw Error('Saved operation not found');
 const call=(await pool.query('SELECT request,response FROM recommendation_calls WHERE operation_id=$1 ORDER BY created_at DESC LIMIT 1',[operationId])).rows[0];
 if(!call?.response)throw Error('Saved recommendation missing');
 const rec=call.response,req=call.request.requirements,ids=new Set(displayedCandidates(rec).map(r=>r.listing.listing_id));
 const saved=(await pool.query('SELECT ref,body FROM domain_payloads WHERE operation_id=$1 ORDER BY created_at',[operationId])).rows;
 const enrichment=saved.filter(p=>p.body.kind==='explanation_enrichment_v1'&&ids.has(p.body.listing_id)).map(p=>({...p.body,source_ref:p.ref})) as ExplanationEnrichment[];
 const archive=saved.find(p=>p.body.provider&&p.body.response?.request_id===rec.request_id)?.body;
 const before=JSON.stringify({req,rec,enrichment});
 const b=addDecisionSupport(makeExplanationBundle(req,rec,op.query,archive),req,rec,enrichment);
 const recovered=recoverExplanation(b,req,rec);report.visible_count=recovered.focus_listing_ids.length;report.local_replay=recovered;
 if(live){
  const writer=await createModel(undefined,{maxTokens:4096,temperature:0,maxRetries:0});
  const reviewer=await createModel(undefined,{maxTokens:1200,temperature:0,maxRetries:0});
  const model={withStructuredOutput:(schema:any,options:any)=>{
   const runnable=(options.name.startsWith('audit_')?reviewer:writer).withStructuredOutput(schema,options);
   return {invoke:async(messages:any,config:any)=>{
    if(calls>=2)throw Error('EXPLANATION_STYLE_TEST_BUDGET_EXHAUSTED');
    calls++;report.requests.push({role:options.name,started_at:new Date().toISOString()});await persist();
    const value=await runnable.invoke(messages,config);report.requests.at(-1).output=value;await persist();return value;
   }};
  }} as unknown as BaseChatModel;
  const result=await generateExplanation(req,rec,rec.state.coverage.dataset_version,op.query,model,await readFile('skills/evidence-based-explanation/SKILL.md','utf8'),archive,enrichment,async d=>{report.failure=d;return 'offline-style-diagnostic';});
  report.generation=result.generation;report.summary=result.summary;
  assert.equal(result.generation.mode,'llm_grounded','Real model prose must pass; a fallback is not a successful style test');
  assert.equal(result.generation.semantic_audit,'passed');
  if(apply)await store.transaction(async tx=>{
   const state=await store.lock(tx,op.session_id);
   if(state.active_operation_id!==operationId||state.latest_request_id!==rec.request_id||state.recommendation?.request_id!==rec.request_id)throw Error('Current recommendation changed; do not replace its introduction');
   if((state.explanation as any)?.generation?.mode!=='evidence_recovery')throw Error('Only the current fallback may be replaced');
   if((await tx.query("SELECT 1 FROM agent_tasks WHERE session_id=$1 AND status IN ('queued','running')",[op.session_id])).rowCount)throw Error('Session has active work');
   const previous=state.explanation as any,last=state.messages.at(-1);
   if(last?.role!=='assistant'||last.text!==previous.summary)throw Error('Latest message changed; do not rewrite history');
   const ref=await store.payload(tx,op.session_id,operationId,{kind:'explanation_style_repair_v1',source_request_id:rec.request_id,previous,model_calls:calls});
   state.explanation={...result,generation:{...result.generation,style_repair_ref:ref}};last.text=result.summary;
   await store.saveState(tx,state);
   await store.event(tx,op.session_id,{type:'workflow_result',payload:{explanation:state.explanation,source_request_id:rec.request_id}},'explanation-style-repair:'+operationId);
   report.applied=true;
  });
 }
 assert.equal(JSON.stringify({req,rec,enrichment}),before);report.status='passed';
}catch(error){report.status='failed';report.error=error instanceof Error?error.message:String(error);throw error;}
finally{await persist();await pool.end();console.log(JSON.stringify({status:report.status,model_calls:calls,report:path,applied:report.applied??false}));}
