import { randomUUID } from 'node:crypto';
import type {PoolClient} from 'pg';
import {displayLimit,nextRecommendationBatch,withPresentation,RECOMMENDATION_RESERVOIR_LIMIT} from '../shared/recommendation-display.js';
import type { BaseChatModel } from '@langchain/core/language_models/chat_models';
import { HumanMessage, SystemMessage } from '@langchain/core/messages';
import { z } from 'zod';
import type { AgentTask } from '../agent-task-store.js';
import type { Assignment } from '../types.js';
import { BusinessError, DomainStore,hash,type DomainState,type TaskEnvelope } from '../stores/domain.js';
import { draftSchema,extractRequirements,mergeRequirements,criterion,type RequirementDraft } from './requirements.js';
import { recommendationRequestSchema,validateResponse,feedbackSchema,type RecommendationRequest,type Feedback } from '../contracts/recommendation-v1.js';
import { renderSummary,criterionText } from './explanation.js';
import {generateExplanation} from '../agents/explanation/generator.js';
import {ExplanationEnricher} from '../agents/explanation/enrichment.js';
import { ActionService } from './actions.js';
import { KnowledgeService } from './knowledge.js';
import { RecommendationGateway } from '../adapters/recommendation.js';
import { DataHttpAdapter } from '../adapters/data-http.js';
import { ToolExecutor,type ToolContext } from '../tools/executor.js';
import { loadProfile } from '../agents/profiles.js';
import {AgentOrchestrator} from '../agents/orchestrator/agent-orchestrator.js';
import { describeAgentFailure,recommendationFailureMessage } from './errors.js';
import {LocationService} from '../adapters/locations.js';
import {DEFAULT_PROXIMITY_SCALE_M} from './geo-intake.js';
import {displayedCandidates,resolveListingSelection} from './selection.js';
import {FollowupService,singaporeDate} from './followup.js';
import {ManualFollowupService} from './manual-followup.js';
import {notesFromFollowupQuery} from '../shared/followup-message.js';
import {ListingPresentationService} from './listing-presentation.js';
import {prepareBrowserFeedback,browserInterestNotice} from './behavior.js';
import {collectBehaviorContext} from './behavior-context.js';
import {coldStartAfterAdoption,NOT_EXITED} from './cold-start.js';
import {RequirementRefinementService} from '../agents/requirement/refinement/service.js';

const resultEnvelope=z.object({schema_version:z.literal('1.1'),agent:z.enum(['a','b','c','d']),operation_id:z.string().uuid(),requirements_version:z.number().int(),payload_ref:z.string().uuid(),payload_hash:z.string()}).strict();
export type Plan={assignments:Assignment[];answer:string};
export class WorkflowService{
  readonly refinement:RequirementRefinementService;
  readonly orchestrator:AgentOrchestrator;
  readonly listingPresentation:ListingPresentationService;
  readonly explanationEnricher:ExplanationEnricher;
  readonly manual:ManualFollowupService;
  readonly actions:ActionService;readonly followup:FollowupService;readonly tools:ToolExecutor;readonly data:DataHttpAdapter;readonly locations:LocationService;readonly recommender:RecommendationGateway;readonly knowledge:KnowledgeService;
  constructor(readonly store:DomainStore,readonly dataset:string,readonly model?:BaseChatModel,intentModel?:BaseChatModel,knowledgeModels:{answer?:BaseChatModel;rewriteModel?:BaseChatModel;rerankModel?:BaseChatModel}={}){
    this.refinement=new RequirementRefinementService(store,dataset);
    this.orchestrator=new AgentOrchestrator(intentModel||model);
    this.followup=new FollowupService(store);this.actions=new ActionService(store,this.followup);this.manual=new ManualFollowupService(store,model);
    this.tools=new ToolExecutor(async trace=>{await store.pool.query('INSERT INTO tool_traces(id,session_id,operation_id,body) VALUES($1,$2,$3,$4)',[trace.id,trace.session_id,trace.operation_id,JSON.stringify(trace)]);});
    this.data=new DataHttpAdapter(process.env.DATA_API_URL||'http://127.0.0.1:8089',this.tools,(ctx,body)=>store.savePayload(ctx.sessionId,ctx.operationId,body));
    this.listingPresentation=new ListingPresentationService(this.data,dataset);
    this.explanationEnricher=new ExplanationEnricher(this.data,this.tools,(ctx,body)=>store.savePayload(ctx.sessionId,ctx.operationId,body));
    this.locations=new LocationService(this.data,this.tools,(ctx,body)=>store.savePayload(ctx.sessionId,ctx.operationId,body));
    this.recommender=new RecommendationGateway(this.data,(ctx,body)=>store.savePayload(ctx.sessionId,ctx.operationId,body),process.env.RECOMMENDATION_MODE||'baseline',process.env.RECOMMENDATION_API_URL,this.locations);
    this.knowledge=new KnowledgeService(process.env.KNOWLEDGE_MODE||'hybrid_rrf',knowledgeModels.answer||model,knowledgeModels);
  }
  async plan(query:string,sessionId:string,requestId:string=randomUUID(),sourceRequestId?:string):Promise<Plan>{
    // Reserve arrival order before remote classification, without holding a DB lock during LLM/Chroma calls.
    const reservation=await this.store.transaction(async tx=>{
      const s=await this.store.lock(tx,sessionId),existing=(await tx.query('SELECT id,query,kind,status,intent_plan,created_at FROM domain_operations WHERE session_id=$1 AND request_id=$2',[sessionId,requestId])).rows[0];
      if(existing){if(existing.query!==query)throw new BusinessError('REQUEST_ID_CONFLICT',"The same request ID cannot be used for different messages.");if(existing.kind==='routing'&&existing.status==='queued'&&Date.now()-Date.parse(existing.intent_plan.routing_started_at||existing.created_at)>30000){await tx.query('UPDATE domain_operations SET intent_plan=$2 WHERE id=$1',[existing.id,JSON.stringify({routing_started_at:new Date().toISOString()})]);return {op:existing.id,snapshot:structuredClone(s)};}return null;}
      const op=randomUUID(),snapshot=structuredClone(s);await tx.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind,source_request_id,intent_plan) VALUES($1,$2,$3,$4,'routing',$5,$6)",[op,sessionId,requestId,query,sourceRequestId||null,JSON.stringify({routing_started_at:new Date().toISOString()})]);this.store.message(s,'user',query);await this.store.saveState(tx,s);return {op,snapshot};
    });
    if(!reservation)return {assignments:[],answer:"Your message was accepted. Results will appear in this conversation."};
    const intent=await this.orchestrator.recognizeIntent(query,reservation.snapshot),plan=this.orchestrator.plan(intent);
    try{return await this.store.transaction(async tx=>{
      const s=await this.store.lock(tx,sessionId),op=reservation.op,{kind,agent,deferred_summary}=plan;
      await tx.query('UPDATE domain_operations SET kind=$2,intent_plan=$3 WHERE id=$1',[op,kind,JSON.stringify({...plan,intent})]);
      await tx.query('INSERT INTO tool_traces(id,session_id,operation_id,body) VALUES($1,$2,$3,$4)',[randomUUID(),sessionId,op,JSON.stringify({tool:'orchestrator_intent',agent:'orchestrator',intent})]);
      const staleDataset=s.recommendation&&s.recommendation.state.coverage.dataset_version!==this.dataset;
      if(staleDataset&&['explain','summary','message','viewing'].includes(kind)){
        const answer="The full listing dataset has been updated. Search again using your current requirements before requesting explanations or follow-up. Earlier conversations and drafts are retained.";
        this.store.message(s,'assistant',answer);await tx.query("UPDATE domain_operations SET status='completed' WHERE id=$1",[op]);await this.store.saveState(tx,s);await this.store.event(tx,sessionId,{type:'workflow_result',payload:{intent,answer}},`dataset-changed:${op}`);return {assignments:[],answer};
      }
      if(kind==='more'){
        const finish=async(answer:string)=>{
          this.store.message(s,'assistant',answer);
          await tx.query("UPDATE domain_operations SET status='completed' WHERE id=$1",[op]);
          await this.store.saveState(tx,s);
          await this.store.event(tx,sessionId,{type:'workflow_result',payload:{intent,answer}},`more:${op}`);
          return {assignments:[],answer};
        };
        if(!s.recommendation)return finish('The requirements or results changed while this request was being processed. Please ask for more again.');
        if((sourceRequestId&&sourceRequestId!==s.latest_request_id)||s.latest_request_id!==reservation.snapshot.latest_request_id||s.active_operation_id!==reservation.snapshot.active_operation_id||s.requirements_version!==reservation.snapshot.requirements_version)return finish('The requirements or results changed while this request was being processed. Please ask for more again.');
        const current=(await tx.query('SELECT status FROM domain_operations WHERE id=$1',[s.active_operation_id])).rows[0];
        if(s.pending||current?.status==='queued')return finish('Your current search is still being updated. Please wait for it to finish before requesting more homes.');
        const duplicate=(await tx.query("SELECT 1 FROM domain_operations WHERE session_id=$1 AND kind='more' AND status='queued' AND id<>$2 AND intent_plan->>'source_request_id'=$3 LIMIT 1",[sessionId,op,s.latest_request_id])).rowCount;
        if(duplicate)return finish('I am already preparing the additional homes for this list.');
        const needsRefresh=!s.recommendation.presentation||staleDataset||s.recommendation.request_id!==s.latest_request_id||s.recommendation.requirements_version!==s.requirements_version;
        if(!needsRefresh&&displayLimit(s.recommendation)>=RECOMMENDATION_RESERVOIR_LIMIT)return finish('You are already viewing all ten homes in this recommendation. Change your requirements to start a new search.');
        if(needsRefresh){
          // Old five-result sessions have no reserved tail: use the same canonical
          // requirements directly, without asking the extractor to reinterpret them.
          await tx.query("UPDATE domain_operations SET kind='search',intent_plan=$2 WHERE id=$1",[op,JSON.stringify({...plan,intent,continuation_refresh:true})]);
          s.active_operation_id=op;
          const created=(await tx.query('SELECT created_at FROM domain_operations WHERE id=$1',[op])).rows[0].created_at;
          const assignments=await this.queueRecommendation(tx,s,op,created);
          await this.store.saveState(tx,s);return {assignments,answer:'Refreshing the saved recommendation under the current requirements to show up to ten homes.'};
        }
        const batch=nextRecommendationBatch(s.recommendation);
        if(!batch.listing_ids.length)return finish('There are no additional homes available in this result under the current requirements. You can adjust the conditions to explore other options.');
        await tx.query('UPDATE domain_operations SET intent_plan=$2 WHERE id=$1',[op,JSON.stringify({...plan,intent,source_request_id:s.latest_request_id,source_operation_id:s.active_operation_id})]);
        const assignments=await this.store.queue(tx,s,op,'c',{kind:'more',query,requirements:s.requirements,recommendation:batch.recommendation,continuation:{previous_count:batch.previous_count,listing_ids:batch.listing_ids},base_limit:displayLimit(s.recommendation)},'more-explain');
        await this.store.saveState(tx,s);return {assignments,answer:'Preparing five more homes with the same requirements.'};
      }
      if(kind==='clarify'||((kind==='explain'||kind==='summary')&&!s.recommendation)){
        const answer=kind==='clarify'?"I can help find homes, answer Singapore rental questions, explain recommendations or prepare enquiries, viewings and summaries. Tell me what you would like to do.":"Search for homes or return to a conversation with recommendations, then choose which homes to explain or summarise.";
        this.store.message(s,'assistant',answer);await tx.query("UPDATE domain_operations SET status='completed' WHERE id=$1",[op]);await this.store.saveState(tx,s);await this.store.event(tx,sessionId,{type:'workflow_result',payload:{intent,answer}},`intent-clarify:${op}`);return {assignments:[],answer};
      }
      if(kind==='search'&&(await tx.query("SELECT 1 FROM domain_operations newer,domain_operations current WHERE current.id=$1 AND newer.session_id=current.session_id AND newer.kind='search' AND (newer.created_at,newer.id)>(current.created_at,current.id) LIMIT 1",[op])).rowCount){await tx.query("UPDATE domain_operations SET status='superseded' WHERE id=$1",[op]);return {assignments:[],answer:"A newer search has been accepted. This earlier request will not overwrite its requirements."};}
      let context:{message_id:string;text:string}[]=[];
      if(kind==='search'&&s.active_operation_id){const previous=(await tx.query('SELECT status,context FROM domain_operations WHERE id=$1',[s.active_operation_id])).rows[0];if(previous&&(previous.status==='queued'||s.pending))context=previous.context;}
      if(kind==='search'){const older=(await tx.query("SELECT earlier.request_id,earlier.query FROM domain_operations earlier,domain_operations current WHERE current.id=$1 AND earlier.session_id=current.session_id AND earlier.kind='routing' AND earlier.status='queued' AND (earlier.created_at,earlier.id)<(current.created_at,current.id) ORDER BY earlier.created_at,earlier.id LIMIT 32",[op])).rows;context.push(...older.map(r=>({message_id:r.request_id,text:r.query})));}
      context=[...new Map([...context,{message_id:requestId,text:query}].map(c=>[c.message_id,c])).values()];
      if(context.length>32||context.reduce((n,m)=>n+m.text.length,0)>16000)throw new BusinessError('CONTEXT_LIMIT',"The requirements awaiting clarification are too long. Combine them into one message and submit it in a new conversation.",422);
      await tx.query('UPDATE domain_operations SET context=$2 WHERE id=$1',[op,JSON.stringify(context)]);
      if(kind==='search'){
        if(s.active_operation_id)await tx.query("UPDATE domain_operations SET status='superseded' WHERE id=$1 AND status='queued'",[s.active_operation_id]);
        s.active_operation_id=op;
      }
      const body={query:kind==='search'?context.map((m,i)=>(i?"User’s subsequent update: ":'')+m.text).join('\n'):query,latest_query:query,context,kind,input_mode:'extract',source_request_id:sourceRequestId??null,active_search_operation_id:s.active_operation_id,requirements:s.requirements,pending:s.pending,recommendation:s.recommendation};
      const assignments=await this.store.queue(tx,s,op,agent,body,'initial');await this.store.saveState(tx,s);
      return {assignments,answer:kind==='search'?"Organising your requirements and checking each hard condition.":"Working with information in this conversation."};
    });}catch(error){
      // A rejected plan must not remain a queued routing lease and retry forever after recovery.
      // If the commit actually succeeded, kind is no longer routing: leave its queued agent task intact.
      await this.store.transaction(async tx=>{
        const s=await this.store.lock(tx,sessionId);
        const row=await tx.query("UPDATE domain_operations SET status='failed' WHERE id=$1 AND kind='routing' AND status='queued' RETURNING id",[reservation.op]);
        if(!row.rowCount)return;
        const code=error instanceof BusinessError?error.code:'ORCHESTRATOR_PLAN_FAILED',message=error instanceof BusinessError?error.message:"This request could not be scheduled. Please resubmit it later.";
        this.store.message(s,'assistant',message);await this.store.saveState(tx,s);
        await this.store.event(tx,sessionId,{type:'workflow_error',payload:{code,message,operation_id:reservation.op}},`routing-failed:${reservation.op}`);
      }).catch(()=>{/* Preserve the original failure if the database itself is unavailable. */});
      throw error;
    }
  }
  private async queueRecommendation(tx:PoolClient,s:DomainState,operationId:string,cutoff:Date|string,notes:string[]=[]){
    const interest=await prepareBrowserFeedback(tx,s,cutoff);
    const feedback=(await tx.query('SELECT body FROM listing_feedback WHERE session_id=$1 AND consumed_request_id IS NULL AND (created_at <= $2 OR event_id=$3) ORDER BY created_at,event_id',[s.session_id,cutoff,`explicit:${operationId}`])).rows.map(x=>x.body);
    const requestId=randomUUID(),previous=s.recommendation?.state.state_id;
    const behavior_context=await collectBehaviorContext(tx,s.session_id,cutoff,interest.ready);
    // Reserve native results once; only the first batch is initially public.
    const request=recommendationRequestSchema.parse({contract_version:'1.0',request_id:requestId,conversation_id:s.session_id,requirements_version:s.requirements_version,requirements:s.requirements,feedback,behavior_context,...(previous?{previous_state_id:previous}:{}),result_limit:RECOMMENDATION_RESERVOIR_LIMIT});
    await tx.query('INSERT INTO recommendation_calls(id,session_id,operation_id,request,feedback_cutoff,interest_context) VALUES($1,$2,$3,$4,$5,$6)',[requestId,s.session_id,operationId,JSON.stringify(request),cutoff,JSON.stringify(interest)]);
    // The stored cold-start compat state (ignored natively since mpd-v1) is read once here and queued with B's body, so retries resend the identical native body.
    s.latest_request_id=requestId;
    return this.store.queue(tx,s,operationId,'b',{request,cold_start:s.recommendation_cold_start??NOT_EXITED,interpretation_notes:notes},'recommend');
  }
  async recoverRouting(){const rows=(await this.store.pool.query("SELECT session_id,request_id,query,source_request_id FROM domain_operations WHERE kind='routing' AND status='queued' AND COALESCE((intent_plan->>'routing_started_at')::timestamptz,created_at)<now()-interval '30 seconds' ORDER BY created_at LIMIT 2")).rows;await Promise.allSettled(rows.map(r=>this.plan(r.query,r.session_id,r.request_id,r.source_request_id||undefined)));}
  context(sessionId:string,env:TaskEnvelope):ToolContext{return {sessionId,operationId:env.operation_id,agent:env.agent,dataset:this.dataset,deadline:Date.now()+120000,calls:0};}
  async run(agent:TaskEnvelope['agent'],prompt:string,sessionId:string,taskId:string):Promise<string>{
    const {env,body}=await this.store.verifyTask(prompt,sessionId,taskId,agent);const prior=await this.store.pool.query('SELECT envelope FROM agent_result_payloads WHERE task_id=$1',[taskId]);if(prior.rows[0])return JSON.stringify(prior.rows[0].envelope);
    const ctx=this.context(sessionId,env),profile=await loadProfile(agent);let payload:unknown;
    await this.store.pool.query('INSERT INTO tool_traces(id,session_id,operation_id,body) VALUES($1,$2,$3,$4)',[randomUUID(),sessionId,env.operation_id,JSON.stringify({tool:'skill_loaded',agent,sha256:profile.sha256})]);
    if(agent==='a'&&body.input_mode==='structured'){
      // UI answers were validated against the server's question plan. Do not
      // reinterpret explicit choices through a second natural-language model.
      payload={draft:draftSchema.parse(body.draft),feedback:[]};
    }else if(agent==='a'){
      const pending=body.pending||null;
      const base=pending?mergeRequirements(body.requirements,pending):body.requirements;
      const query=body.query;
      const instructions=profile.instructions+(pending?"\nPending clarifications (context only; the latest message answers these questions): "+JSON.stringify(pending.clarifications):'');
      let draft=await extractRequirements(query,base,this.model,instructions,{
        prepareDraft:async draft=>{
          await this.resolveRequirementLocations(draft,base,ctx);
          return {draft:draftSchema.parse(draft),effective_requirements:mergeRequirements(base,draft)};
        },
        onAttempt:async event=>{await this.store.pool.query('INSERT INTO tool_traces(id,session_id,operation_id,body) VALUES($1,$2,$3,$4)',[randomUUID(),sessionId,env.operation_id,JSON.stringify({tool:'requirement_intake_attempt',agent:'a',...event})]);},
      });
      if(pending){
        const effective=mergeRequirements(base,draft);
        draft=draftSchema.parse({...draft,upsert:effective.criteria,remove_fields:[],remove_criterion_ids:body.requirements.criteria.filter((old:any)=>!effective.criteria.some(c=>c.criterion_id===old.criterion_id)).map((c:any)=>c.criterion_id),sources:[...pending.sources,...draft.sources]});
      }
      payload={draft:draftSchema.parse(draft),feedback:this.interpretFeedback(body.latest_query||body.query,body.recommendation,body.source_request_id,env.operation_id)};
    }else if(agent==='b'){
      const req=recommendationRequestSchema.parse(body.request);const rec=await this.recommender.recommend(req,ctx,body.cold_start??NOT_EXITED);rec.warnings.push(...(body.interpretation_notes||[]).map((message:string)=>({code:'GEO_INTERPRETATION',message})));payload=rec;
    }else if(agent==='c'){
      if(!body.recommendation)throw new BusinessError('NO_RECOMMENDATION',"Please search for homes first.");
      const rec=body.recommendation;
      const archive=rec.native?await this.store.loadPayload(rec.native.exchange_ref,sessionId):undefined;
      const enrichment=await this.explanationEnricher.load(rec,ctx);
      payload={explanation:await generateExplanation(body.requirements,rec,this.dataset,body.query||'',this.model,profile.instructions,archive,enrichment,diagnostic=>this.store.savePayload(sessionId,env.operation_id,diagnostic),body.continuation),...(body.continuation?{expanded_recommendation:rec,base_limit:body.base_limit}:{})};
    }else if(body.kind==='daily')payload={kind:'daily',date:singaporeDate()};
    else if(body.kind==='knowledge'||body.kind==='status'){
      if(body.kind==='status'){
        const rows=(await this.store.pool.query('SELECT body FROM workflow_actions WHERE session_id=$1 ORDER BY updated_at DESC LIMIT 5',[sessionId])).rows;
        const labels:Record<string,string>={MANUAL_SENT:"You confirmed sending it yourself; no platform receipt is available",MANUAL_REPLIED:"You pasted a reply; see Follow-up for its analysis and next steps",DRAFT:"Not sent: draft",PENDING_REVIEW:"Not sent: awaiting approval",APPROVED:"Not sent: approved, awaiting execution",SIMULATED:"Local simulation only; no message was sent",QUEUED:"Queued; not yet submitted to the platform",SENDING:"Submitting to the platform; awaiting result",ACCEPTED:"Accepted by platform; this does not mean read or viewing confirmed",DELIVERED:"Delivery receipt received",READ:"Read receipt received",REPLIED:"Agent’s reply received; listing conditions still need confirmation",CONFIRMED:"Both parties’ explicit agreement recorded; see the record for its mode",UNKNOWN:"Sending result unknown. Check the platform record before sending again",FAILED:"Submission failed or the platform reported failure",CANCELLED:"Cancelled"};
        payload={kind:'knowledge',knowledge:{answer:rows.length?rows.map(({body:a})=>`Listing ${a.listing_ids.join(", ")}: ${labels[a.status]||a.status} (${a.mode==='manual'?"Manually recorded by you":a.mode==='live'?"Live platform":"Simulation"}).`).join('\n'):"There are no contact records. Choose an enquiry or viewing from a listing, then edit and approve the draft.",citations:[],retrieval:'owned_action_records',vector_enabled:false}};
      }else payload={kind:'knowledge',knowledge:await this.tools.run(ctx,'knowledge',{query:body.query},()=>this.knowledge.query(body.query))};
    }
    else if(body.kind==='summary'){
      if(!body.recommendation)throw new BusinessError('NO_RECOMMENDATION',"Search for homes before generating a summary.");payload={kind:'summary',document:renderSummary(body.requirements,body.recommendation,this.dataset)};
    }else{
      const s=await this.store.state(sessionId);
      if(!body.source_request_id||body.source_request_id!==s.latest_request_id||body.active_search_operation_id!==s.active_operation_id)throw new BusinessError('SELECTION_STALE',"Choose a home from the current recommendations before requesting follow-up.");
      try{const id=resolveListingSelection(s.recommendation,String(body.query)).listing.listing_id;payload={kind:'action',action:await this.manual.draftFromHistory(s,[id],body.kind==='viewing'?'VIEWING':'MESSAGE',notesFromFollowupQuery(String(body.query)).slice(0,2000))};}
      catch(error){if(error instanceof BusinessError&&['SELECTION_REQUIRED','SELECTION_NOT_IN_RESULT'].includes(error.code))payload={kind:'selection_needed',message:error.message};else throw error;}
    }
    return this.store.transaction(async tx=>{const row=await tx.query('SELECT envelope FROM agent_result_payloads WHERE task_id=$1',[taskId]);if(row.rows[0])return JSON.stringify(row.rows[0].envelope);const ref=await this.store.payload(tx,sessionId,env.operation_id,payload);const output={schema_version:'1.1',agent,operation_id:env.operation_id,requirements_version:env.requirements_version,payload_ref:ref,payload_hash:hash(payload)};await tx.query('INSERT INTO agent_result_payloads(task_id,envelope) VALUES($1,$2) ON CONFLICT DO NOTHING',[taskId,JSON.stringify(output)]);return JSON.stringify(output);});
  }
  private async resolveRequirementLocations(draft:RequirementDraft,base:import('../contracts/recommendation-v1.js').Requirements,ctx:ToolContext){
    for(const intent of draft.location_intents||[]){
        if(intent.category){
          const target={category:intent.category,name:intent.name};
          draft.upsert.push(criterion('place_proximity',intent.operator||'lte',intent.range_m??intent.radius_m??DEFAULT_PROXIMITY_SCALE_M,'m',intent.strength==='soft',{target,distance_type:intent.distance_type,distance_preference:intent.radius_m!==undefined||intent.range_m?'bounded':'nearer',...(intent.strength==='soft'?{weight:intent.weight??1}:{})}));
          if(intent.strength==='hard'&&intent.prefer_closer===true)draft.upsert.push(criterion('place_proximity','lte',DEFAULT_PROXIMITY_SCALE_M,'m',true,{target,distance_type:intent.distance_type,distance_preference:'nearer'}));
          draft.sources.push({field:'place_proximity',quote:intent.quote});
          draft.interpretation_notes=[...(draft.interpretation_notes||[]),`“${intent.name}” has been retained as a ${intent.strength==='soft'?"soft preference":"hard requirement"}; missing station data or route evidence leaves it unverified rather than ignored.`];
          continue;
        }
        const resolved=await this.locations.resolve(ctx,intent.name,intent.explicit_station);
        if(!resolved.entity){draft.clarifications.push(resolved.note);continue;}
        const id=String(resolved.entity.properties.place_id),target={place_id:id,name:String(resolved.entity.properties.name)};
        if(intent.explicit_station&&/^LTA:(MRT|LRT):/.test(id))draft.remove_criterion_ids=[...(draft.remove_criterion_ids||[]),...base.criteria.filter((c:any)=>c.field==='place_proximity'&&c.target?.place_id==='URA:AREA:'+id.slice(8)).map((c:any)=>c.criterion_id)];
        draft.upsert.push(criterion('place_proximity',intent.operator||'lte',intent.range_m??intent.radius_m??DEFAULT_PROXIMITY_SCALE_M,'m',intent.strength==='soft',{target,distance_type:intent.distance_type,distance_preference:intent.radius_m!==undefined||intent.range_m?'bounded':'nearer',...(intent.strength==='soft'?{weight:intent.weight??1}:{})}));
        if(intent.strength==='hard'&&intent.prefer_closer===true)draft.upsert.push(criterion('place_proximity','lte',DEFAULT_PROXIMITY_SCALE_M,'m',true,{target,distance_type:intent.distance_type,distance_preference:'nearer'}));
        draft.sources.push({field:'place_proximity',quote:intent.quote});
        draft.interpretation_notes=[...(draft.interpretation_notes||[]),resolved.note,intent.radius_m?`The distance range of ${intent.radius_m} metres uses ${intent.distance_type==='straight_line'?"straight line":intent.distance_type} measurements.${intent.prefer_closer?"Closer homes are preferred within this range.":''}`:"No distance cap was specified. Closer is preferred, without excluding homes using a default radius."];
      }
  }
  private interpretFeedback(query:string,rec:any,source:string|null,op:string):Feedback[]{
    if(!rec||source!==rec.request_id)return [];
    const m=query.match(/第([一二三四五六七八九十]|10|[1-9])套.*?(太小|不喜欢|喜欢|面积小)/);if(!m)return [];
    const map:Record<string,number>={"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10};const candidate=displayedCandidates(rec)[(map[m[1]]??Number(m[1]))-1];if(!candidate)return [];
    return [feedbackSchema.parse({event_id:`explicit:${op}`,listing_id:candidate.listing.listing_id,occurred_at:new Date().toISOString(),event_type:'explicit_attribute_feedback',payload:{attribute:/小/.test(m[2])?'floor_area_sqft':'listing',sentiment:m[2]==='喜欢'?'positive':'negative',quote:m[0]}})];
  }
  async emitDueReminders(){
    const rows=(await this.store.pool.query("SELECT a.id,a.session_id,a.body FROM workflow_actions a JOIN session_domain_state s ON s.session_id=a.session_id WHERE s.deleted_at IS NULL AND a.body->>'next_followup_at' IS NOT NULL AND (a.body->>'next_followup_at')::timestamptz<=now() AND a.body->>'status'<>'CANCELLED' LIMIT 100")).rows;
    for(const row of rows)await this.store.transaction(async tx=>{
      // A conversation can be deleted between the scan and acquiring its lock.
      const current=await tx.query('SELECT * FROM session_domain_state WHERE session_id=$1 FOR UPDATE',[row.session_id]);
      const s=current.rows[0] as DomainState|undefined;if(!s||s.deleted_at)return;
      const a=row.body,key=`reminder:${row.id}:${a.revision}:${a.next_followup_at}`;
      if((await tx.query('SELECT 1 FROM session_events WHERE session_id=$1 AND event_key=$2',[s.session_id,key])).rowCount)return;
      this.store.message(s,'assistant',`Follow-up reminder due: ${a.subject}. Check the next step in the workspace. No message was sent automatically.`);
      await this.store.event(tx,s.session_id,{type:'action_update',payload:{action_id:row.id,reminder:'due',message:"Follow-up reminder due; no external message was sent automatically.",mode:'simulation'}},key);await this.store.saveState(tx,s);
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
      if((isSearch&&s.active_operation_id!==env.operation_id)||(operation.kind==='more'&&(operation.intent_plan.source_request_id!==s.latest_request_id||operation.intent_plan.source_operation_id!==s.active_operation_id))||s.requirements_version!==env.requirements_version){await tx.query("INSERT INTO handled_results(task_id,status) VALUES($1,'superseded')",[task.id]);await tx.query("UPDATE domain_operations SET status='superseded' WHERE id=$1 AND status='queued'",[env.operation_id]);return [];}
      const wake:{sessionId:string;agent:TaskEnvelope['agent']}[]=[];
      try{
        if(t.status==='failed'){const failure=describeAgentFailure(task.agent,String(t.error||''));throw new BusinessError(failure.code,failure.message);}
        const result=resultEnvelope.parse(JSON.parse(t.output));if(result.agent!==task.agent||result.operation_id!==env.operation_id||result.requirements_version!==env.requirements_version)throw Error('Result identity mismatch');
        const p=(await tx.query('SELECT body,sha256 FROM domain_payloads WHERE ref=$1 AND session_id=$2 AND operation_id=$3',[result.payload_ref,s.session_id,env.operation_id])).rows[0];if(!p||p.sha256!==result.payload_hash||hash(p.body)!==result.payload_hash)throw Error('Result payload mismatch');
        const payload=p.body;
        const next=async(agent:TaskEnvelope['agent'],body:unknown,stage:string)=>{const a=await this.store.queue(tx,s,env.operation_id,agent,body,stage);wake.push(...a.map(x=>({sessionId:s.session_id,agent:x.agent})));};
        if(task.agent==='a'){
          const draft=draftSchema.parse(payload.draft);
          if(draft.clarifications.length||!draft.supported){s.pending=draft;const known=mergeRequirements(s.requirements,draft).criteria;const text=(known.length?"Clear conditions saved provisionally (search has not started):\n"+known.map(c=>'• '+criterionText(c)).join('\n')+'\n\n':'')+"Please clarify:\n"+draft.clarifications.join('\n');this.store.message(s,'assistant',text);await this.store.event(tx,s.session_id,{type:'clarification',payload:{questions:draft.clarifications,operation_id:env.operation_id}},`clarify:${task.id}`);}
          else{
            const requirements=mergeRequirements(s.requirements,draft);if(hash(requirements)!==hash(s.requirements)){s.requirements_version++;s.requirements=requirements;await tx.query('INSERT INTO requirement_revisions(session_id,version,body,sources) VALUES($1,$2,$3,$4)',[s.session_id,s.requirements_version,JSON.stringify(requirements),JSON.stringify(draft.sources)]);}
            s.pending=null;
            for(const fb of payload.feedback??[]) {const event=feedbackSchema.parse(fb);await tx.query('INSERT INTO listing_feedback(session_id,event_id,body) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',[s.session_id,event.event_id,JSON.stringify(event)]);}
            const assignments=await this.queueRecommendation(tx,s,env.operation_id,operation.created_at,draft.interpretation_notes||[]);
            wake.push(...assignments.map(x=>({sessionId:s.session_id,agent:x.agent})));
          }
        }else if(task.agent==='b'){
          const call=(await tx.query('SELECT request,interest_context FROM recommendation_calls WHERE id=$1 AND operation_id=$2',[s.latest_request_id,env.operation_id])).rows[0];if(!call)throw Error('Recommendation call missing');
          const response=withPresentation(validateResponse(payload,call.request),operation.intent_plan?.continuation_refresh?10:5);
          const interestNotice=browserInterestNotice(!!call.interest_context?.ready,response);if(interestNotice)response.warnings.push(interestNotice);
          for(const e of response.evidence){if(e.source_kind==='data_api'){const evidence=(await tx.query('SELECT 1 FROM domain_payloads WHERE ref=$1 AND session_id=$2',[e.source_ref,s.session_id])).rowCount;if(!evidence)throw Error('Evidence source cannot be resolved');}}
          await tx.query('UPDATE recommendation_calls SET response=$2 WHERE id=$1',[s.latest_request_id,JSON.stringify(response)]);
          if(response.status==='error')throw new BusinessError(response.error!.code,recommendationFailureMessage(response.error!.code));
          await tx.query('UPDATE listing_feedback SET consumed_request_id=$2 WHERE session_id=$1 AND event_id=ANY($3::text[]) AND consumed_request_id IS NULL',[s.session_id,s.latest_request_id,call.request.feedback.map((f:Feedback)=>f.event_id)]);
          if(response.status==='needs_clarification'){const questions=response.clarifications.map(x=>x.question);this.store.message(s,'assistant',questions.join('\n'));await this.store.event(tx,s.session_id,{type:'clarification',payload:{questions}},`clarify:${task.id}`);}
          else{s.recommendation=response;s.recommendation_cold_start=coldStartAfterAdoption(s.recommendation_cold_start,response);await next('c',{requirements:s.requirements,recommendation:response,query:operation.query},'explain');}
        }else if(task.agent==='c'){
          const source=payload.explanation.source_request_id;const sourceOperation=(await tx.query('SELECT operation_id FROM recommendation_calls WHERE id=$1',[source])).rows[0]?.operation_id;if(source!==s.latest_request_id||sourceOperation!==s.active_operation_id)throw new BusinessError('RESULT_STALE',"Recommendations have changed; this explanation is no longer current.");
          if(payload.expanded_recommendation){
            if(!s.recommendation||displayLimit(s.recommendation)!==payload.base_limit||hash(withPresentation(s.recommendation,10))!==hash(payload.expanded_recommendation))throw new BusinessError('RESULT_STALE','The requirements or results changed while this request was being processed. Please ask for more again.');
            // The new cards and their explanation are committed in one transaction.
            s.recommendation=payload.expanded_recommendation;
            await tx.query('UPDATE recommendation_calls SET response=$2 WHERE id=$1',[s.latest_request_id,JSON.stringify(s.recommendation)]);
          }
          s.explanation=payload.explanation;this.store.message(s,'assistant',payload.explanation.summary);
          const refinementId=await this.refinement.generate(tx,s);if(refinementId)s.messages.at(-1)!.refinement_id=refinementId;
          await this.store.event(tx,s.session_id,{type:'workflow_result',payload:{explanation:payload.explanation,source_request_id:source}},`result:${task.id}`);
          if(isSearch&&operation.intent_plan?.deferred_summary)await next('d',{kind:'summary',requirements:s.requirements,recommendation:s.recommendation},'followup-summary');
        }else if(payload.kind==='knowledge'){this.store.message(s,'assistant',payload.knowledge.answer,payload.knowledge);await this.store.event(tx,s.session_id,{type:'workflow_result',payload:{knowledge:payload.knowledge}},`knowledge:${task.id}`);}
        else if(payload.kind==='selection_needed'){this.store.message(s,'assistant',payload.message);await this.store.event(tx,s.session_id,{type:'workflow_result',payload:{selection_needed:true,message:payload.message}},`selection:${task.id}`);}
        else if(payload.kind==='daily'){await this.followup.daily(s.session_id,payload.date,tx);this.store.message(s,'assistant',`Generated ${payload.date} conversation and follow-up summary. Download it from Documents. Dates use Asia/Singapore.`);}
        else if(payload.kind==='summary'){
          const source=payload.document.model.source_request_id;const sourceOperation=(await tx.query('SELECT operation_id FROM recommendation_calls WHERE id=$1',[source])).rows[0]?.operation_id;if(source!==s.latest_request_id||sourceOperation!==s.active_operation_id)throw new BusinessError('RESULT_STALE',"Recommendations have changed or are updating. Generate the summary again.");
          const id=randomUUID();await tx.query('INSERT INTO workflow_artifacts(id,session_id,source_request_id,requirements_version,content,sha256) VALUES($1,$2,$3,$4,$5,$6)',[id,s.session_id,source,s.requirements_version,JSON.stringify(payload.document),hash(payload.document)]);const text="Summary generated. Markdown and HTML downloads are available.";this.store.message(s,'assistant',text);await this.store.event(tx,s.session_id,{type:'workflow_result',payload:{artifact_id:id,message:text}},`artifact:${task.id}`);
        }else if(payload.kind==='action'){
          const a=payload.action;const sourceCall=(await tx.query('SELECT operation_id FROM recommendation_calls WHERE id=$1',[a.source_request_id])).rows[0];if(a.source_request_id!==s.latest_request_id||sourceCall?.operation_id!==s.active_operation_id)throw new BusinessError('ACTION_STALE',"Recommendations have changed. Create a new draft.");await tx.query('INSERT INTO workflow_actions(id,session_id,body) VALUES($1,$2,$3)',[a.id,s.session_id,JSON.stringify(a)]);this.store.message(s,'assistant',`Prepared for listing ${a.listing_ids.join(", ")}: ${a.action_type==='VIEWING'?"viewing enquiry":"verification enquiry"} draft.${a.verification_context?.some((v:any)=>v.group==='unverified')?"Every unresolved condition is included in the draft; the home’s verification status is unchanged.":''}Edit and copy the draft in Follow-up, send it yourself and select Confirm sent. Paste and confirm the agent’s reply for analysis and a summary. Viewings still need both parties’ explicit agreement.`);await this.store.event(tx,s.session_id,{type:'action_update',payload:{action_id:a.id,status:a.status,mode:'simulation'}},`draft:${task.id}`);
        }
        await tx.query("INSERT INTO handled_results(task_id,status) VALUES($1,'applied')",[task.id]);await tx.query('UPDATE domain_operations SET status=$2 WHERE id=$1',[env.operation_id,wake.length?'queued':'completed']);
      }catch(error){
        if(error&&typeof error==='object'&&'severity' in error)throw error;
        const detail=error instanceof Error?error.message:"Business validation failed";const message=error instanceof z.ZodError?"The internal result failed business-format validation, so this turn has stopped. Resubmit or clarify your requirements.":detail;await tx.query("INSERT INTO handled_results(task_id,status,error) VALUES($1,'rejected',$2)",[task.id,detail]);await tx.query("UPDATE domain_operations SET status='failed' WHERE id=$1",[env.operation_id]);this.store.message(s,'assistant',`This turn could not be completed: ${message}`);await this.store.event(tx,s.session_id,{type:'workflow_error',payload:{code:error instanceof BusinessError?error.code:'BUSINESS_VALIDATION_FAILED',message,operation_id:env.operation_id}},`failure:${task.id}`);wake.length=0;
      }
      await this.store.saveState(tx,s);return wake;
    });
  }
}
