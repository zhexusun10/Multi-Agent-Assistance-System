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
    if(rows.length>50||rows.reduce((n,r)=>n+r.text.length,0)>64000)throw new BusinessError('REPLY_HISTORY_LIMIT','这套房源的回复记录较长，请先整理会话后继续，避免遗漏已经回答的条件。');
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
    if(row?.analysis_state!=='READY'||row.analysis?.analysis_version!==REPLY_ANALYSIS_VERSION)throw new BusinessError('ANALYSIS_PENDING','经纪人的最新回复正在理解，请完成后再起草，避免重复询问已经回答的内容。');
    draft.body=(row.analysis as ReplyAnalysis).suggested_reply;draft.subject=type==='VIEWING'?'继续协商看房':'根据经纪人回复继续咨询';draft.content_hash=actionHash(draft);return draft;
  }
  private async action(tx:PoolClient,session:string,id:string){const row=(await tx.query('SELECT body FROM workflow_actions WHERE id=$1 AND session_id=$2 FOR UPDATE',[id,session])).rows[0];if(!row)throw new BusinessError('ACTION_NOT_FOUND','跟进记录不存在',404);return row.body as Action;}
  private async editable(tx:PoolClient,s:DomainState,a:Action,revision:number){
    if(a.revision!==revision)throw new BusinessError('REVISION_CONFLICT','草稿已修改，请重新核对并复制。');
    if(!['DRAFT','PENDING_REVIEW','APPROVED'].includes(a.status))throw new BusinessError('INVALID_STATUS','这份草稿已提交或取消，请查看已有发送记录。');
    const op=(await tx.query('SELECT operation_id FROM recommendation_calls WHERE id=$1',[a.source_request_id])).rows[0]?.operation_id;
    if(s.latest_request_id!==a.source_request_id||s.requirements_version!==a.requirements_version||op!==s.active_operation_id)throw new BusinessError('ACTION_STALE','需求或推荐已变化，请基于当前结果重新生成草稿。');
  }
  private async log(tx:PoolClient,a:Action,kind:string,body:unknown,key:string){await tx.query('INSERT INTO followup_events(session_id,action_id,kind,body,event_key) VALUES($1,$2,$3,$4,$5) ON CONFLICT(event_key) DO NOTHING',[a.session_id,a.id,kind,JSON.stringify(body),key]);await this.store.event(tx,a.session_id,{type:'action_update',payload:{action_id:a.id,kind}},key);}
  async prepare(session:string,id:string,input:unknown){
    const v=version.parse(input);return this.store.transaction(async tx=>{const s=await this.store.lock(tx,session),a=await this.action(tx,session,id);await this.editable(tx,s,a,v.revision);
      if(isLegacyGeneratedMessage(a.body))throw new BusinessError('DRAFT_REWRITE_REQUIRED','这份草稿是旧格式，请先编辑并重新起草自然消息，保存后再复制。');
      if(a.action_type==='VIEWING'&&(!a.slots||Date.parse(a.slots.starts_at)<=Date.now()||Date.parse(a.slots.ends_at)<=Date.parse(a.slots.starts_at)||Date.parse(a.slots.ends_at)-Date.parse(a.slots.starts_at)>4*3600000))throw new BusinessError('INVALID_SLOTS','请先编辑并保存未来的看房起止时间（不超过4小时）。');
      const prepared={revision:a.revision,body:wireBody(a),content_hash:hash({revision:a.revision,body:wireBody(a)}),requirements_snapshot:structuredClone(s.requirements),prepared_at:new Date().toISOString()};
      a.manual_prepared=prepared;await tx.query('UPDATE workflow_actions SET body=$2,updated_at=now() WHERE id=$1',[id,JSON.stringify(a)]);
      return {revision:prepared.revision,body:prepared.body,content_hash:prepared.content_hash,notice:'复制不会发送消息。请自行核对收件人并在外部平台发送，完成后再确认。'};
    });
  }
  async sent(session:string,id:string,input:unknown){
    const v=z.object({revision:z.number().int().positive(),content_hash:z.string().regex(/^[a-f0-9]{64}$/),confirmed_sent:z.literal(true)}).strict().parse(input);
    return this.store.transaction(async tx=>{const s=await this.store.lock(tx,session),a=await this.action(tx,session,id);
      if(a.manual){if(a.manual.revision===v.revision&&a.manual.content_hash===v.content_hash)return a;throw new BusinessError('REVISION_CONFLICT','发送快照与当前记录不同。');}
      if(a.revision!==v.revision)throw new BusinessError('REVISION_CONFLICT','草稿已修改，请重新核对并复制。');
      if(!['DRAFT','PENDING_REVIEW','APPROVED'].includes(a.status))throw new BusinessError('INVALID_STATUS','当前动作不能记录为用户自行发送。');
      const body=a.manual_prepared?.revision===v.revision&&a.manual_prepared.content_hash===v.content_hash?a.manual_prepared.body:wireBody(a);if(hash({revision:a.revision,body})!==v.content_hash)throw new BusinessError('CONTENT_CHANGED','正文已变化，请重新复制并核对。');
      if(!a.manual_prepared||a.manual_prepared.content_hash!==v.content_hash)throw new BusinessError('COPY_REQUIRED','请先打开并核对完整草稿。');
      // A user's acknowledgement is a correspondence record, never a provider execution/receipt.
      // Recording an already sent message remains possible if the user searched again meanwhile.
      // Its analysis must still use the reviewed copy's original requirements, not the latest search.
      a.manual={source:'user_confirmed',sent_at:new Date().toISOString(),revision:a.revision,content_hash:v.content_hash,message_body:body,requirements_snapshot:structuredClone(a.manual_prepared.requirements_snapshot)};a.mode='manual';a.status='MANUAL_SENT';a.approval=null;
      await tx.query('UPDATE workflow_actions SET body=$2,updated_at=now() WHERE id=$1',[id,JSON.stringify(a)]);
      if(a.next_followup_at&&Date.parse(a.next_followup_at)>Date.now())await tx.query("INSERT INTO followup_tasks(id,session_id,action_id,kind,due_at,note) VALUES($1,$2,$3,'await_reply',$4,'查看经纪人是否回复（用户自行发送）')",[randomUUID(),session,id,a.next_followup_at]);
      await this.log(tx,a,'manual_sent',{source:'user_confirmed',text:body,revision:a.revision,meaning:'用户确认已在外部平台自行发送；没有平台送达回执。'},`manual-sent:${id}:${a.revision}`);return a;
    });
  }
  async receive(session:string,id:string,input:unknown){
    const v=z.object({reply_id:z.string().uuid(),text:z.string().trim().min(1).max(8000),confirmed_received:z.literal(true)}).strict().parse(input);
    return this.store.transaction(async tx=>{await this.store.lock(tx,session);const a=await this.action(tx,session,id);
      const existing=(await tx.query('SELECT * FROM manual_followup_replies WHERE id=$1',[v.reply_id])).rows[0];
      if(existing){if(existing.session_id!==session||existing.action_id!==id||existing.text!==v.text)throw new BusinessError('REPLY_ID_CONFLICT','回复编号已使用，请刷新后重试。');return existing;}
      if(!a.manual||!['MANUAL_SENT','MANUAL_REPLIED','CONFIRMED'].includes(a.status))throw new BusinessError('MANUAL_SEND_REQUIRED','请先核对草稿并确认已自行发送。');
      const row=(await tx.query('INSERT INTO manual_followup_replies(id,session_id,action_id,text) VALUES($1,$2,$3,$4) RETURNING *',[v.reply_id,session,id,v.text])).rows[0];
      if(a.status!=='CONFIRMED')a.status='MANUAL_REPLIED';await tx.query('UPDATE workflow_actions SET body=$2,updated_at=now() WHERE id=$1',[id,JSON.stringify(a)]);
      await tx.query("UPDATE followup_tasks SET status='DONE',revision=revision+1 WHERE action_id=$1 AND kind='await_reply' AND status IN ('OPEN','DUE')",[id]);
      await this.log(tx,a,'manual_reply',{reply_id:v.reply_id,source:'user_pasted',text:v.text,meaning:'用户粘贴并确认收到回复；待理解，不自动确认房源条件或预约。'},`manual-reply:${v.reply_id}`);return row;
    });
  }
  async analyze(session:string,replyId:string,retry=false){
    const job=await this.store.transaction(async tx=>{await this.store.lock(tx,session);const row=(await tx.query('SELECT * FROM manual_followup_replies WHERE id=$1 AND session_id=$2 FOR UPDATE',[replyId,session])).rows[0];if(!row)throw new BusinessError('REPLY_NOT_FOUND','回复不存在',404);
      if(row.analysis_state==='PROCESSING'&&Date.now()-new Date(row.analysis_started_at).getTime()<90000||row.analysis_state==='READY'&&row.analysis?.analysis_version===REPLY_ANALYSIS_VERSION&&!retry)return null;
      const a=await this.action(tx,session,row.action_id);const revision=row.analysis_revision+1;
      // Compare timestamps inside PostgreSQL: JavaScript Date truncates its microsecond precision.
      const history=await this.history(a,tx,row.id),reference_matches=await this.referenceMatches(a,tx);
      await tx.query("UPDATE manual_followup_replies SET analysis_state='PROCESSING',analysis_started_at=now(),analysis_revision=$2 WHERE id=$1",[replyId,revision]);return {row,a,revision,text:history.map(r=>r.text).join('\n'),reference_matches};
    });if(!job)return;
    const analysis=await analyzeReply(job.text,job.a.manual!.requirements_snapshot,this.model,{reference_matches:job.reference_matches});
    await this.store.transaction(async tx=>{const s=await this.store.lock(tx,session);const updated=await tx.query("UPDATE manual_followup_replies SET analysis=$3,analysis_state='READY' WHERE id=$1 AND analysis_revision=$2 AND analysis_state='PROCESSING' RETURNING id",[replyId,job.revision,JSON.stringify(analysis)]);if(!updated.rowCount)return;
      this.store.message(s,'assistant',`经纪人回复理解（用户粘贴，房源 ${job.a.listing_ids.join('、')}）\n${analysis.summary}\n\n下一步：\n${analysis.next_steps.map(x=>'• '+x).join('\n')}\n${analysis.disclaimer}${analysis.model_status==='failed'?'\n本次模型不可用，已保留原文并使用保守规则；可在跟进工作台重试理解。':''}`);await this.store.saveState(tx,s);
      await this.log(tx,job.a,'reply_analysis',{reply_id:replyId,analysis},`reply-analysis:${replyId}:${job.revision}`);
    });
  }
  async recover(){const rows=(await this.store.pool.query("SELECT id,session_id FROM manual_followup_replies WHERE analysis_state='PENDING' OR (analysis_state='PROCESSING' AND analysis_started_at<now()-interval '90 seconds') ORDER BY created_at LIMIT 5")).rows;await Promise.allSettled(rows.map(r=>this.analyze(r.session_id,r.id)));}
  async nextDraft(session:string,id:string,input:unknown){
    const requested=z.object({reply_id:z.string().uuid()}).strict().parse(input),old=(await this.store.pool.query('SELECT analysis,analysis_state FROM manual_followup_replies WHERE id=$1 AND session_id=$2 AND action_id=$3',[requested.reply_id,session,id])).rows[0];
    if(old?.analysis_state==='READY'&&old.analysis?.analysis_version!==REPLY_ANALYSIS_VERSION)await this.analyze(session,requested.reply_id,true);
    const v=z.object({reply_id:z.string().uuid()}).strict().parse(input);return this.store.transaction(async tx=>{const s=await this.store.lock(tx,session),a=await this.action(tx,session,id);const row=(await tx.query('SELECT * FROM manual_followup_replies WHERE id=$1 AND session_id=$2 AND action_id=$3 FOR UPDATE',[v.reply_id,session,id])).rows[0];if(!row?.analysis)throw new BusinessError('ANALYSIS_REQUIRED','请先完成回复理解。');
      if(row.analysis_state!=='READY'||row.analysis.analysis_version!==REPLY_ANALYSIS_VERSION)throw new BusinessError('ANALYSIS_PENDING','最新回复正在理解，请稍后继续起草。');
      if(a.requirements_version!==s.requirements_version||a.source_request_id!==s.latest_request_id)throw new BusinessError('ACTION_STALE','原回复基于旧需求；请回到当前房源列表起草，避免沿用旧条件。');
      const history=await this.history(a,tx);if(history.at(-1)?.id!==row.id)throw new BusinessError('REPLY_SUPERSEDED','这套房源已有较新的回复，请从最新回复继续起草。');
      const analysis=row.analysis as ReplyAnalysis;
      if(row.next_action_id){const existing=await this.action(tx,session,row.next_action_id);
        // Repair an untouched generated draft; never overwrite a user-edited or sent message.
        if(existing.status==='DRAFT'&&existing.revision===1&&!existing.manual_prepared&&existing.body!==analysis.suggested_reply){existing.body=analysis.suggested_reply;existing.revision++;existing.content_hash=actionHash(existing);await tx.query('UPDATE workflow_actions SET body=$2,updated_at=now() WHERE id=$1',[existing.id,JSON.stringify(existing)]);await this.log(tx,existing,'manual_next_draft',{reply_id:v.reply_id,meaning:'已按新版回复理解更新未编辑的后续草稿，尚未发送。'},`manual-next-repair:${existing.id}:${existing.revision}`);}
        return existing;}
      const draft=new ActionService(this.store).draft(s,a.listing_ids,'MESSAGE');draft.body=analysis.suggested_reply;draft.subject='根据经纪人回复继续咨询';draft.recipient=structuredClone(a.recipient);draft.content_hash=actionHash(draft);
      await tx.query('INSERT INTO workflow_actions(id,session_id,body) VALUES($1,$2,$3)',[draft.id,session,JSON.stringify(draft)]);await tx.query('UPDATE manual_followup_replies SET next_action_id=$2 WHERE id=$1',[v.reply_id,draft.id]);await this.log(tx,draft,'manual_next_draft',{reply_id:v.reply_id,meaning:'已起草下一步咨询，尚未发送。'},`manual-next:${v.reply_id}`);return draft;
    });
  }
}
