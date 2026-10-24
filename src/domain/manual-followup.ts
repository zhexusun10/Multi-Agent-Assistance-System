import {randomUUID} from 'node:crypto';
import {z} from 'zod';
import type {PoolClient} from 'pg';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {BusinessError,DomainStore,hash,type DomainState} from '../stores/domain.js';
import {ActionService,wireBody,actionHash,type Action} from './actions.js';
import {analyzeReply,REPLY_ANALYSIS_VERSION,type ReplyAnalysis} from './reply-analysis.js';
import {isLegacyGeneratedMessage} from '../shared/followup-message.js';

const version=z.object({revision:z.number().int().positive()}).strict();
export class ManualFollowupService{
  constructor(readonly store:DomainStore,readonly model?:BaseChatModel){}
  private async history(a:Action,db:Pick<PoolClient,'query'>=this.store.pool,beforeId?:string){
    const rows=(await db.query(`SELECT r.* FROM manual_followup_replies r JOIN workflow_actions a ON a.id=r.action_id
      WHERE r.session_id=$1 AND a.body->>'source_request_id'=$2 AND a.body->>'requirements_version'=$3
      AND a.body->'listing_ids'=$4::jsonb AND ($5::uuid IS NULL OR (r.created_at,r.id)<=(SELECT created_at,id FROM manual_followup_replies WHERE id=$5::uuid))
      ORDER BY r.created_at DESC,r.id DESC LIMIT 51`,[a.session_id,a.source_request_id,String(a.requirements_version),JSON.stringify(a.listing_ids),beforeId||null])).rows.reverse();
    if(rows.length>50||rows.reduce((n,r)=>n+r.text.length,0)>64000)throw new BusinessError('REPLY_HISTORY_LIMIT',"This home has a long reply history. Organise the conversation before continuing so answered conditions are not missed.");
    return rows;
  }
  private async referenceMatches(a:Action,db:Pick<PoolClient,'query'>=this.store.pool){
    const rec=(await db.query('SELECT response FROM recommendation_calls WHERE id=$1 AND session_id=$2',[a.source_request_id,a.session_id])).rows[0]?.response;
    const items=[...(rec?.recommendations||[]),...(rec?.unverified_candidates||[])].filter(c=>a.listing_ids.includes(c.listing.listing_id));
    return items.length===a.listing_ids.length?items[0].criterion_results.filter((r:any)=>r.status==='pass'&&items.every(c=>c.criterion_results.some((v:any)=>v.criterion_id===r.criterion_id&&v.status==='pass'))).map((r:any)=>r.criterion_id):[];
  }
  /** All draft entry points share the same answered-topic memory, scoped to this user, listing and search version. */
  async draftFromHistory(s:Omit<DomainState,'owner_hash'>,ids:string[],type:'MESSAGE'|'VIEWING',notes=''){
    const draft=new ActionService(this.store).draft(s,ids,type,notes),rows=await this.history(draft),latest=rows.at(-1);
    if(!latest)return draft;
    if(latest.analysis_state==='READY'&&latest.analysis?.analysis_version!==REPLY_ANALYSIS_VERSION)await this.analyze(draft.session_id,latest.id,true);
    const row=(await this.store.pool.query('SELECT analysis,analysis_state FROM manual_followup_replies WHERE id=$1',[latest.id])).rows[0];
    if(row?.analysis_state!=='READY'||row.analysis?.analysis_version!==REPLY_ANALYSIS_VERSION)throw new BusinessError('ANALYSIS_PENDING',"The latest agent reply is being analysed. Wait before drafting to avoid repeating answered questions.");
    draft.body=(row.analysis as ReplyAnalysis).suggested_reply;draft.subject=type==='VIEWING'?"Continue arranging a viewing":"Follow up on the agent’s reply";draft.content_hash=actionHash(draft);return draft;
  }
  private async action(tx:PoolClient,session:string,id:string){const row=(await tx.query('SELECT body FROM workflow_actions WHERE id=$1 AND session_id=$2 FOR UPDATE',[id,session])).rows[0];if(!row)throw new BusinessError('ACTION_NOT_FOUND',"Follow-up record not found",404);return row.body as Action;}
  private async editable(tx:PoolClient,s:DomainState,a:Action,revision:number){
    if(a.revision!==revision)throw new BusinessError('REVISION_CONFLICT',"The draft has changed. Review and copy it again.");
    if(!['DRAFT','PENDING_REVIEW','APPROVED'].includes(a.status))throw new BusinessError('INVALID_STATUS',"This draft was submitted or cancelled. Check the sending record.");
    const op=(await tx.query('SELECT operation_id FROM recommendation_calls WHERE id=$1',[a.source_request_id])).rows[0]?.operation_id;
    if(s.latest_request_id!==a.source_request_id||s.requirements_version!==a.requirements_version||op!==s.active_operation_id)throw new BusinessError('ACTION_STALE',"Your requirements or recommendations have changed. Create a new draft from the current results.");
  }
  private async log(tx:PoolClient,a:Action,kind:string,body:unknown,key:string){await tx.query('INSERT INTO followup_events(session_id,action_id,kind,body,event_key) VALUES($1,$2,$3,$4,$5) ON CONFLICT(event_key) DO NOTHING',[a.session_id,a.id,kind,JSON.stringify(body),key]);await this.store.event(tx,a.session_id,{type:'action_update',payload:{action_id:a.id,kind}},key);}
  async prepare(session:string,id:string,input:unknown){
    const v=version.parse(input);return this.store.transaction(async tx=>{const s=await this.store.lock(tx,session),a=await this.action(tx,session,id);await this.editable(tx,s,a,v.revision);
      if(isLegacyGeneratedMessage(a.body))throw new BusinessError('DRAFT_REWRITE_REQUIRED',"This draft uses an earlier format. Rewrite it as a natural message, save it and copy it again.");
      if(a.action_type==='VIEWING'&&(!a.slots||Date.parse(a.slots.starts_at)<=Date.now()||Date.parse(a.slots.ends_at)<=Date.parse(a.slots.starts_at)||Date.parse(a.slots.ends_at)-Date.parse(a.slots.starts_at)>4*3600000))throw new BusinessError('INVALID_SLOTS',"Edit and save future viewing start/end times, lasting no more than four hours.");
      const prepared={revision:a.revision,body:wireBody(a),content_hash:hash({revision:a.revision,body:wireBody(a)}),requirements_snapshot:structuredClone(s.requirements),prepared_at:new Date().toISOString()};
      a.manual_prepared=prepared;await tx.query('UPDATE workflow_actions SET body=$2,updated_at=now() WHERE id=$1',[id,JSON.stringify(a)]);
      return {revision:prepared.revision,body:prepared.body,content_hash:prepared.content_hash,notice:"Copying does not send the message. Check the recipient, send it yourself on the external platform, then confirm."};
    });
  }
  async sent(session:string,id:string,input:unknown){
    const v=z.object({revision:z.number().int().positive(),content_hash:z.string().regex(/^[a-f0-9]{64}$/),confirmed_sent:z.literal(true)}).strict().parse(input);
    return this.store.transaction(async tx=>{const s=await this.store.lock(tx,session),a=await this.action(tx,session,id);
      if(a.manual){if(a.manual.revision===v.revision&&a.manual.content_hash===v.content_hash)return a;throw new BusinessError('REVISION_CONFLICT',"The sending snapshot differs from the current record.");}
      if(a.revision!==v.revision)throw new BusinessError('REVISION_CONFLICT',"The draft has changed. Review and copy it again.");
      if(!['DRAFT','PENDING_REVIEW','APPROVED'].includes(a.status))throw new BusinessError('INVALID_STATUS',"This action cannot currently be marked as sent by you.");
      const body=a.manual_prepared?.revision===v.revision&&a.manual_prepared.content_hash===v.content_hash?a.manual_prepared.body:wireBody(a);if(hash({revision:a.revision,body})!==v.content_hash)throw new BusinessError('CONTENT_CHANGED',"The message has changed. Copy and review it again.");
      if(!a.manual_prepared||a.manual_prepared.content_hash!==v.content_hash)throw new BusinessError('COPY_REQUIRED',"Open and review the full draft first.");
      // A user's acknowledgement is a correspondence record, never a provider execution/receipt.
      // Recording an already sent message remains possible if the user searched again meanwhile.
      // Its analysis must still use the reviewed copy's original requirements, not the latest search.
      a.manual={source:'user_confirmed',sent_at:new Date().toISOString(),revision:a.revision,content_hash:v.content_hash,message_body:body,requirements_snapshot:structuredClone(a.manual_prepared.requirements_snapshot)};a.mode='manual';a.status='MANUAL_SENT';a.approval=null;
      await tx.query('UPDATE workflow_actions SET body=$2,updated_at=now() WHERE id=$1',[id,JSON.stringify(a)]);
      if(a.next_followup_at&&Date.parse(a.next_followup_at)>Date.now())await tx.query("INSERT INTO followup_tasks(id,session_id,action_id,kind,due_at,note) VALUES($1,$2,$3,'await_reply',$4,'Check for the agent’s reply (message sent manually by you)')",[randomUUID(),session,id,a.next_followup_at]);
      await this.log(tx,a,'manual_sent',{source:'user_confirmed',text:body,revision:a.revision,meaning:"You confirmed sending it on the external platform; no delivery receipt is available."},`manual-sent:${id}:${a.revision}`);return a;
    });
  }
  async receive(session:string,id:string,input:unknown){
    const v=z.object({reply_id:z.string().uuid(),text:z.string().trim().min(1).max(8000),confirmed_received:z.literal(true)}).strict().parse(input);
    return this.store.transaction(async tx=>{await this.store.lock(tx,session);const a=await this.action(tx,session,id);
      const existing=(await tx.query('SELECT * FROM manual_followup_replies WHERE id=$1',[v.reply_id])).rows[0];
      if(existing){if(existing.session_id!==session||existing.action_id!==id||existing.text!==v.text)throw new BusinessError('REPLY_ID_CONFLICT',"The reply ID is already in use. Refresh and try again.");return existing;}
      if(!a.manual||!['MANUAL_SENT','MANUAL_REPLIED','CONFIRMED'].includes(a.status))throw new BusinessError('MANUAL_SEND_REQUIRED',"Review the draft and confirm you sent it first.");
      const row=(await tx.query('INSERT INTO manual_followup_replies(id,session_id,action_id,text) VALUES($1,$2,$3,$4) RETURNING *',[v.reply_id,session,id,v.text])).rows[0];
      if(a.status!=='CONFIRMED')a.status='MANUAL_REPLIED';await tx.query('UPDATE workflow_actions SET body=$2,updated_at=now() WHERE id=$1',[id,JSON.stringify(a)]);
      await tx.query("UPDATE followup_tasks SET status='DONE',revision=revision+1 WHERE action_id=$1 AND kind='await_reply' AND status IN ('OPEN','DUE')",[id]);
      await this.log(tx,a,'manual_reply',{reply_id:v.reply_id,source:'user_pasted',text:v.text,meaning:"You pasted and confirmed a reply. It awaits analysis and does not automatically verify conditions or bookings."},`manual-reply:${v.reply_id}`);return row;
    });
  }
  async analyze(session:string,replyId:string,retry=false){
    const job=await this.store.transaction(async tx=>{await this.store.lock(tx,session);const row=(await tx.query('SELECT * FROM manual_followup_replies WHERE id=$1 AND session_id=$2 FOR UPDATE',[replyId,session])).rows[0];if(!row)throw new BusinessError('REPLY_NOT_FOUND',"Reply not found",404);
      if(row.analysis_state==='PROCESSING'&&Date.now()-new Date(row.analysis_started_at).getTime()<90000||row.analysis_state==='READY'&&row.analysis?.analysis_version===REPLY_ANALYSIS_VERSION&&!retry)return null;
      const a=await this.action(tx,session,row.action_id);const revision=row.analysis_revision+1;
      // Compare timestamps inside PostgreSQL: JavaScript Date truncates its microsecond precision.
      const history=await this.history(a,tx,row.id),reference_matches=await this.referenceMatches(a,tx);
      await tx.query("UPDATE manual_followup_replies SET analysis_state='PROCESSING',analysis_started_at=now(),analysis_revision=$2 WHERE id=$1",[replyId,revision]);return {row,a,revision,text:history.map(r=>r.text).join('\n'),reference_matches};
    });if(!job)return;
    const analysis=await analyzeReply(job.text,job.a.manual!.requirements_snapshot,this.model,{reference_matches:job.reference_matches});
    await this.store.transaction(async tx=>{const s=await this.store.lock(tx,session);const updated=await tx.query("UPDATE manual_followup_replies SET analysis=$3,analysis_state='READY' WHERE id=$1 AND analysis_revision=$2 AND analysis_state='PROCESSING' RETURNING id",[replyId,job.revision,JSON.stringify(analysis)]);if(!updated.rowCount)return;
      this.store.message(s,'assistant',`Agent reply analysis (pasted by you, listing ${job.a.listing_ids.join(", ")})
${analysis.summary}

Next steps:
${analysis.next_steps.map(x=>'• '+x).join('\n')}\n${analysis.disclaimer}${analysis.model_status==='failed'?"\nThe model was unavailable. The original reply is retained and conservative rules were used. Retry analysis in Follow-up.":''}`);await this.store.saveState(tx,s);
      await this.log(tx,job.a,'reply_analysis',{reply_id:replyId,analysis},`reply-analysis:${replyId}:${job.revision}`);
    });
  }
  async recover(){const rows=(await this.store.pool.query("SELECT id,session_id FROM manual_followup_replies WHERE analysis_state='PENDING' OR (analysis_state='PROCESSING' AND analysis_started_at<now()-interval '90 seconds') ORDER BY created_at LIMIT 5")).rows;await Promise.allSettled(rows.map(r=>this.analyze(r.session_id,r.id)));}
  async nextDraft(session:string,id:string,input:unknown){
    const requested=z.object({reply_id:z.string().uuid()}).strict().parse(input),old=(await this.store.pool.query('SELECT analysis,analysis_state FROM manual_followup_replies WHERE id=$1 AND session_id=$2 AND action_id=$3',[requested.reply_id,session,id])).rows[0];
    if(old?.analysis_state==='READY'&&old.analysis?.analysis_version!==REPLY_ANALYSIS_VERSION)await this.analyze(session,requested.reply_id,true);
    const v=z.object({reply_id:z.string().uuid()}).strict().parse(input);return this.store.transaction(async tx=>{const s=await this.store.lock(tx,session),a=await this.action(tx,session,id);const row=(await tx.query('SELECT * FROM manual_followup_replies WHERE id=$1 AND session_id=$2 AND action_id=$3 FOR UPDATE',[v.reply_id,session,id])).rows[0];if(!row?.analysis)throw new BusinessError('ANALYSIS_REQUIRED',"Complete reply analysis first.");
      if(row.analysis_state!=='READY'||row.analysis.analysis_version!==REPLY_ANALYSIS_VERSION)throw new BusinessError('ANALYSIS_PENDING',"The latest reply is being analysed. Continue drafting afterwards.");
      if(a.requirements_version!==s.requirements_version||a.source_request_id!==s.latest_request_id)throw new BusinessError('ACTION_STALE',"The reply concerns earlier requirements. Draft from the current list to avoid using outdated conditions.");
      const history=await this.history(a,tx);if(history.at(-1)?.id!==row.id)throw new BusinessError('REPLY_SUPERSEDED',"A newer reply is available for this home. Continue drafting from the latest reply.");
      const analysis=row.analysis as ReplyAnalysis;
      if(row.next_action_id){const existing=await this.action(tx,session,row.next_action_id);
        // Repair an untouched generated draft; never overwrite a user-edited or sent message.
        if(existing.status==='DRAFT'&&existing.revision===1&&!existing.manual_prepared&&existing.body!==analysis.suggested_reply){existing.body=analysis.suggested_reply;existing.revision++;existing.content_hash=actionHash(existing);await tx.query('UPDATE workflow_actions SET body=$2,updated_at=now() WHERE id=$1',[existing.id,JSON.stringify(existing)]);await this.log(tx,existing,'manual_next_draft',{reply_id:v.reply_id,meaning:"The unedited follow-up draft has been updated using the latest analysis. It has not been sent."},`manual-next-repair:${existing.id}:${existing.revision}`);}
        return existing;}
      const draft=new ActionService(this.store).draft(s,a.listing_ids,'MESSAGE');draft.body=analysis.suggested_reply;draft.subject="Follow up on the agent’s reply";draft.recipient=structuredClone(a.recipient);draft.content_hash=actionHash(draft);
      await tx.query('INSERT INTO workflow_actions(id,session_id,body) VALUES($1,$2,$3)',[draft.id,session,JSON.stringify(draft)]);await tx.query('UPDATE manual_followup_replies SET next_action_id=$2 WHERE id=$1',[v.reply_id,draft.id]);await this.log(tx,draft,'manual_next_draft',{reply_id:v.reply_id,meaning:"The next enquiry has been drafted, not sent."},`manual-next:${v.reply_id}`);return draft;
    });
  }
}
