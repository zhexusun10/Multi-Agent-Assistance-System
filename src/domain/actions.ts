import { randomUUID } from 'node:crypto';
import { z } from 'zod';
import { BusinessError, DomainStore, hash, type DomainState } from '../stores/domain.js';
import {displayedCandidates} from './selection.js';
import {criterionText} from './explanation.js';
import type {Recipient,Template} from '../integrations/messaging.js';
import {normalizeAddress} from '../integrations/messaging.js';
import type {PoolClient} from 'pg';
import type {Requirements} from '../contracts/recommendation-v1.js';
import {composeCustomerMessage,customerNotes,formatViewingTime,isLegacyGeneratedMessage} from '../shared/followup-message.js';
import {customerQuestion} from './customer-questions.js';
import {recordViewingIntent} from './behavior-context.js';
export const actionEditSchema=z.object({revision:z.number().int().positive(),subject:z.string().min(1).max(200),body:z.string().trim().min(1).max(8000),recipient:z.object({name:z.string().min(1).max(100),email:z.union([z.string().email(),z.literal('')]).optional(),verified:z.boolean(),channel:z.enum(['whatsapp','telegram']).optional(),address:z.string().max(100).optional(),opt_in:z.boolean().optional()}).strict(),slots:z.object({starts_at:z.string().datetime({offset:true}),ends_at:z.string().datetime({offset:true}),time_zone:z.literal('Asia/Singapore')}).strict().nullable(),next_followup_at:z.string().datetime({offset:true}).nullable().optional(),negotiation_notes:z.string().max(2000).optional(),template:z.object({name:z.string().max(100),language:z.string().max(30),parameters:z.array(z.string().max(4000)).max(20)}).strict().nullable().optional()}).strict();
export type VerificationContext={listing_id:string;group:'eligible'|'unverified';unresolved:{criterion_id:string;requirement:string;evidence_ids:string[]}[]};
export type Action={id:string;session_id:string;source_request_id:string;requirements_version:number;listing_ids:string[];action_type:'MESSAGE'|'VIEWING';revision:number;subject:string;body:string;recipient:Recipient;slots:{starts_at:string;ends_at:string;time_zone:string}|null;status:string;content_hash:string;approval:{revision:number;hash:string;reviewer:string;at:string}|null;execution:{external_id:string;mode:'simulation'|'live';executed_at:string}|null;next_followup_at:string|null;confirmation?:unknown;verification_context?:VerificationContext[];mode:'simulation'|'live'|'manual';manual_prepared?:{revision:number;body:string;content_hash:string;requirements_snapshot:Requirements;prepared_at:string};manual?:{source:'user_confirmed';sent_at:string;revision:number;content_hash:string;message_body:string;requirements_snapshot:Requirements};negotiation_notes?:string;template?:Template|null};
export function actionHash(a:Action){return hash({source_request_id:a.source_request_id,requirements_version:a.requirements_version,listing_ids:a.listing_ids,action_type:a.action_type,subject:a.subject,body:a.body,recipient:a.recipient,slots:a.slots,next_followup_at:a.next_followup_at,...(a.verification_context?{verification_context:a.verification_context}:{}),...(a.negotiation_notes!==undefined?{negotiation_notes:a.negotiation_notes}:{}),...(a.template!==undefined?{template:a.template}:{})});}
export const wireBody=(a:Action)=>composeCustomerMessage(a.body,a.negotiation_notes,a.slots,!!a.template);
export interface DeliveryQueue{queue(tx:PoolClient,s:DomainState,a:Action,key:string):Promise<void>;}
export class ActionService{
  constructor(private store:DomainStore,private delivery?:DeliveryQueue){}
  draft(s:Omit<DomainState,'owner_hash'>,ids:string[],type:'MESSAGE'|'VIEWING',notes=''):Action{
    if(process.env.DATASET_VERSION&&s.recommendation?.state.coverage.dataset_version!==process.env.DATASET_VERSION)throw new BusinessError('DATASET_CHANGED','房源数据已更新，请先按当前需求重新搜索。');
    if(!s.recommendation||s.recommendation.request_id!==s.latest_request_id||s.recommendation.requirements_version!==s.requirements_version)throw new BusinessError('ACTION_STALE','请基于当前列表重新选择房源。');
    const items=ids.map(id=>displayedCandidates(s.recommendation).find(c=>c.listing.listing_id===id));if(!items.length||new Set(ids).size!==ids.length||items.some(x=>!x))throw new BusinessError('CURRENT_SELECTION_REQUIRED','请从当前已核验或待核实候选中选择房源。');
    const hard=new Set(s.requirements.criteria.filter(c=>c.strength==='hard').map(c=>c.criterion_id));if(items.some(c=>c!.criterion_results.some(r=>hard.has(r.criterion_id)&&r.status==='fail')))throw new BusinessError('HARD_FAILURE_SELECTION','当前候选明确不符合硬条件，不能沿用此推荐生成跟进。');
    const verification_context:VerificationContext[]=items.map(c=>({listing_id:c!.listing.listing_id,group:s.recommendation!.unverified_candidates.some(x=>x.listing.listing_id===c!.listing.listing_id)?'unverified':'eligible',unresolved:c!.criterion_results.filter(r=>hard.has(r.criterion_id)&&r.status==='unknown').map(r=>({criterion_id:r.criterion_id,requirement:criterionText(s.requirements.criteria.find(x=>x.criterion_id===r.criterion_id)!),evidence_ids:r.evidence_ids}))}));
    const details=items.map(c=>`${c!.listing.title}（房源 ${c!.listing.listing_id}）`).join('、');
    const price=items.length===1&&items[0]!.listing.price!==null?`我看到的月租是${items[0]!.listing.price!.toLocaleString('en-SG')}新币，现在还是这个价格吗？`:'';
    const questions=[...new Set(verification_context.flatMap(v=>v.unresolved.map(r=>customerQuestion(s.requirements.criteria.find(c=>c.criterion_id===r.criterion_id)!))))];
    const a:Action={id:randomUUID(),session_id:s.session_id,source_request_id:s.latest_request_id!,requirements_version:s.requirements_version,listing_ids:ids,verification_context,action_type:type,revision:1,subject:type==='VIEWING'?'想约时间看房':'想了解一下这套房',body:`你好，我对 ${details} 很感兴趣，请问现在还可以租吗？${price}\n\n想问一下最短可以租多久，带哪些家具，除了房租还有没有其他费用。${questions.length?'\n'+questions.join('\n'):''}${type==='VIEWING'?'\n\n我也想找个时间去看看。':''}\n\n谢谢！`,recipient:{name:'房源经纪人（待核实）',email:'',verified:false},slots:null,status:'DRAFT',content_hash:'',approval:null,execution:null,next_followup_at:null,mode:'simulation'};const clean=customerNotes(notes);if(clean)a.negotiation_notes=clean;a.content_hash=actionHash(a);return a;
  }
  private fresh(s:DomainState,a:Action){if(s.latest_request_id!==a.source_request_id||s.requirements_version!==a.requirements_version||a.listing_ids.some(id=>!displayedCandidates(s.recommendation).some(c=>c.listing.listing_id===id)))throw new BusinessError('ACTION_STALE','需求或推荐已变化，请基于当前结果重新生成草稿。');}
  async change(sessionId:string,id:string,kind:'edit'|'approve'|'execute'|'cancel'|'confirm',input:unknown,actor:string){
    return this.store.transaction(async tx=>{
      const s=await this.store.lock(tx,sessionId);const row=await tx.query('SELECT body FROM workflow_actions WHERE id=$1 AND session_id=$2 FOR UPDATE',[id,sessionId]);if(!row.rows[0])throw new BusinessError('ACTION_NOT_FOUND','动作不存在',404);const a=row.rows[0].body as Action;
      const version=z.object({revision:z.number().int().positive()}).passthrough().parse(input);if(version.revision!==a.revision)throw new BusinessError('REVISION_CONFLICT','草稿已被修改，请重新加载。');
      const executionKey=`${a.id}:${a.revision}:${a.action_type}`;
      if(kind==='execute'){
        const done=await tx.query('SELECT result FROM action_executions WHERE execution_key=$1 AND session_id=$2',[executionKey,sessionId]);if(done.rows[0])return a;
      }
      if(kind!=='cancel'&&kind!=='confirm'){
        this.fresh(s,a);
        const call=await tx.query('SELECT operation_id FROM recommendation_calls WHERE id=$1',[a.source_request_id]);
        if(call.rows[0]?.operation_id!==s.active_operation_id)throw new BusinessError('ACTION_STALE','新的搜索正在进行，请等待当前推荐后重新生成草稿。');
      }
      if(kind==='edit'){
        if(!['DRAFT','PENDING_REVIEW','APPROVED'].includes(a.status))throw new BusinessError('ACTION_IMMUTABLE','执行中或执行后的动作不能编辑。');
        const patch=actionEditSchema.parse(input);if(patch.recipient.channel&&patch.recipient.address)patch.recipient.address=normalizeAddress(patch.recipient.channel,patch.recipient.address);Object.assign(a,{subject:patch.subject,body:patch.body,recipient:patch.recipient,slots:patch.slots,next_followup_at:patch.next_followup_at??null,revision:a.revision+1,approval:null,status:'PENDING_REVIEW',...(patch.negotiation_notes!==undefined?{negotiation_notes:customerNotes(patch.negotiation_notes)}:{}),...(patch.template!==undefined?{template:patch.template}:{})});a.content_hash=actionHash(a);
      }else if(kind==='approve'){
        if(!['PENDING_REVIEW','DRAFT'].includes(a.status))throw new BusinessError('INVALID_STATUS','当前动作不可批准。');
        if(isLegacyGeneratedMessage(a.body))throw new BusinessError('DRAFT_REWRITE_REQUIRED','这份草稿是旧格式，请先编辑并重新起草自然消息，再审核。');
        if(!a.recipient.verified)throw new BusinessError('CONTACT_UNVERIFIED','请先填写并人工核实联系人。');
        if(a.recipient.channel){if(!a.recipient.opt_in||!a.recipient.address)throw new BusinessError('CONTACT_UNVERIFIED','请确认渠道、地址及对方允许联系。');normalizeAddress(a.recipient.channel,a.recipient.address);}
        else if(process.env.EXTERNAL_ACTION_MODE==='live'||!z.string().email().safeParse(a.recipient.email).success)throw new BusinessError('CONTACT_UNVERIFIED','请选择 WhatsApp 或 Telegram 并人工核实联系人。');
        if(a.action_type==='VIEWING')this.checkSlots(a);
        if(a.template&&(a.negotiation_notes&&!a.body.includes(a.negotiation_notes)||a.slots&&!a.body.includes(formatViewingTime(a.slots))))throw new BusinessError('TEMPLATE_CONTENT_REQUIRED','模板正文须包含当前协商诉求及完整的看房时间（新加坡本地日期和时间）。请将预览中的时间文字放入模板参数并重新保存。');
        a.content_hash=actionHash(a);a.approval={revision:a.revision,hash:a.content_hash,reviewer:hash(actor),at:new Date().toISOString()};a.status='APPROVED';
      }else if(kind==='execute'){
        if(isLegacyGeneratedMessage(a.body))throw new BusinessError('DRAFT_REWRITE_REQUIRED','这份草稿是旧格式，请先编辑并重新起草自然消息，再审核。');
        if(a.status!=='APPROVED'||!a.approval||a.approval.revision!==a.revision||a.approval.hash!==actionHash(a)||!a.recipient.verified)throw new BusinessError('APPROVAL_REQUIRED','执行需要当前修订的有效审批。');
        if(a.action_type==='VIEWING'){
          this.checkSlots(a);await tx.query("SELECT pg_advisory_xact_lock(hashtext('real-estate-viewing-reservations'))");
          const keys=[`client:${s.owner_hash}`,`contact:${hash(a.recipient.channel?`${a.recipient.channel}:${a.recipient.address}`:(a.recipient.email||'').toLowerCase())}`];
          const conflict=await tx.query('SELECT 1 FROM viewing_reservations WHERE calendar_key=ANY($1::text[]) AND starts_at<$3::timestamptz AND ends_at>$2::timestamptz LIMIT 1',[keys,a.slots!.starts_at,a.slots!.ends_at]);
          if(conflict.rowCount)throw new BusinessError('CALENDAR_CONFLICT','本地日历中已有重叠的候选或确认看房时间，请选择其他时段。');
          for(const key of keys)await tx.query('INSERT INTO viewing_reservations(execution_key,session_id,calendar_key,starts_at,ends_at) VALUES($1,$2,$3,$4,$5)',[executionKey,sessionId,key,a.slots!.starts_at,a.slots!.ends_at]);
        }
        if(process.env.EXTERNAL_ACTION_MODE==='live'){
          if(!this.delivery)throw new BusinessError('LIVE_DISABLED','发送队列不可用。');await this.delivery.queue(tx,s,a,executionKey);a.mode='live';a.status='QUEUED';
        }else{a.execution={external_id:`simulated:${executionKey}`,mode:'simulation',executed_at:new Date().toISOString()};a.status='SIMULATED';a.mode='simulation';}
        await tx.query('INSERT INTO action_executions(execution_key,action_id,session_id,result) VALUES($1,$2,$3,$4)',[executionKey,a.id,sessionId,JSON.stringify(a.execution||{status:'QUEUED',mode:'live'})]);
      }else if(kind==='cancel'){
        if(!['DRAFT','PENDING_REVIEW','APPROVED','QUEUED'].includes(a.status))throw new BusinessError('INVALID_STATUS','该动作不能取消；已经提交的记录需要保留。');await tx.query("UPDATE message_outbox SET state='CANCELLED',updated_at=now() WHERE action_id=$1 AND state='QUEUED'",[id]);await tx.query('DELETE FROM viewing_reservations WHERE execution_key=$1',[executionKey]);a.status='CANCELLED';a.approval=null;
      }else if(kind==='confirm'){
        const v=z.object({revision:z.number().int(),client_confirmed:z.literal(true),salesperson_confirmed:z.literal(true),evidence_note:z.string().trim().min(5).max(1000)}).strict().parse(input);
        if(a.action_type!=='VIEWING'||!['SIMULATED','ACCEPTED','DELIVERED','READ','REPLIED','MANUAL_SENT','MANUAL_REPLIED'].includes(a.status))throw new BusinessError('INVALID_STATUS','请先提交看房询问，再记录双方的明确同意。');a.confirmation={client_confirmed:true,salesperson_confirmed:true,evidence_note:v.evidence_note,recorded_at:new Date().toISOString(),mode:a.mode};a.status='CONFIRMED';
      }
      if(kind==='edit'||kind==='cancel')await recordViewingIntent(tx,a);
      await tx.query('UPDATE workflow_actions SET body=$2,updated_at=now() WHERE id=$1',[id,JSON.stringify(a)]);await tx.query('INSERT INTO followup_events(session_id,action_id,kind,body) VALUES($1,$2,$3,$4)',[sessionId,id,kind,JSON.stringify({revision:a.revision,status:a.status,mode:a.mode})]);await this.store.event(tx,sessionId,{type:'action_update',payload:{action_id:id,status:a.status,revision:a.revision,mode:a.mode}},`action:${id}:${a.revision}:${a.status}`);return a;
    });
  }
  private checkSlots(a:Action){if(!a.slots)throw new BusinessError('SLOTS_REQUIRED','请提供新加坡时区的看房起止时间。');const from=Date.parse(a.slots.starts_at),to=Date.parse(a.slots.ends_at);if(!Number.isFinite(from)||!Number.isFinite(to)||from<=Date.now()||to<=from||to-from>4*3600000)throw new BusinessError('INVALID_SLOTS','看房须在未来，结束晚于开始，时长不超过四小时。');}
}
