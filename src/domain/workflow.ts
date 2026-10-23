import { randomUUID } from 'node:crypto';
import type { BaseChatModel } from '@langchain/core/language_models/chat_models';
import { HumanMessage, SystemMessage } from '@langchain/core/messages';
import { z } from 'zod';
import type { AgentTask } from '../agent-task-store.js';
import type { Assignment } from '../types.js';
import { BusinessError, DomainStore,hash,type DomainState,type TaskEnvelope } from '../stores/domain.js';
import { draftSchema,extractRules,extractRequirements,mergeRequirements,pendingQuestions,repairPendingLocationSubjects,criterion,type RequirementDraft } from './requirements.js';
import { recommendationRequestSchema,validateResponse,feedbackSchema,type RecommendationRequest } from '../contracts/recommendation-v1.js';
import { buildExplanation,renderSummary,criterionText } from './explanation.js';
import { ActionService } from './actions.js';
import { KnowledgeService } from './knowledge.js';
import { RecommendationGateway } from '../adapters/recommendation.js';
import { DataHttpAdapter } from '../adapters/data-http.js';
import { ToolExecutor,type ToolContext } from '../tools/executor.js';
import { loadProfile } from '../agents/profiles.js';
import {AgentOrchestrator} from '../agents/orchestrator/agent-orchestrator.js';
import { describeAgentFailure } from './errors.js';
import {LocationService} from '../adapters/locations.js';
import {DEFAULT_PROXIMITY_SCALE_M} from './geo-intake.js';
import {displayedCandidates,resolveListingSelection,interpretFeedback} from './selection.js';
import {FollowupService,singaporeDate} from './followup.js';
import {ManualFollowupService} from './manual-followup.js';
import {notesFromFollowupQuery} from '../shared/followup-message.js';
import {ListingPresentationService} from './listing-presentation.js';
import {loadFeedbackSidecar,coldStartAfterAdoption} from './feedback-sidecar.js';
import {feedbackSidecarSchema} from '../contracts/recommendation-native.js';

const resultEnvelope=z.object({schema_version:z.literal('1.1'),agent:z.enum(['a','b','c','d']),operation_id:z.string().uuid(),requirements_version:z.number().int(),payload_ref:z.string().uuid(),payload_hash:z.string()}).strict();
export type Plan={assignments:Assignment[];answer:string};
export class WorkflowService{
  readonly orchestrator:AgentOrchestrator;
  readonly listingPresentation:ListingPresentationService;
  readonly manual:ManualFollowupService;
  readonly actions:ActionService;readonly followup:FollowupService;readonly tools:ToolExecutor;readonly data:DataHttpAdapter;readonly locations:LocationService;readonly recommender:RecommendationGateway;readonly knowledge:KnowledgeService;
  constructor(readonly store:DomainStore,readonly dataset:string,readonly model?:BaseChatModel,intentModel?:BaseChatModel,knowledgeModels:{answer?:BaseChatModel;rewriteModel?:BaseChatModel;rerankModel?:BaseChatModel}={}){
    this.orchestrator=new AgentOrchestrator(intentModel||model);
    this.followup=new FollowupService(store);this.actions=new ActionService(store,this.followup);this.manual=new ManualFollowupService(store,model);
    this.tools=new ToolExecutor(async trace=>{await store.pool.query('INSERT INTO tool_traces(id,session_id,operation_id,body) VALUES($1,$2,$3,$4)',[trace.id,trace.session_id,trace.operation_id,JSON.stringify(trace)]);});
    this.data=new DataHttpAdapter(process.env.DATA_API_URL||'http://127.0.0.1:8089',this.tools,(ctx,body)=>store.savePayload(ctx.sessionId,ctx.operationId,body));
    this.listingPresentation=new ListingPresentationService(this.data,dataset);
    this.locations=new LocationService(this.data,this.tools,(ctx,body)=>store.savePayload(ctx.sessionId,ctx.operationId,body));
    this.recommender=new RecommendationGateway(this.data,(ctx,body)=>store.savePayload(ctx.sessionId,ctx.operationId,body),process.env.RECOMMENDATION_MODE||'baseline',process.env.RECOMMENDATION_API_URL,this.locations);
    this.knowledge=new KnowledgeService(process.env.KNOWLEDGE_MODE||'hybrid_rrf',knowledgeModels.answer||model,knowledgeModels);
  }
  async plan(query:string,sessionId:string,requestId:string=randomUUID(),sourceRequestId?:string):Promise<Plan>{
    // Reserve arrival order before remote classification, without holding a DB lock during LLM/Chroma calls.
    const reservation=await this.store.transaction(async tx=>{
      const s=await this.store.lock(tx,sessionId),existing=(await tx.query('SELECT id,query,kind,status,intent_plan,created_at FROM domain_operations WHERE session_id=$1 AND request_id=$2',[sessionId,requestId])).rows[0];
      if(existing){if(existing.query!==query)throw new BusinessError('REQUEST_ID_CONFLICT','同一请求 ID 不可用于不同消息。');if(existing.kind==='routing'&&existing.status==='queued'&&Date.now()-Date.parse(existing.intent_plan.routing_started_at||existing.created_at)>30000){await tx.query('UPDATE domain_operations SET intent_plan=$2 WHERE id=$1',[existing.id,JSON.stringify({routing_started_at:new Date().toISOString()})]);return {op:existing.id,snapshot:structuredClone(s)};}return null;}
      const op=randomUUID(),snapshot=structuredClone(s);await tx.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind,source_request_id,intent_plan) VALUES($1,$2,$3,$4,'routing',$5,$6)",[op,sessionId,requestId,query,sourceRequestId||null,JSON.stringify({routing_started_at:new Date().toISOString()})]);this.store.message(s,'user',query);await this.store.saveState(tx,s);return {op,snapshot};
    });
    if(!reservation)return {assignments:[],answer:'该消息已受理，可以在会话中继续查看结果。'};
    const intent=await this.orchestrator.recognizeIntent(query,reservation.snapshot),plan=this.orchestrator.plan(intent);
    try{return await this.store.transaction(async tx=>{
      const s=await this.store.lock(tx,sessionId),op=reservation.op,{kind,agent,deferred_summary}=plan;
      await tx.query('UPDATE domain_operations SET kind=$2,intent_plan=$3 WHERE id=$1',[op,kind,JSON.stringify({...plan,intent})]);
      await tx.query('INSERT INTO tool_traces(id,session_id,operation_id,body) VALUES($1,$2,$3,$4)',[randomUUID(),sessionId,op,JSON.stringify({tool:'orchestrator_intent',agent:'orchestrator',intent})]);
      const staleDataset=s.recommendation&&s.recommendation.state.coverage.dataset_version!==this.dataset;
      if(staleDataset&&['explain','summary','message','viewing'].includes(kind)){
        const answer='房源数据已更新为完整快照。请先发送“按当前需求重新搜索”，再基于新结果解释或跟进；旧会话与草稿仍保留。';
        this.store.message(s,'assistant',answer);await tx.query("UPDATE domain_operations SET status='completed' WHERE id=$1",[op]);await this.store.saveState(tx,s);await this.store.event(tx,sessionId,{type:'workflow_result',payload:{intent,answer}},`dataset-changed:${op}`);return {assignments:[],answer};
      }
      if(kind==='clarify'||((kind==='explain'||kind==='summary')&&!s.recommendation)){
        const answer=kind==='clarify'?'我可以帮你找房、解答新加坡租房知识、解释已有推荐，或准备联系、看房与总结。请说明你这次希望做哪一项。':'请先搜索房源或回到已有推荐会话，再选择需要解释或总结的房源。';
        this.store.message(s,'assistant',answer);await tx.query("UPDATE domain_operations SET status='completed' WHERE id=$1",[op]);await this.store.saveState(tx,s);await this.store.event(tx,sessionId,{type:'workflow_result',payload:{intent,answer}},`intent-clarify:${op}`);return {assignments:[],answer};
      }
      if(kind==='search'&&(await tx.query("SELECT 1 FROM domain_operations newer,domain_operations current WHERE current.id=$1 AND newer.session_id=current.session_id AND newer.kind='search' AND (newer.created_at,newer.id)>(current.created_at,current.id) LIMIT 1",[op])).rowCount){await tx.query("UPDATE domain_operations SET status='superseded' WHERE id=$1",[op]);return {assignments:[],answer:'较新的找房请求已受理，这条较早的请求不会覆盖新条件。'};}
      let context:{message_id:string;text:string}[]=[];
      if(kind==='search'&&s.active_operation_id){const previous=(await tx.query('SELECT status,context FROM domain_operations WHERE id=$1',[s.active_operation_id])).rows[0];if(previous&&(previous.status==='queued'||s.pending))context=previous.context;}
      if(kind==='search'){const older=(await tx.query("SELECT earlier.request_id,earlier.query FROM domain_operations earlier,domain_operations current WHERE current.id=$1 AND earlier.session_id=current.session_id AND earlier.kind='routing' AND earlier.status='queued' AND (earlier.created_at,earlier.id)<(current.created_at,current.id) ORDER BY earlier.created_at,earlier.id LIMIT 32",[op])).rows;context.push(...older.map(r=>({message_id:r.request_id,text:r.query})));}
      context=[...new Map([...context,{message_id:requestId,text:query}].map(c=>[c.message_id,c])).values()];
      if(context.length>32||context.reduce((n,m)=>n+m.text.length,0)>16000)throw new BusinessError('CONTEXT_LIMIT','本轮待确认的需求过长，请将完整需求整理为一条消息后在新会话提交。',422);
      await tx.query('UPDATE domain_operations SET context=$2 WHERE id=$1',[op,JSON.stringify(context)]);
      if(kind==='search'){
        if(s.active_operation_id)await tx.query("UPDATE domain_operations SET status='superseded' WHERE id=$1 AND status='queued'",[s.active_operation_id]);
        s.active_operation_id=op;
      }
      const ruleChanges=extractRules(query,s.requirements);
      const reuse=kind==='search'&&s.requirements.criteria.length>0&&!s.pending&&/按当前需求|重新推荐|重新检索|重新搜索|search again|refresh/i.test(query)&&!ruleChanges.upsert.length&&!ruleChanges.remove_fields.length&&!ruleChanges.location_intents?.length;
      const body={query:kind==='search'?context.map((m,i)=>(i?'用户后续修正：':'')+m.text).join('\n'):query,latest_query:query,context,kind,input_mode:reuse?'reuse_requirements':'extract',source_request_id:sourceRequestId??null,active_search_operation_id:s.active_operation_id,requirements:s.requirements,pending:s.pending,recommendation:s.recommendation};
      const assignments=await this.store.queue(tx,s,op,agent,body,'initial');await this.store.saveState(tx,s);
      return {assignments,answer:kind==='search'?'正在整理需求，并按硬条件逐项核查。':'正在根据当前会话资料处理。'};
    });}catch(error){
      // A rejected plan must not remain a queued routing lease and retry forever after recovery.
      // If the commit actually succeeded, kind is no longer routing: leave its queued agent task intact.
      await this.store.transaction(async tx=>{
        const s=await this.store.lock(tx,sessionId);
        const row=await tx.query("UPDATE domain_operations SET status='failed' WHERE id=$1 AND kind='routing' AND status='queued' RETURNING id",[reservation.op]);
        if(!row.rowCount)return;
        const code=error instanceof BusinessError?error.code:'ORCHESTRATOR_PLAN_FAILED',message=error instanceof BusinessError?error.message:'本次请求暂时无法编排，请稍后重新提交。';
        this.store.message(s,'assistant',message);await this.store.saveState(tx,s);
        await this.store.event(tx,sessionId,{type:'workflow_error',payload:{code,message,operation_id:reservation.op}},`routing-failed:${reservation.op}`);
      }).catch(()=>{/* Preserve the original failure if the database itself is unavailable. */});
      throw error;
    }
  }
  async recoverRouting(){const rows=(await this.store.pool.query("SELECT session_id,request_id,query,source_request_id FROM domain_operations WHERE kind='routing' AND status='queued' AND COALESCE((intent_plan->>'routing_started_at')::timestamptz,created_at)<now()-interval '30 seconds' ORDER BY created_at LIMIT 2")).rows;await Promise.allSettled(rows.map(r=>this.plan(r.query,r.session_id,r.request_id,r.source_request_id||undefined)));}
  context(sessionId:string,env:TaskEnvelope):ToolContext{return {sessionId,operationId:env.operation_id,agent:env.agent,dataset:this.dataset,deadline:Date.now()+120000,calls:0};}
  async run(agent:TaskEnvelope['agent'],prompt:string,sessionId:string,taskId:string):Promise<string>{
    const {env,body}=await this.store.verifyTask(prompt,sessionId,taskId,agent);const prior=await this.store.pool.query('SELECT envelope FROM agent_result_payloads WHERE task_id=$1',[taskId]);if(prior.rows[0])return JSON.stringify(prior.rows[0].envelope);
    const ctx=this.context(sessionId,env),profile=await loadProfile(agent);let payload:unknown;
    await this.store.pool.query('INSERT INTO tool_traces(id,session_id,operation_id,body) VALUES($1,$2,$3,$4)',[randomUUID(),sessionId,env.operation_id,JSON.stringify({tool:'skill_loaded',agent,sha256:profile.sha256})]);
    if(agent==='a'){
      const pending=body.pending?repairPendingLocationSubjects(body.pending):null;
      const base=pending?mergeRequirements(body.requirements,pending):body.requirements;
      const query=pending?body.latest_query:body.query;
      const instructions=profile.instructions+(pending?'\n待澄清问题（仅作上下文，用户最新消息是对这些问题的回答）：'+JSON.stringify(pending.clarifications):'');
      let draft=body.input_mode==='reuse_requirements'?draftSchema.parse({upsert:[],remove_fields:[],sources:[],clarifications:[],supported:true}):await extractRequirements(query,base,this.model,instructions);
      for(const intent of draft.location_intents||[]){
        if(intent.category==='MRTStation'){
          const target={category:'MRTStation',name:'最近的MRT地铁站'};
          draft.upsert.push(criterion('place_proximity','lte',intent.radius_m??DEFAULT_PROXIMITY_SCALE_M,'m',intent.strength==='soft',{target,distance_type:intent.distance_type}));
          if(intent.strength==='hard')draft.upsert.push(criterion('place_proximity','lte',DEFAULT_PROXIMITY_SCALE_M,'m',true,{target,distance_type:intent.distance_type}));
          draft.sources.push({field:'place_proximity',quote:intent.quote});
          draft.interpretation_notes=[...(draft.interpretation_notes||[]),'“MRT”按地铁站类别处理，使用官方已收录MRT站点出口计算最近直线距离，不要求指定站名，也不代替实际步行路线。'];
          continue;
        }
        const resolved=await this.locations.resolve(ctx,intent.name,intent.explicit_station);
        if(!resolved.entity){draft.clarifications.push(resolved.note);continue;}
        const id=String(resolved.entity.properties.place_id),target={place_id:id,name:String(resolved.entity.properties.name)};
        if(intent.explicit_station&&/^LTA:(MRT|LRT):/.test(id))draft.remove_criterion_ids=[...(draft.remove_criterion_ids||[]),...base.criteria.filter((c:any)=>c.field==='place_proximity'&&c.target?.place_id==='URA:AREA:'+id.slice(8)).map((c:any)=>c.criterion_id)];
        draft.upsert.push(criterion('place_proximity','lte',intent.radius_m??DEFAULT_PROXIMITY_SCALE_M,'m',intent.strength==='soft',{target,distance_type:intent.distance_type}));
        if(intent.strength==='hard')draft.upsert.push(criterion('place_proximity','lte',DEFAULT_PROXIMITY_SCALE_M,'m',true,{target,distance_type:intent.distance_type}));
        draft.sources.push({field:'place_proximity',quote:intent.quote});
        draft.interpretation_notes=[...(draft.interpretation_notes||[]),resolved.note,intent.radius_m?`距离范围 ${intent.radius_m} 米按${intent.distance_type==='straight_line'?'直线':intent.distance_type}口径处理；在范围内继续优先更近的房源。`:'未指定距离上限，采用“越近越好”的软偏好，不因默认半径排除房源。'];
      }
      if(pending)draft={...draft,clarifications:pendingQuestions(pending,draft),upsert:[...pending.upsert,...draft.upsert],remove_fields:[...pending.remove_fields,...draft.remove_fields],sources:[...pending.sources,...draft.sources]};
      payload={draft:draftSchema.parse(draft),feedback:interpretFeedback(body.latest_query||body.query,body.recommendation,body.source_request_id,env.operation_id)};
    }else if(agent==='b'){
      const req=recommendationRequestSchema.parse(body.request);const sidecar=body.feedback_snapshot?feedbackSidecarSchema.parse(body.feedback_snapshot):null;const rec=await this.recommender.recommend(req,ctx,sidecar);rec.warnings.push(...(body.interpretation_notes||[]).map((message:string)=>({code:'GEO_INTERPRETATION',message})));payload=rec;
    }else if(agent==='c'){
      if(!body.recommendation)throw new BusinessError('NO_RECOMMENDATION','请先检索房源。');
      const rec=body.recommendation;
      // The model can select evidence to emphasize; critical facts remain code-rendered.
      let highlights:unknown=null;
      if(this.model){const response=await this.model.invoke([new SystemMessage('你是解释 Agent。只返回JSON {"listing_ids":[...],"criterion_ids":[...]}，从输入中选择值得强调的证据条件。不得增加房源、改排名、生成数值或推断可租状态。'),new HumanMessage(JSON.stringify({question:body.query,listing_ids:displayedCandidates(rec).map((c:any)=>c.listing.listing_id),criteria:body.requirements.criteria}))],{signal:AbortSignal.timeout(20000)});try{const h=z.object({listing_ids:z.array(z.string()),criterion_ids:z.array(z.string())}).strict().parse(JSON.parse(response.text.replace(/^```(?:json)?\s*|\s*```$/g,'')));if(h.listing_ids.every(id=>displayedCandidates(rec).some((c:any)=>c.listing.listing_id===id))&&h.criterion_ids.every(id=>body.requirements.criteria.some((c:any)=>c.criterion_id===id)))highlights=h;}catch{/* Preserve deterministic facts when optional wording selection fails. */}}
      payload={explanation:buildExplanation(body.requirements,rec,this.dataset,body.query||''),highlights};
    }else if(body.kind==='daily')payload={kind:'daily',date:singaporeDate()};
    else if(body.kind==='knowledge'||body.kind==='status'){
      if(body.kind==='status'){
        const rows=(await this.store.pool.query('SELECT body FROM workflow_actions WHERE session_id=$1 ORDER BY updated_at DESC LIMIT 5',[sessionId])).rows;
        const labels:Record<string,string>={MANUAL_SENT:'用户确认已自行发送；没有平台回执',MANUAL_REPLIED:'用户已粘贴回复；请查看跟进工作台的理解与待办',DRAFT:'尚未发送：草稿',PENDING_REVIEW:'尚未发送：等待审批',APPROVED:'尚未发送：已批准，待点击执行',SIMULATED:'仅本地模拟，没有真实发送',QUEUED:'正在排队，尚未提交平台',SENDING:'正在提交平台，等待结果',ACCEPTED:'平台已受理，不代表已读或看房确认',DELIVERED:'收到送达回执',READ:'收到已读回执',REPLIED:'已收到经纪人回复，房源条件仍需核实',CONFIRMED:'已记录双方明确同意；模式以记录为准',UNKNOWN:'发送结果不明，请先核对平台记录，不要直接重发',FAILED:'未成功提交或平台报告失败',CANCELLED:'已取消'};
        payload={kind:'knowledge',knowledge:{answer:rows.length?rows.map(({body:a})=>`房源 ${a.listing_ids.join('、')}：${labels[a.status]||a.status}（${a.mode==='manual'?'用户手动记录':a.mode==='live'?'真实平台':'模拟'}）。`).join('\n'):'还没有联系记录。请先在房源卡片选择咨询或看房，编辑并审批草稿。',citations:[],retrieval:'owned_action_records',vector_enabled:false}};
      }else payload={kind:'knowledge',knowledge:await this.tools.run(ctx,'knowledge',{query:body.query},()=>this.knowledge.query(body.query))};
    }
    else if(body.kind==='summary'){
      if(!body.recommendation)throw new BusinessError('NO_RECOMMENDATION','请先检索房源，再生成总结。');payload={kind:'summary',document:renderSummary(body.requirements,body.recommendation,this.dataset)};
    }else{
      const s=await this.store.state(sessionId);
      if(!body.source_request_id||body.source_request_id!==s.latest_request_id||body.active_search_operation_id!==s.active_operation_id)throw new BusinessError('SELECTION_STALE','请从当前推荐列表选房后，再提出跟进请求。');
      try{const id=resolveListingSelection(s.recommendation,String(body.query)).listing.listing_id;payload={kind:'action',action:await this.manual.draftFromHistory(s,[id],body.kind==='viewing'?'VIEWING':'MESSAGE',notesFromFollowupQuery(String(body.query)).slice(0,2000))};}
      catch(error){if(error instanceof BusinessError&&['SELECTION_REQUIRED','SELECTION_NOT_IN_RESULT'].includes(error.code))payload={kind:'selection_needed',message:error.message};else throw error;}
    }
    return this.store.transaction(async tx=>{const row=await tx.query('SELECT envelope FROM agent_result_payloads WHERE task_id=$1',[taskId]);if(row.rows[0])return JSON.stringify(row.rows[0].envelope);const ref=await this.store.payload(tx,sessionId,env.operation_id,payload);const output={schema_version:'1.1',agent,operation_id:env.operation_id,requirements_version:env.requirements_version,payload_ref:ref,payload_hash:hash(payload)};await tx.query('INSERT INTO agent_result_payloads(task_id,envelope) VALUES($1,$2) ON CONFLICT DO NOTHING',[taskId,JSON.stringify(output)]);return JSON.stringify(output);});
  }
  async emitDueReminders(){
    const rows=(await this.store.pool.query("SELECT id,session_id,body FROM workflow_actions WHERE body->>'next_followup_at' IS NOT NULL AND (body->>'next_followup_at')::timestamptz<=now() AND body->>'status'<>'CANCELLED' LIMIT 100")).rows;
    for(const row of rows)await this.store.transaction(async tx=>{
      const s=await this.store.lock(tx,row.session_id),a=row.body,key=`reminder:${row.id}:${a.revision}:${a.next_followup_at}`;
      if((await tx.query('SELECT 1 FROM session_events WHERE session_id=$1 AND event_key=$2',[s.session_id,key])).rowCount)return;
      this.store.message(s,'assistant',`跟进提醒已到期：${a.subject}。请在工作台检查下一步；系统没有自动发送消息。`);
      await this.store.event(tx,s.session_id,{type:'action_update',payload:{action_id:row.id,reminder:'due',message:'跟进提醒已到期；没有自动发送外部消息。',mode:'simulation'}},key);await this.store.saveState(tx,s);
    });
  }
  async apply(task:AgentTask):Promise<{sessionId:string;agent:TaskEnvelope['agent']}[]>{
    return this.store.transaction(async tx=>{
      const taskRow=await tx.query('SELECT * FROM agent_tasks WHERE id=$1 FOR UPDATE',[task.id]);const t=taskRow.rows[0];if(!t||!['completed','failed'].includes(t.status))throw Error('Task is not terminal');
      if((await tx.query('SELECT 1 FROM handled_results WHERE task_id=$1',[task.id])).rowCount)return [];
      const s=await this.store.lock(tx,task.sessionId),env=t.metadata as TaskEnvelope;
      const operation=(await tx.query('SELECT * FROM domain_operations WHERE id=$1 AND session_id=$2',[env.operation_id,s.session_id])).rows[0];
      if(!operation)throw Error('Task operation missing');
      const isSearch=operation.kind==='search';
      if((isSearch&&s.active_operation_id!==env.operation_id)||s.requirements_version!==env.requirements_version){await tx.query("INSERT INTO handled_results(task_id,status) VALUES($1,'superseded')",[task.id]);await tx.query("UPDATE domain_operations SET status='superseded' WHERE id=$1 AND status='queued'",[env.operation_id]);return [];}
      const wake:{sessionId:string;agent:TaskEnvelope['agent']}[]=[];
      try{
        if(t.status==='failed'){const failure=describeAgentFailure(task.agent,String(t.error||''));throw new BusinessError(failure.code,failure.message);}
        const result=resultEnvelope.parse(JSON.parse(t.output));if(result.agent!==task.agent||result.operation_id!==env.operation_id||result.requirements_version!==env.requirements_version)throw Error('Result identity mismatch');
        const p=(await tx.query('SELECT body,sha256 FROM domain_payloads WHERE ref=$1 AND session_id=$2 AND operation_id=$3',[result.payload_ref,s.session_id,env.operation_id])).rows[0];if(!p||p.sha256!==result.payload_hash||hash(p.body)!==result.payload_hash)throw Error('Result payload mismatch');
        const payload=p.body;
        const next=async(agent:TaskEnvelope['agent'],body:unknown,stage:string)=>{const a=await this.store.queue(tx,s,env.operation_id,agent,body,stage);wake.push(...a.map(x=>({sessionId:s.session_id,agent:x.agent})));};
        if(task.agent==='a'){
          const draft=draftSchema.parse(payload.draft);
          if(draft.clarifications.length||!draft.supported){s.pending=draft;const known=mergeRequirements(s.requirements,draft).criteria;const text=(known.length?'已暂存的明确条件（尚未开始推荐）：\n'+known.map(c=>'• '+criterionText(c)).join('\n')+'\n\n':'')+'还需确认：\n'+draft.clarifications.join('\n');this.store.message(s,'assistant',text);await this.store.event(tx,s.session_id,{type:'clarification',payload:{questions:draft.clarifications,operation_id:env.operation_id}},`clarify:${task.id}`);}
          else{
            const requirements=mergeRequirements(s.requirements,draft);if(hash(requirements)!==hash(s.requirements)){s.requirements_version++;s.requirements=requirements;await tx.query('INSERT INTO requirement_revisions(session_id,version,body,sources) VALUES($1,$2,$3,$4)',[s.session_id,s.requirements_version,JSON.stringify(requirements),JSON.stringify(draft.sources)]);}
            s.pending=null;
            for(const fb of payload.feedback??[]) {const event=feedbackSchema.parse(fb);await tx.query('INSERT INTO listing_feedback(session_id,event_id,body) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',[s.session_id,event.event_id,JSON.stringify(event)]);}
            const feedback=(await tx.query('SELECT body FROM listing_feedback WHERE session_id=$1 AND consumed_request_id IS NULL ORDER BY event_id',[s.session_id])).rows.map(x=>x.body);
            // Complete feedback snapshot of the previously ADOPTED round (native contract 2.0),
            // built once here and persisted; agent B and its retries reuse it verbatim.
            const feedback_snapshot=await loadFeedbackSidecar(tx,s);
            const requestId=randomUUID(),previous=s.recommendation?.state.state_id;
            const request=recommendationRequestSchema.parse({contract_version:'1.0',request_id:requestId,conversation_id:s.session_id,requirements_version:s.requirements_version,requirements:s.requirements,feedback,...(previous?{previous_state_id:previous}:{}),result_limit:10});
            await tx.query('INSERT INTO recommendation_calls(id,session_id,operation_id,request,feedback_snapshot) VALUES($1,$2,$3,$4,$5)',[requestId,s.session_id,env.operation_id,JSON.stringify(request),JSON.stringify(feedback_snapshot)]);
            await tx.query('UPDATE listing_feedback SET consumed_request_id=$2 WHERE session_id=$1 AND consumed_request_id IS NULL',[s.session_id,requestId]);
            s.latest_request_id=requestId;await next('b',{request,feedback_snapshot,interpretation_notes:draft.interpretation_notes||[]},'recommend');
          }
        }else if(task.agent==='b'){
          const call=(await tx.query('SELECT request FROM recommendation_calls WHERE id=$1 AND operation_id=$2',[s.latest_request_id,env.operation_id])).rows[0];if(!call)throw Error('Recommendation call missing');
          const response=validateResponse(payload,call.request);
          for(const e of response.evidence){if(e.source_kind==='data_api'){const evidence=(await tx.query('SELECT 1 FROM domain_payloads WHERE ref=$1 AND session_id=$2',[e.source_ref,s.session_id])).rowCount;if(!evidence)throw Error('Evidence source cannot be resolved');}}
          await tx.query('UPDATE recommendation_calls SET response=$2 WHERE id=$1',[s.latest_request_id,JSON.stringify(response)]);
          if(response.status==='error')throw new BusinessError(response.error!.code,response.error!.message);
          if(response.status==='needs_clarification'){const questions=response.clarifications.map(x=>x.question);this.store.message(s,'assistant',questions.join('\n'));await this.store.event(tx,s.session_id,{type:'clarification',payload:{questions}},`clarify:${task.id}`);}
          else{s.recommendation=response;s.recommendation_cold_start=coldStartAfterAdoption(s.recommendation_cold_start,response);await next('c',{requirements:s.requirements,recommendation:response,query:operation.query},'explain');}
        }else if(task.agent==='c'){
          const source=payload.explanation.source_request_id;const sourceOperation=(await tx.query('SELECT operation_id FROM recommendation_calls WHERE id=$1',[source])).rows[0]?.operation_id;if(source!==s.latest_request_id||sourceOperation!==s.active_operation_id)throw new BusinessError('RESULT_STALE','推荐已变化，此解释已失效。');s.explanation=payload.explanation;this.store.message(s,'assistant',payload.explanation.summary);await this.store.event(tx,s.session_id,{type:'workflow_result',payload:{explanation:payload.explanation,source_request_id:source}},`result:${task.id}`);
          if(isSearch&&operation.intent_plan?.deferred_summary)await next('d',{kind:'summary',requirements:s.requirements,recommendation:s.recommendation},'followup-summary');
        }else if(payload.kind==='knowledge'){this.store.message(s,'assistant',payload.knowledge.answer,payload.knowledge);await this.store.event(tx,s.session_id,{type:'workflow_result',payload:{knowledge:payload.knowledge}},`knowledge:${task.id}`);}
        else if(payload.kind==='selection_needed'){this.store.message(s,'assistant',payload.message);await this.store.event(tx,s.session_id,{type:'workflow_result',payload:{selection_needed:true,message:payload.message}},`selection:${task.id}`);}
        else if(payload.kind==='daily'){await this.followup.daily(s.session_id,payload.date,tx);this.store.message(s,'assistant',`已生成 ${payload.date} 的会话与跟进总结，可到总结文档下载。日期按 Asia/Singapore 计算。`);}
        else if(payload.kind==='summary'){
          const source=payload.document.model.source_request_id;const sourceOperation=(await tx.query('SELECT operation_id FROM recommendation_calls WHERE id=$1',[source])).rows[0]?.operation_id;if(source!==s.latest_request_id||sourceOperation!==s.active_operation_id)throw new BusinessError('RESULT_STALE','推荐已变化或正在更新，请重新生成总结。');
          const id=randomUUID();await tx.query('INSERT INTO workflow_artifacts(id,session_id,source_request_id,requirements_version,content,sha256) VALUES($1,$2,$3,$4,$5,$6)',[id,s.session_id,source,s.requirements_version,JSON.stringify(payload.document),hash(payload.document)]);const text='总结已生成，可下载 Markdown 和 HTML。';this.store.message(s,'assistant',text);await this.store.event(tx,s.session_id,{type:'workflow_result',payload:{artifact_id:id,message:text}},`artifact:${task.id}`);
        }else if(payload.kind==='action'){
          const a=payload.action;const sourceCall=(await tx.query('SELECT operation_id FROM recommendation_calls WHERE id=$1',[a.source_request_id])).rows[0];if(a.source_request_id!==s.latest_request_id||sourceCall?.operation_id!==s.active_operation_id)throw new BusinessError('ACTION_STALE','推荐已变化，请重新生成草稿。');await tx.query('INSERT INTO workflow_actions(id,session_id,body) VALUES($1,$2,$3)',[a.id,s.session_id,JSON.stringify(a)]);this.store.message(s,'assistant',`已为房源 ${a.listing_ids.join('、')} 准备${a.action_type==='VIEWING'?'看房询问':'联系核实'}草稿。${a.verification_context?.some((v:any)=>v.group==='unverified')?'待核实事项已逐项写入草稿，房源仍保持待核实状态。':''}请到跟进工作台编辑并复制草稿，自行发送后点击“确认已发送”；收到回复后粘贴并确认，系统会继续理解和总结。看房仍须双方明确同意。`);await this.store.event(tx,s.session_id,{type:'action_update',payload:{action_id:a.id,status:a.status,mode:'simulation'}},`draft:${task.id}`);
        }
        await tx.query("INSERT INTO handled_results(task_id,status) VALUES($1,'applied')",[task.id]);await tx.query('UPDATE domain_operations SET status=$2 WHERE id=$1',[env.operation_id,wake.length?'queued':'completed']);
      }catch(error){
        if(error&&typeof error==='object'&&'severity' in error)throw error;
        const detail=error instanceof Error?error.message:'业务校验失败';const message=error instanceof z.ZodError?'内部结果未通过业务格式校验，本轮已停止。请重新提交或补充需求。':detail;await tx.query("INSERT INTO handled_results(task_id,status,error) VALUES($1,'rejected',$2)",[task.id,detail]);await tx.query("UPDATE domain_operations SET status='failed' WHERE id=$1",[env.operation_id]);this.store.message(s,'assistant',`本轮未完成：${message}`);await this.store.event(tx,s.session_id,{type:'workflow_error',payload:{code:error instanceof BusinessError?error.code:'BUSINESS_VALIDATION_FAILED',message,operation_id:env.operation_id}},`failure:${task.id}`);wake.length=0;
      }
      await this.store.saveState(tx,s);return wake;
    });
  }
}

