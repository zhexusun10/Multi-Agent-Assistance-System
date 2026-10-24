/** Repair a completed recommendation's failed explanation from saved facts.
 * Dry run unless --apply; never searches, changes requirements, or calls a model. */
import pg from 'pg';
import {writeFile,mkdir} from 'node:fs/promises';
import {DomainStore} from '../src/stores/domain.js';
import {PostgresAgentTaskStore} from '../src/agent-task-store.js';
import {PostgresSessionEventStore} from '../src/session-store.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {recoverExplanation} from '../src/agents/explanation/recovery.js';
import {buildExplanation} from '../src/domain/explanation.js';
import {displayedCandidates} from '../src/domain/selection.js';
import type {ExplanationEnrichment} from '../src/agents/explanation/enrichment.js';

const operationId=process.argv[2],apply=process.argv.includes('--apply');
if(!/^[0-9a-f-]{36}$/.test(operationId||''))throw Error('Usage: recover-saved-explanation.ts <operation UUID> [--apply]');
const pool=new pg.Pool({connectionString:process.env.DATABASE_URL,max:2});
const store=new DomainStore(pool,new PostgresAgentTaskStore(pool),new PostgresSessionEventStore(pool));
try{
 const report=await store.transaction(async tx=>{
  const op=(await tx.query('SELECT * FROM domain_operations WHERE id=$1 FOR UPDATE',[operationId])).rows[0];
  if(!op)throw Error('Operation missing');
  const state=await store.lock(tx,op.session_id);
  if(op.status==='completed'&&(state.explanation as any)?.generation?.recovered_operation_id===operationId)return {status:'already_recovered',model_calls:0};
  if(op.status!=='failed'||state.active_operation_id!==operationId)throw Error('Only the current failed operation may be repaired');
  if((await tx.query("SELECT 1 FROM agent_tasks WHERE session_id=$1 AND status IN ('queued','running')",[op.session_id])).rowCount)throw Error('Session has active work');
  const call=(await tx.query('SELECT * FROM recommendation_calls WHERE operation_id=$1 AND id=$2',[operationId,state.latest_request_id])).rows[0];
  if(!call?.response||call.response.request_id!==state.recommendation?.request_id)throw Error('Accepted recommendation is missing or stale');
  const task=(await tx.query("SELECT id,error FROM agent_tasks WHERE session_id=$1 AND agent='c' AND status='failed' AND metadata->>'operation_id'=$2 ORDER BY created_at DESC LIMIT 1",[op.session_id,operationId])).rows[0];
  if(!task)throw Error('Failed explanation task missing');
  const rec=call.response,req=call.request.requirements,ids=new Set(displayedCandidates(rec).map(c=>c.listing.listing_id));
  const saved=(await tx.query('SELECT ref,body FROM domain_payloads WHERE operation_id=$1 ORDER BY created_at',[operationId])).rows;
  const extra=saved.filter(p=>p.body.kind==='explanation_enrichment_v1'&&ids.has(p.body.listing_id)).map(p=>({...p.body,source_ref:p.ref})) as ExplanationEnrichment[];
  const archive=saved.find(p=>p.body.provider&&p.body.response?.request_id===rec.request_id)?.body;
  const bundle=addDecisionSupport(makeExplanationBundle(req,rec,op.query,archive),req,rec,extra),narrative=recoverExplanation(bundle,req,rec);
  const explanation={...buildExplanation(req,rec,rec.state.coverage.dataset_version),question:op.query,summary:narrative.paragraphs.map(p=>p.text).join('\n\n'),narrative,generation:{version:'3.1',mode:'evidence_recovery',semantic_audit:'not_run',deterministic_validation:'passed',recovered_operation_id:operationId,enrichment_refs:extra.map(e=>e.source_ref)}};
  if(apply){
   await store.payload(tx,op.session_id,operationId,{kind:'saved_explanation_recovery_v1',failed_task_id:task.id,original_error:task.error,source_request_id:rec.request_id,model_calls:0});
   state.explanation=explanation;
   const last=state.messages.at(-1);
   if(last?.role==='assistant'&&last.text.startsWith('本轮未完成：推荐理由'))last.text=explanation.summary;
   else store.message(state,'assistant',explanation.summary);
   await store.saveState(tx,state);
   await tx.query("UPDATE domain_operations SET status='completed' WHERE id=$1",[operationId]);
   await store.event(tx,op.session_id,{type:'workflow_result',payload:{explanation,source_request_id:rec.request_id,recovered:true}},'saved-explanation-recovery:'+operationId);
  }
  return {status:apply?'recovered':'dry_run_passed',operation_id:operationId,model_calls:0,visible_count:narrative.focus_listing_ids.length,summary:explanation.summary};
 });
 await mkdir('evaluation/reports',{recursive:true});await writeFile('evaluation/reports/refinement-recovery.json',JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify(report));
}finally{await pool.end();}
