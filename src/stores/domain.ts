import { createHash, randomUUID } from 'node:crypto';
import {readFile} from 'node:fs/promises';
import type { Pool, PoolClient } from 'pg';
import type { Assignment, MasterEvent } from '../types.js';
import { PostgresAgentTaskStore } from '../agent-task-store.js';
import { PostgresSessionEventStore } from '../session-store.js';
import type { RecommendationResponse, Requirements } from '../contracts/recommendation-v1.js';
import type { RequirementDraft } from '../domain/requirements.js';
import {publicRefinement} from '../contracts/requirement-refinement.js';

function canonical(value:unknown):unknown{if(Array.isArray(value))return value.map(canonical);if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>[k,canonical(v)]));return value;}
export const hash=(value:unknown)=>createHash('sha256').update(typeof value==='string'?value:JSON.stringify(canonical(value))).digest('hex');
export class BusinessError extends Error{constructor(public code:string,message:string,public status=409){super(message);}}
export type DomainState={session_id:string;owner_hash:string;deleted_at?:string|null;requirements_version:number;requirements:Requirements;active_operation_id:string|null;latest_request_id:string|null;recommendation:RecommendationResponse|null;recommendation_cold_start:{exited:boolean;exited_pool_key:string|null}|null;explanation:unknown;pending:RequirementDraft|null;revision:number;messages:{role:string;text:string;at:string;knowledge?:import('../domain/knowledge.js').KnowledgeResult;refinement_id?:string}[]};
export type TaskEnvelope={schema_version:'1.1';agent:'a'|'b'|'c'|'d';operation_id:string;requirements_version:number;input_ref:string;input_hash:string};
export class DomainStore{
  constructor(readonly pool:Pool,readonly tasks:PostgresAgentTaskStore,readonly events:PostgresSessionEventStore){}
  async setup(){for(const name of ['001-business.sql','002-followup.sql','003-manual-followup.sql','004-browser-interest.sql','005-requirement-refinement.sql','006-session-behavior-context.sql','007-recommendation-cold-start.sql'])await this.pool.query(await readFile(new URL('../../migrations/'+name,import.meta.url),'utf8'));await this.pool.query("INSERT INTO conversation_log(id,session_id,role,body,at) SELECT md5(s.session_id||m::text),s.session_id,m->>'role',m->>'text',(m->>'at')::timestamptz FROM session_domain_state s CROSS JOIN LATERAL jsonb_array_elements(s.messages) m ON CONFLICT DO NOTHING");}
  async transaction<T>(work:(tx:PoolClient)=>Promise<T>):Promise<T>{const tx=await this.pool.connect();try{await tx.query('BEGIN');const result=await work(tx);await tx.query('COMMIT');return result;}catch(e){await tx.query('ROLLBACK');throw e;}finally{tx.release();}}
  async create(owner:string){const id=randomUUID();await this.pool.query('INSERT INTO session_domain_state(session_id,owner_hash) VALUES($1,$2)',[id,hash(owner)]);return id;}
  async assertOwner(sessionId:string,owner:string){const r=await this.pool.query('SELECT owner_hash,deleted_at FROM session_domain_state WHERE session_id=$1',[sessionId]);if(!r.rows[0]||r.rows[0].owner_hash!==hash(owner))throw new BusinessError('SESSION_FORBIDDEN','无法访问该会话',403);if(r.rows[0].deleted_at)throw new BusinessError('SESSION_DELETED','该对话已删除',410);}
  async lock(tx:PoolClient,id:string):Promise<DomainState>{const r=await tx.query('SELECT * FROM session_domain_state WHERE session_id=$1 FOR UPDATE',[id]);if(!r.rows[0])throw new BusinessError('SESSION_NOT_FOUND','会话不存在',404);if(r.rows[0].deleted_at)throw new BusinessError('SESSION_DELETED','该对话已删除',410);return r.rows[0];}
  async deleteSession(id:string,owner:string){return this.transaction(async tx=>{
    const row=(await tx.query('SELECT owner_hash,deleted_at FROM session_domain_state WHERE session_id=$1 FOR UPDATE',[id])).rows[0];
    if(!row||row.owner_hash!==hash(owner))throw new BusinessError('SESSION_FORBIDDEN','无法访问该会话',403);
    if(row.deleted_at)return {status:'deleted',session_id:id};
    await tx.query('UPDATE session_domain_state SET deleted_at=clock_timestamp(),active_operation_id=NULL,revision=revision+1 WHERE session_id=$1',[id]);
    await tx.query("UPDATE domain_operations SET status='superseded' WHERE session_id=$1 AND status='queued'",[id]);
    await tx.query("UPDATE agent_tasks SET status='failed',error='SESSION_DELETED',delivered_at=now(),updated_at=now() WHERE session_id=$1 AND (status IN ('queued','running') OR delivered_at IS NULL)",[id]);
    await tx.query("UPDATE message_outbox SET state='CANCELLED',updated_at=now() WHERE session_id=$1 AND state='QUEUED'",[id]);
    await tx.query("UPDATE followup_tasks SET status='CANCELLED' WHERE session_id=$1 AND status IN ('OPEN','DUE')",[id]);
    await tx.query('DELETE FROM browser_reading_leases WHERE segment_id IN (SELECT segment_id FROM listing_view_segments WHERE session_id=$1)',[id]);
    return {status:'deleted',session_id:id};
  });}
  async state(id:string){return this.transaction(async tx=>{
    await tx.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');
    const result=await tx.query('SELECT * FROM session_domain_state WHERE session_id=$1',[id]);
    if(!result.rows[0])throw new BusinessError('SESSION_NOT_FOUND','会话不存在',404);
    if(result.rows[0].deleted_at)throw new BusinessError('SESSION_DELETED','该对话已删除',410);
    const s=result.rows[0] as DomainState;
    const cur=await tx.query('SELECT last_event_id FROM session_event_counters WHERE session_id=$1',[id]);
    const actions=await tx.query('SELECT body FROM workflow_actions WHERE session_id=$1 ORDER BY updated_at',[id]);
    const artifacts=await tx.query('SELECT id,source_request_id,requirements_version,sha256,created_at FROM workflow_artifacts WHERE session_id=$1 ORDER BY created_at',[id]);
    const recOp=s.recommendation?(await tx.query('SELECT operation_id FROM recommendation_calls WHERE id=$1',[s.recommendation.request_id])).rows[0]?.operation_id:null;
    // Return active work in the same snapshot as messages, so the browser can
    // recover a lost completion notification without submitting another query.
    const pending=await tx.query("SELECT request_id,kind FROM domain_operations WHERE session_id=$1 AND status='queued' ORDER BY created_at",[id]);
    const refinements=await tx.query('SELECT * FROM requirement_refinement_plans WHERE session_id=$1 ORDER BY created_at',[id]);
    const {owner_hash,...safe}=s;
    return {...safe,refinement_plans:refinements.rows.map(publicRefinement),recommendation_operation_id:recOp,last_event_id:Number(cur.rows[0]?.last_event_id||0),pending_operations:pending.rows,actions:actions.rows.map(x=>x.body),artifacts:artifacts.rows};
  });}
  async saveState(tx:PoolClient,s:DomainState){s.revision++;await tx.query("INSERT INTO conversation_log(id,session_id,role,body,at) SELECT md5($1||m::text),$1,m->>'role',m->>'text',(m->>'at')::timestamptz FROM jsonb_array_elements($2::jsonb) m ON CONFLICT DO NOTHING",[s.session_id,JSON.stringify(s.messages)]);await tx.query(`UPDATE session_domain_state SET requirements_version=$2,requirements=$3,active_operation_id=$4,latest_request_id=$5,recommendation=$6,explanation=$7,pending=$8,revision=$9,messages=$10,recommendation_cold_start=$11 WHERE session_id=$1`,[s.session_id,s.requirements_version,JSON.stringify(s.requirements),s.active_operation_id,s.latest_request_id,JSON.stringify(s.recommendation),JSON.stringify(s.explanation),JSON.stringify(s.pending),s.revision,JSON.stringify(s.messages.slice(-200)),s.recommendation_cold_start==null?null:JSON.stringify(s.recommendation_cold_start)]);}
  async payload(tx:PoolClient,sessionId:string,operationId:string,body:unknown){const ref=randomUUID();await tx.query('INSERT INTO domain_payloads(ref,session_id,operation_id,body,sha256) VALUES($1,$2,$3,$4,$5)',[ref,sessionId,operationId,JSON.stringify(body),hash(body)]);return ref;}
  async savePayload(sessionId:string,operationId:string,body:unknown){return this.transaction(tx=>this.payload(tx,sessionId,operationId,body));}
  async loadPayload(ref:string,sessionId:string){const r=await this.pool.query('SELECT body FROM domain_payloads WHERE ref=$1 AND session_id=$2',[ref,sessionId]);if(!r.rows[0])throw new BusinessError('EVIDENCE_NOT_FOUND','证据不可访问',404);return r.rows[0].body;}
  async queue(tx:PoolClient,s:DomainState,op:string,agent:TaskEnvelope['agent'],body:unknown,step:string):Promise<Assignment[]>{
    const ref=await this.payload(tx,s.session_id,op,body);const env:TaskEnvelope={schema_version:'1.1',agent,operation_id:op,requirements_version:s.requirements_version,input_ref:ref,input_hash:hash(body)};
    const assignment={agent,prompt:JSON.stringify(env)};const tasks=await this.tasks.enqueueInTx(tx,s.session_id,[assignment],{stepKey:`${op}:${step}`,metadata:env});
    if(tasks.length)await this.event(tx,s.session_id,{type:'stage',payload:{agent,operation_id:op,status:'queued'}},`stage:${op}:${step}`);
    return tasks.length?[assignment]:[];
  }
  async verifyTask(serialized:string,sessionId:string,taskId:string,agent:string){
    const env=JSON.parse(serialized) as TaskEnvelope;
    const r=await this.pool.query('SELECT metadata,status,agent,session_id,prompt FROM agent_tasks WHERE id=$1',[taskId]);const row=r.rows[0];
    if(!row||row.session_id!==sessionId||row.agent!==agent||row.status!=='running'||row.prompt!==serialized||hash(row.metadata)!==hash(env)||env.agent!==agent||env.schema_version!=='1.1')throw new BusinessError('TASK_IDENTITY_INVALID','任务身份校验失败');
    const body=await this.loadPayload(env.input_ref,sessionId);if(hash(body)!==env.input_hash)throw Error('TASK_INPUT_HASH_MISMATCH');return {env,body};
  }
  async event(tx:PoolClient,sessionId:string,event:MasterEvent,key:string){await this.events.appendInTx(tx,sessionId,event,key);}
  message(s:DomainState,role:string,text:string,knowledge?:import('../domain/knowledge.js').KnowledgeResult){s.messages.push({role,text,at:new Date().toISOString(),...(knowledge?{knowledge}:{})});}
}
