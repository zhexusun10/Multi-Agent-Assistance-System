import {randomUUID,createHmac,timingSafeEqual} from 'node:crypto';
import type {PoolClient} from 'pg';
import {z} from 'zod';
import {DomainStore,BusinessError,hash,type DomainState} from '../stores/domain.js';
import {MessagingGateway,DeliveryError,normalizeAddress,type Channel,type SendMessage} from '../integrations/messaging.js';
import {wireBody,actionHash,type Action,type DeliveryQueue} from './actions.js';
import {escapeHtml,criterionText} from './explanation.js';

export const singaporeDate=(d=new Date())=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Singapore',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);
const equal=(a:string,b:string)=>{const x=Buffer.from(a),y=Buffer.from(b);return x.length===y.length&&timingSafeEqual(x,y);};
const stateOrder:Record<string,number>={QUEUED:0,SENDING:1,ACCEPTED:2,DELIVERED:3,READ:4,REPLIED:5,CONFIRMED:6};
export class FollowupService implements DeliveryQueue{
  private working=false;
  private polling=false;
  constructor(readonly store:DomainStore,readonly gateway=new MessagingGateway()){}
  async pollTelegram(){
    if(this.polling||this.gateway.env.TELEGRAM_RECEIVE_MODE!=='polling'||!this.gateway.env.TELEGRAM_BOT_TOKEN)return;
    this.polling=true;try{await this.store.transaction(async tx=>{
      const key='telegram:'+hash(this.gateway.env.TELEGRAM_BOT_TOKEN!);
      const locked=(await tx.query('SELECT pg_try_advisory_xact_lock(hashtext($1)) AS ok',[key])).rows[0].ok;if(!locked)return;
      const offset=Number((await tx.query('SELECT last_update FROM integration_offsets WHERE integration=$1',[key])).rows[0]?.last_update??-1);
      const updates=await this.gateway.telegramUpdates(offset+1);let last=offset;
      for(const u of updates){if(!Number.isSafeInteger(u.update_id))continue;await this.telegramUpdate(u);last=Math.max(last,u.update_id);}
      await tx.query('INSERT INTO integration_offsets(integration,last_update) VALUES($1,$2) ON CONFLICT(integration) DO UPDATE SET last_update=EXCLUDED.last_update',[key,last]);
    });}finally{this.polling=false;}
  }
  private async telegramUpdate(body:any){const m=body.message;if(Number.isSafeInteger(body.update_id)&&m?.chat?.id&&typeof m.text==='string')await this.inbound('telegram',String(m.chat.id),String(m.message_id),m.reply_to_message?.message_id?.toString()||null,m.text,`tg:${body.update_id}`);}
  async snapshot(session:string){
    const [tasks,events,artifacts,replies]=await Promise.all([
      this.store.pool.query('SELECT * FROM followup_tasks WHERE session_id=$1 ORDER BY due_at LIMIT 200',[session]),
      this.store.pool.query('SELECT id,action_id,kind,body,created_at FROM followup_events WHERE session_id=$1 ORDER BY id DESC LIMIT 200',[session]),
      this.store.pool.query('SELECT id,source_request_id,requirements_version,sha256,created_at,title,artifact_type FROM workflow_artifacts WHERE session_id=$1 ORDER BY created_at',[session]),
      this.store.pool.query('SELECT * FROM manual_followup_replies WHERE session_id=$1 ORDER BY created_at DESC LIMIT 200',[session])]);
    return {followup_tasks:tasks.rows,followup_events:events.rows,artifacts:artifacts.rows,manual_replies:replies.rows};
  }
  async log(tx:PoolClient,session:string,action:string,kind:string,body:unknown,key?:string){
    await tx.query('INSERT INTO followup_events(session_id,action_id,kind,body,event_key) VALUES($1,$2,$3,$4,$5) ON CONFLICT(event_key) DO NOTHING',[session,action,kind,JSON.stringify(body),key||null]);
    await this.store.event(tx,session,{type:'action_update',payload:{action_id:action,kind}},key||`followup:${randomUUID()}`);
  }
  private async message(tx:PoolClient,a:Action):Promise<SendMessage>{
    const address=a.recipient.channel&&a.recipient.address?normalizeAddress(a.recipient.channel,a.recipient.address):'';
    const last=(await tx.query('SELECT max(provider_at) AS at FROM messaging_inbound WHERE channel=$1 AND address=$2',[a.recipient.channel,address])).rows[0]?.at;
    return {recipient:a.recipient,body:wireBody(a),...(a.template?{template:a.template}:{}),lastInboundAt:last?new Date(last).toISOString():null};
  }
  async queue(tx:PoolClient,s:DomainState,a:Action,key:string){
    const message=await this.message(tx,a);await this.gateway.validate(message);
    await tx.query('INSERT INTO message_outbox(execution_key,action_id,session_id,channel,address,payload) VALUES($1,$2,$3,$4,$5,$6)',[key,a.id,s.session_id,a.recipient.channel,normalizeAddress(a.recipient.channel!,a.recipient.address!),JSON.stringify({message,revision:a.revision,hash:actionHash(a)})]);
  }
  /** At most one provider attempt. An interrupted or timed-out attempt needs human reconciliation. */
  async processOutbox(){
    if(this.working)return;this.working=true;
    try{
      const expired=(await this.store.pool.query("SELECT execution_key,session_id FROM message_outbox WHERE state='SENDING' AND started_at<now()-interval '60 seconds' LIMIT 20")).rows;
      for(const j of expired)await this.finish(j.execution_key,j.session_id,'UNKNOWN',null,'INTERRUPTED_SEND');
      if(this.gateway.capabilities().mode!=='live')return;
      const candidates=(await this.store.pool.query("SELECT execution_key,session_id FROM message_outbox WHERE state='QUEUED' ORDER BY created_at LIMIT 10")).rows;
      for(const job of candidates){
        const claimed=await this.store.transaction(async tx=>{
          const s=await this.store.lock(tx,job.session_id);
          const j=(await tx.query("SELECT * FROM message_outbox WHERE execution_key=$1 AND state='QUEUED' FOR UPDATE SKIP LOCKED",[job.execution_key])).rows[0];if(!j)return null;
          const a=(await tx.query('SELECT body FROM workflow_actions WHERE id=$1 FOR UPDATE',[j.action_id])).rows[0].body as Action;
          const op=(await tx.query('SELECT operation_id FROM recommendation_calls WHERE id=$1',[a.source_request_id])).rows[0]?.operation_id;
          if(a.status!=='QUEUED'||s.latest_request_id!==a.source_request_id||s.requirements_version!==a.requirements_version||op!==s.active_operation_id||actionHash(a)!==j.payload.hash||a.approval?.hash!==j.payload.hash){
            await tx.query("UPDATE message_outbox SET state='CANCELLED',error_code='STALE_BEFORE_SEND',updated_at=now() WHERE execution_key=$1",[j.execution_key]);a.status='CANCELLED';await tx.query('UPDATE workflow_actions SET body=$2 WHERE id=$1',[a.id,JSON.stringify(a)]);await tx.query('DELETE FROM viewing_reservations WHERE execution_key=$1',[j.execution_key]);await this.log(tx,s.session_id,a.id,'cancelled',{reason:"Requirements, approval or selection changed; not sent"});return null;
          }
          await tx.query("UPDATE message_outbox SET state='SENDING',started_at=now(),updated_at=now() WHERE execution_key=$1",[j.execution_key]);a.status='SENDING';await tx.query('UPDATE workflow_actions SET body=$2 WHERE id=$1',[a.id,JSON.stringify(a)]);await this.log(tx,s.session_id,a.id,'sending',{mode:'live'});return {a,j};
        });
        if(!claimed)continue;
        try{const message=await this.store.transaction(tx=>this.message(tx,claimed.a));const ack=await this.gateway.send(message);await this.finish(job.execution_key,job.session_id,'ACCEPTED',ack.message_id);}
        catch(e){await this.finish(job.execution_key,job.session_id,e instanceof DeliveryError?e.outcome:e instanceof BusinessError?'FAILED':'UNKNOWN',null,e instanceof DeliveryError?e.code:e instanceof BusinessError?e.code:'SEND_RESULT_UNKNOWN');}
      }
    }finally{this.working=false;}
  }
  private async finish(key:string,session:string,status:string,messageId:string|null,error?:string){
    await this.store.transaction(async tx=>{
      await this.store.lock(tx,session);const j=(await tx.query('SELECT * FROM message_outbox WHERE execution_key=$1 FOR UPDATE',[key])).rows[0];if(!j||j.state!=='SENDING')return;
      await tx.query('UPDATE message_outbox SET state=$2,provider_message_id=$3,error_code=$4,updated_at=now() WHERE execution_key=$1',[key,status,messageId,error||null]);
      const a=(await tx.query('SELECT body FROM workflow_actions WHERE id=$1 FOR UPDATE',[j.action_id])).rows[0].body as Action;if((stateOrder[a.status]??-1)<=(stateOrder[status]??1))a.status=status;if(messageId)a.execution={external_id:messageId,mode:'live',executed_at:new Date().toISOString()};
      await tx.query('UPDATE workflow_actions SET body=$2,updated_at=now() WHERE id=$1',[a.id,JSON.stringify(a)]);
      if(status==='FAILED')await tx.query('DELETE FROM viewing_reservations WHERE execution_key=$1',[key]);
      await this.log(tx,session,a.id,'provider_result',{status,message_id:messageId,error_code:error||null,meaning:status==='ACCEPTED'?"Accepted by platform; this does not establish delivery, reading or a confirmed booking":status==='UNKNOWN'?"Result unknown. Do not resend automatically; check the platform record":"Submission failed"});
      // An unusually fast webhook may precede persistence of the provider message id.
      const inbound=(await tx.query('SELECT event_key,body FROM messaging_inbound WHERE channel=$1 AND address=$2 AND reply_to=$3 AND session_id IS NULL',[j.channel,j.address,messageId])).rows;
      for(const m of inbound){await tx.query('UPDATE messaging_inbound SET action_id=$2,session_id=$3 WHERE event_key=$1',[m.event_key,a.id,session]);await this.reply(tx,a,m.body,m.event_key);}
    });
    if(messageId){const receipts=(await this.store.pool.query('SELECT channel,body FROM messaging_status_receipts WHERE message_id=$1 AND processed=false',[messageId])).rows;for(const r of receipts)await this.delivery(r.channel,r.body);}
  }
  verifyChallenge(query:URLSearchParams){const e=this.gateway.env;if(!e.WHATSAPP_VERIFY_TOKEN||query.get('hub.mode')!=='subscribe'||!equal(query.get('hub.verify_token')||'',e.WHATSAPP_VERIFY_TOKEN))throw new BusinessError('WEBHOOK_AUTH',"Webhook verification failed",403);return query.get('hub.challenge')||'';}
  async webhook(channel:Channel,raw:Buffer,signature:string){
    const e=this.gateway.env;
    if(channel==='whatsapp'){
      if(!e.WHATSAPP_APP_SECRET||!equal(signature,'sha256='+createHmac('sha256',e.WHATSAPP_APP_SECRET).update(raw).digest('hex')))throw new BusinessError('WEBHOOK_AUTH',"Invalid webhook signature",403);
    }else if(!e.TELEGRAM_WEBHOOK_SECRET||!equal(signature,e.TELEGRAM_WEBHOOK_SECRET))throw new BusinessError('WEBHOOK_AUTH',"Invalid webhook secret",403);
    let body:any;try{body=JSON.parse(raw.toString('utf8'));}catch{throw new BusinessError('INVALID_JSON',"Invalid webhook JSON",400);}
    if(channel==='telegram'){
      await this.telegramUpdate(body);
    }else{
      if(body.object!=='whatsapp_business_account')throw new BusinessError('WEBHOOK_OBJECT',"Unsupported WhatsApp webhook object",400);
      for(const entry of body.entry||[])for(const change of entry.changes||[]){const v=change.value;if(String(v?.metadata?.phone_number_id)!==e.WHATSAPP_PHONE_NUMBER_ID)continue;
        for(const m of v.messages||[])if(m.type==='text'&&typeof m.text?.body==='string')await this.inbound(channel,normalizeAddress(channel,m.from),m.id,m.context?.id||null,m.text.body,`wa:${m.id}`,Number(m.timestamp));
        for(const st of v.statuses||[])if(['sent','delivered','read','failed'].includes(st.status))await this.delivery(channel,st);
      }
    }
    return {received:true};
  }
  private async inbound(channel:Channel,address:string,messageId:string,replyTo:string|null,text:string,key:string,providerTimestamp?:number){
    await this.store.transaction(async tx=>{
      // Reply id is strongest. Without it, associate only a single unambiguous action across all owners.
      const matches=(await tx.query(`SELECT DISTINCT action_id,session_id FROM message_outbox WHERE channel=$1 AND address=$2 AND state IN ('SENDING','ACCEPTED','DELIVERED','READ','REPLIED') ${replyTo?'AND provider_message_id=$3':''} ORDER BY action_id LIMIT 2`,replyTo?[channel,address,replyTo]:[channel,address])).rows;
      const target=matches.length===1?matches[0]:null;
      if(target)await this.store.lock(tx,target.session_id);
      const timestamp=providerTimestamp&&Number.isFinite(providerTimestamp)&&providerTimestamp>0&&providerTimestamp*1000<=Date.now()+60000?new Date(providerTimestamp*1000).toISOString():null;
      const inserted=await tx.query('INSERT INTO messaging_inbound(event_key,channel,address,provider_message_id,reply_to,body,action_id,session_id,provider_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING RETURNING event_key',[key,channel,address,messageId,replyTo,text.slice(0,8000),target?.action_id||null,target?.session_id||null,timestamp]);
      if(inserted.rowCount&&target){const a=(await tx.query('SELECT body FROM workflow_actions WHERE id=$1 FOR UPDATE',[target.action_id])).rows[0].body as Action;await this.reply(tx,a,text.slice(0,8000),key);}
    });
  }
  private async reply(tx:PoolClient,a:Action,text:string,key:string){
    if(a.status!=='CONFIRMED')a.status='REPLIED';await tx.query('UPDATE workflow_actions SET body=$2,updated_at=now() WHERE id=$1',[a.id,JSON.stringify(a)]);
    await tx.query("UPDATE followup_tasks SET status='DONE',revision=revision+1 WHERE action_id=$1 AND kind='await_reply' AND status IN ('OPEN','DUE')",[a.id]);
    await this.log(tx,a.session_id,a.id,'reply',{text,meaning:"Reply received; listing requirements and viewing arrangements still need manual confirmation"},`reply:${key}`);
  }
  private async delivery(channel:Channel,status:any){
    const receiptKey=`delivery:${channel}:${status.id}:${status.status}`;await this.store.pool.query('INSERT INTO messaging_status_receipts(event_key,channel,message_id,body) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING',[receiptKey,channel,String(status.id),JSON.stringify(status)]);
    const matches=(await this.store.pool.query('SELECT * FROM message_outbox WHERE channel=$1 AND provider_message_id=$2',[channel,status.id])).rows;if(matches.length!==1)return;
    const j=matches[0];await this.store.transaction(async tx=>{
      await this.store.lock(tx,j.session_id);const a=(await tx.query('SELECT body FROM workflow_actions WHERE id=$1 FOR UPDATE',[j.action_id])).rows[0].body as Action;
      const target=({sent:'ACCEPTED',delivered:'DELIVERED',read:'READ',failed:'FAILED'} as Record<string,string>)[status.status];
      if((stateOrder[target]??-1)>(stateOrder[a.status]??-1)||(target==='FAILED'&&['ACCEPTED','SENDING'].includes(a.status)))a.status=target;
      await tx.query('UPDATE workflow_actions SET body=$2,updated_at=now() WHERE id=$1',[a.id,JSON.stringify(a)]);await this.log(tx,a.session_id,a.id,'delivery',{status:target,provider_timestamp:status.timestamp||null},receiptKey);await tx.query('UPDATE messaging_status_receipts SET processed=true WHERE event_key=$1',[receiptKey]);
    });
  }
  async task(session:string,input:unknown){
    const v=z.object({action_id:z.string().uuid(),id:z.string().uuid().optional(),revision:z.number().int().positive().optional(),kind:z.enum(['await_reply','viewing_reminder','general']).default('await_reply'),due_at:z.string().datetime({offset:true}).optional(),note:z.string().max(1000).default(''),status:z.enum(['OPEN','DONE','CANCELLED']).default('OPEN')}).strict().parse(input);
    if(v.status==='OPEN'&&(!v.due_at||Date.parse(v.due_at)<=Date.now()))throw new BusinessError('FOLLOWUP_DATE',"Reminder time must be later than the current server time.",422);
    return this.store.transaction(async tx=>{
      await this.store.lock(tx,session);const action=(await tx.query('SELECT body FROM workflow_actions WHERE id=$1 AND session_id=$2',[v.action_id,session])).rows[0];if(!action)throw new BusinessError('ACTION_NOT_FOUND',"Action not found",404);
      const id=v.id||randomUUID();if(v.id){const r=await tx.query('UPDATE followup_tasks SET due_at=COALESCE($4,due_at),kind=$5,note=$6,status=$7,revision=revision+1 WHERE id=$1 AND session_id=$2 AND action_id=$8 AND revision=$3 RETURNING *',[id,session,v.revision,v.due_at,v.kind,v.note,v.status,v.action_id]);if(!r.rowCount)throw new BusinessError('REVISION_CONFLICT',"The reminder has changed. Refresh it.");}
      else await tx.query('INSERT INTO followup_tasks(id,session_id,action_id,kind,due_at,note,status) VALUES($1,$2,$3,$4,$5,$6,$7)',[id,session,v.action_id,v.kind,v.due_at,v.note,v.status]);
      await this.log(tx,session,v.action_id,'task_update',{id,status:v.status,due_at:v.due_at});return {id};
    });
  }
  async reminders(){
    const due=(await this.store.pool.query("SELECT id,session_id FROM followup_tasks WHERE status IN ('OPEN','DUE') AND due_at<=now() AND reminded_revision<revision LIMIT 100")).rows;
    for(const row of due)await this.store.transaction(async tx=>{
      const s=await this.store.lock(tx,row.session_id),t=(await tx.query("SELECT * FROM followup_tasks WHERE id=$1 AND reminded_revision<revision AND status IN ('OPEN','DUE') FOR UPDATE",[row.id])).rows[0];if(!t)return;
      await tx.query("UPDATE followup_tasks SET status='DUE',reminded_revision=revision WHERE id=$1",[t.id]);this.store.message(s,'assistant',`Follow-up reminder: ${t.note||"Check the agent’s reply or prepare the next enquiry draft"} (action ${t.action_id}). Reminders do not send external messages automatically.`);await this.store.saveState(tx,s);await this.log(tx,s.session_id,t.action_id,'reminder',{task_id:t.id,note:t.note},`task-reminder:${t.id}:${t.revision}`);
    });
  }
  async calendar(session:string,month:string){
    if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(month))throw new BusinessError('MONTH_INVALID',"The month must use YYYY-MM format",422);
    const rows=(await this.store.pool.query("SELECT a.body FROM workflow_actions a JOIN session_domain_state s ON s.session_id=a.session_id WHERE s.owner_hash=(SELECT owner_hash FROM session_domain_state WHERE session_id=$1) AND a.body->'slots' IS NOT NULL AND a.body->>'status' NOT IN ('CANCELLED','FAILED') ORDER BY a.updated_at",[session])).rows;
    return {now:new Date().toISOString(),today:singaporeDate(),time_zone:'Asia/Singapore',month,items:rows.map(r=>r.body as Action).filter(a=>a.slots&&singaporeDate(new Date(a.slots.starts_at)).startsWith(month)).map(a=>({id:a.id,session_id:a.session_id,subject:a.subject,listing_ids:a.listing_ids,slots:a.slots,status:a.status,mode:a.mode,confirmed:a.status==='CONFIRMED'}))};
  }
  async daily(session:string,date:string,client?:PoolClient){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(date)||Number.isNaN(Date.parse(date+'T00:00:00+08:00'))||singaporeDate(new Date(date+'T00:00:00+08:00'))!==date)throw new BusinessError('DATE_INVALID',"Invalid date",422);
    const work=async(tx:PoolClient)=>{
      const s=await this.store.lock(tx,session);
      const messages=(await tx.query("SELECT role,body,at FROM conversation_log WHERE session_id=$1 AND (at AT TIME ZONE 'Asia/Singapore')::date=$2::date ORDER BY at,id",[session,date])).rows;
      const events=(await tx.query("SELECT kind,body,action_id,created_at FROM followup_events WHERE session_id=$1 AND (created_at AT TIME ZONE 'Asia/Singapore')::date=$2::date ORDER BY id",[session,date])).rows;
      const actions=(await tx.query('SELECT body FROM workflow_actions WHERE session_id=$1 ORDER BY updated_at',[session])).rows.map(r=>r.body as Action),tasks=(await tx.query("SELECT * FROM followup_tasks WHERE session_id=$1 AND status IN ('OPEN','DUE') ORDER BY due_at",[session])).rows;
      const lines=[`# ${date} conversation and follow-up summary`,'',`Time zone: Asia/Singapore; generated at ${new Date().toISOString()}`,`Conversation: ${session}`,'',"## Conversation that day (original text, not factual verification)",...messages.map(m=>`- ${new Date(m.at).toISOString()} ${m.role}: ${m.body}`),'',"## Current requirements snapshot",...s.requirements.criteria.map(c=>'- '+criterionText(c)),'',"## Current follow-up state snapshot",...actions.flatMap(a=>[`- ${a.subject}; listing ${a.listing_ids.join(', ')}; status ${a.status}; mode ${a.mode}`,`  Requests for discussion: ${a.negotiation_notes||"See message body"}; ${a.slots?"Proposed time: "+a.slots.starts_at+" to "+a.slots.ends_at:"No viewing time"}`,`  To confirm: ${a.verification_context?.flatMap(v=>v.unresolved.map(u=>u.requirement)).join("; ")||"Current availability still needs confirmation"}`]),'',"## Events and incoming messages that day",...events.map(e=>`- ${new Date(e.created_at).toISOString()} ${e.kind} (${e.action_id}): ${JSON.stringify(e.body)}`),'',"## Current open reminders",...tasks.map(t=>`- ${new Date(t.due_at).toISOString()} ${t.kind} ${t.note} (${t.status})`),'',"## Record limitations","- Conversations and events use Singapore dates; requirements, actions and open reminders reflect the generation-time snapshot.","- Acceptance, delivery, replies and agreement are recorded separately. A reply does not automatically verify listing requirements.","- Earlier versions retained only the latest 200 messages. Previously discarded history cannot be restored."];
      const markdown=lines.join('\n'),document={markdown,html:`<!doctype html><html lang="en-SG"><meta charset="utf-8"><title>Conversation and follow-up summary</title><style>body{max-width:960px;margin:40px auto;font:16px/1.8 system-ui;padding:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>${escapeHtml(date)} conversation and follow-up summary</h1><pre>${escapeHtml(markdown)}</pre></html>`,model:{source_request_id:s.latest_request_id,date,session_id:session},template_version:'daily-followup-v1'};
      const id=randomUUID();await tx.query("INSERT INTO workflow_artifacts(id,session_id,source_request_id,requirements_version,content,sha256,title,artifact_type) VALUES($1,$2,$3,$4,$5,$6,$7,'daily')",[id,session,s.latest_request_id,s.requirements_version,JSON.stringify(document),hash(document),date+" conversation and follow-up summary"]);await this.store.event(tx,session,{type:'workflow_result',payload:{artifact_id:id}},`daily:${id}`);return {id};
    };return client?work(client):this.store.transaction(work);
  }
}
