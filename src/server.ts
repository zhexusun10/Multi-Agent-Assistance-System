import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { parseEnv } from 'node:util';
import pg from 'pg';
import { z } from 'zod';
import { PostgresSaver } from '@langchain/langgraph-checkpoint-postgres';
import { PostgresAgentTaskStore } from './agent-task-store.js';
import { createAgentRegistry,createDomainRegistry } from './agents/registry.js';
import { createMaster } from './agents/master.js';
import { createModel } from './model.js';
import { PostgresSessionEventStore } from './session-store.js';
import { querySchema,runtimeEventSchema } from './types.js';
import { DomainStore,BusinessError,hash } from './stores/domain.js';
import { WorkflowService } from './domain/workflow.js';
import { feedbackSchema } from './contracts/recommendation-v1.js';
import {singaporeDate} from './domain/followup.js';
import {wireBody,type Action} from './domain/actions.js';
import {DeliveryError} from './integrations/messaging.js';
import {displayedCandidates} from './domain/selection.js';
import {INTENT_PARAMETERS} from './agents/orchestrator/intent-templates.js';
import {RAG_PARAMETERS} from './domain/knowledge.js';

const databaseUrl=process.env.DATABASE_URL;
if(!databaseUrl)throw Error('DATABASE_URL is required');
const pool=new pg.Pool({connectionString:databaseUrl,max:20});
const checkpointer=new PostgresSaver(pool),events=new PostgresSessionEventStore(pool),tasks=new PostgresAgentTaskStore(pool);
await checkpointer.setup();await events.setup();await tasks.setup();
const business=process.env.BUSINESS_ENABLED!=='false';
let projectConfig:Record<string,string|undefined>|null=null;
try{projectConfig=parseEnv(await readFile('.env','utf8'));}catch{/* Container deployments can use explicit environment variables. */}
const provider=(process.env.CHAT_MODEL||'openai:gpt-4.1-mini').split(':')[0];
const providerKey=({openai:'OPENAI_API_KEY',anthropic:'ANTHROPIC_API_KEY','google-genai':'GOOGLE_API_KEY'} as Record<string,string>)[provider];
if(projectConfig&&providerKey&&Object.hasOwn(projectConfig,providerKey))process.env[providerKey]=projectConfig[providerKey];
const useModel=process.env.LLM_MODE==='model'||(process.env.LLM_MODE!=='rules'&&!!process.env[providerKey]);
const model=useModel||!business?await createModel():undefined;
const store=new DomainStore(pool,tasks,events);if(business)await store.setup();
const intentModel=useModel?await createModel(undefined,{maxTokens:INTENT_PARAMETERS.max_tokens,temperature:INTENT_PARAMETERS.temperature}):undefined;
const knowledgeModels=useModel?{answer:await createModel(undefined,{maxTokens:2048,temperature:.1}),rewriteModel:await createModel(undefined,{maxTokens:RAG_PARAMETERS.rewrite_max_tokens,temperature:RAG_PARAMETERS.rewrite_temperature}),rerankModel:await createModel(undefined,{maxTokens:768,temperature:.1})}:{};
const workflow=business?new WorkflowService(store,process.env.DATASET_VERSION||'historical-csv-main-0c45bf3',model,intentModel,knowledgeModels):undefined;
let dataInfo:any=null;
if(workflow){
  const capabilities=await workflow.data.request('/api/v1/capabilities') as any;
  const ready=await workflow.data.request('/ready') as any;
  dataInfo=capabilities.listing_snapshot;
  if(capabilities.read_only!==true||capabilities.sql_fallback!==false||ready.status!=='ok')throw Error('Data service readiness contract failed');
  const version=capabilities.listing_snapshot?.dataset_version||capabilities.listing_snapshot?.date;
  if(version!==workflow.dataset)throw Error('DATA_BUNDLE_MISMATCH: configured dataset differs from data service');
  const bundlePath=process.env.DATA_BUNDLE_MANIFEST_PATH;
  if(!bundlePath)throw Error('DATA_BUNDLE_MANIFEST_PATH is required');
  const bundle=JSON.parse(await readFile(bundlePath,'utf8'));
  if(process.env.DATA_MODE==='full'){
    if(bundle.status!=='passed'&&bundle.status!=='ok')throw Error('Full data bundle must have a successful verification report');
    if(bundle.snapshot?.date!==workflow.dataset||bundle.sql?.properties!==dataInfo.expected_counts?.properties||bundle.graph?.listings!==bundle.sql.properties)throw Error('Full data bundle version/count mismatch');
    if(process.env.RECOMMENDATION_MODE==='baseline'&&!capabilities.canonical_assessment_scan)throw Error('Full baseline requires a complete canonical scan');
  }else if(bundle.dataset_version!==workflow.dataset||bundle.expected_counts?.properties!==593)throw Error('Sample data bundle is not verified');
}
const master=createMaster(model,workflow?createDomainRegistry(workflow,checkpointer):createAgentRegistry(model!,checkpointer),{checkpointer,events,tasks,...(workflow?{domain:workflow}:{})});
await master.resumePendingTasks();
const recovery=setInterval(()=>{void workflow?.recoverRouting().catch(e=>console.error('Intent recovery failed',e.name));void master.wakePendingTasks().catch(e=>console.error('Recovery wake failed',e.name));},2000);
const reminders=setInterval(()=>void workflow?.emitDueReminders().catch(e=>console.error('Reminder scan failed',e.name)),15000);
const delivery=setInterval(()=>void workflow?.followup.processOutbox().catch(e=>console.error('Delivery scan failed',e.name)),2000);
const followups=setInterval(()=>void workflow?.followup.reminders().catch(e=>console.error('Follow-up scan failed',e.name)),15000);
const telegramPolling=setInterval(()=>void workflow?.followup.pollTelegram().catch(e=>console.error('Telegram receive failed',e instanceof DeliveryError?e.code:e.name)),5000);
const contactUrl=(process.env.CONTACT_DATABASE_URL||(process.env.DATA_MODE!=='full'?process.env.SAMPLE_PROPERTYGURU_DATABASE_URL:undefined))?.replace('postgresql+psycopg2:','postgresql:');
const contactPool=contactUrl?new pg.Pool({connectionString:contactUrl,max:2,options:'-c default_transaction_read_only=on',statement_timeout:3000}):null;
const host=process.env.GRAPH_HOST||'127.0.0.1',port=Number(process.env.GRAPH_PORT||3001);

async function readRaw(req:IncomingMessage){
  const chunks:Buffer[]=[];let size=0;
  for await(const chunk of req){size+=chunk.length;if(size>65536)throw new BusinessError('BODY_TOO_LARGE','请求内容过大',413);chunks.push(Buffer.from(chunk));}
  return Buffer.concat(chunks);
}
async function readBody(req:IncomingMessage):Promise<any>{try{return JSON.parse((await readRaw(req)).toString('utf8'));}catch(e){if(e instanceof BusinessError)throw e;throw new BusinessError('INVALID_JSON','JSON 格式错误',400);}}
const token=(req:IncomingMessage)=>{const raw=req.headers.authorization?.replace(/^Bearer /,'');if(!raw||!/^[A-Za-z0-9_-]{32,256}$/.test(raw))throw new BusinessError('AUTH_REQUIRED','需要有效的本机会话凭证',401);return raw;};
function startSse(res:ServerResponse){res.writeHead(200,{'content-type':'text/event-stream; charset=utf-8','cache-control':'no-cache',connection:'keep-alive','x-accel-buffering':'no'});res.flushHeaders();const heartbeat=setInterval(()=>{if(!res.destroyed)res.write(': heartbeat\n\n');},15000);res.on('close',()=>clearInterval(heartbeat));return heartbeat;}

const server=createServer(async(req,res)=>{
  const send=(status:number,body:unknown)=>{res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'});res.end(JSON.stringify(body));};
  const url=new URL(req.url||'/',`http://${req.headers.host||'localhost'}`);
  try{
    if(req.method==='GET'&&url.pathname==='/health'){await pool.query('SELECT 1');return send(200,{status:'ok',llm_mode:useModel?'model':'rules',business_enabled:business});}
    if(req.method==='GET'&&url.pathname==='/config')return send(200,{dataset:workflow?.dataset,llm_mode:useModel?'model':'rules',model:useModel?process.env.CHAT_MODEL:null,recommendation_mode:process.env.RECOMMENDATION_MODE||'baseline',action_mode:workflow?.followup.gateway.capabilities().mode||'simulation',integrations:workflow?.followup.gateway.capabilities(),now:new Date().toISOString(),today:singaporeDate(),time_zone:'Asia/Singapore',knowledge_mode:process.env.KNOWLEDGE_MODE||'hybrid_rrf',intent_weights:INTENT_PARAMETERS,rag_parameters:RAG_PARAMETERS,full_snapshot:process.env.DATA_MODE==='full',dataset_counts:dataInfo?.expected_counts||null});
    if(workflow&&url.pathname==='/webhooks/whatsapp'&&req.method==='GET'){const challenge=workflow.followup.verifyChallenge(url.searchParams);res.writeHead(200,{'content-type':'text/plain'});return res.end(challenge);}
    if(workflow&&req.method==='POST'&&/^\/webhooks\/(whatsapp|telegram)$/.test(url.pathname)){const channel=url.pathname.endsWith('whatsapp')?'whatsapp':'telegram',signature=req.headers[channel==='whatsapp'?'x-hub-signature-256':'x-telegram-bot-api-secret-token'];return send(200,await workflow.followup.webhook(channel,await readRaw(req),typeof signature==='string'?signature:''));}
    if(workflow&&req.method==='GET'&&url.pathname==='/locations/catalog')return send(200,await workflow.locations.coverage());
    if(workflow&&req.method==='GET'&&url.pathname.startsWith('/locations/')){const id=decodeURIComponent(url.pathname.slice('/locations/'.length));const record=await workflow.locations.describe(id);return send(record?200:404,record||{detail:'Location not found'});}
    const owner=business?token(req):'';
    if(workflow&&req.method==='POST'&&url.pathname==='/sessions'){const id=await store.create(owner);return send(201,await store.state(id));}
    if(workflow&&req.method==='GET'&&url.pathname==='/state'){const id=z.string().min(1).parse(url.searchParams.get('session_id'));await store.assertOwner(id,owner);return send(200,{...await store.state(id),...await workflow.followup.snapshot(id)});}
    if(workflow&&req.method==='GET'&&/^\/listings\/\d+\/presentation$/.test(url.pathname)){
      const id=z.string().regex(/^\d{1,20}$/).parse(url.pathname.split('/')[2]),session=z.string().min(1).parse(url.searchParams.get('session_id')),request=z.string().uuid().parse(url.searchParams.get('source_request_id'));await store.assertOwner(session,owner);
      const current=await store.state(session);
      if(current.recommendation?.state.coverage.dataset_version!==workflow.dataset)throw new BusinessError('DATASET_CHANGED','房源数据已更新，请按当前需求重新搜索。');
      if(current.latest_request_id!==request||current.recommendation?.request_id!==request||current.recommendation.requirements_version!==current.requirements_version||current.recommendation_operation_id!==current.active_operation_id)throw new BusinessError('PRESENTATION_STALE','推荐已更新，请查看当前房源。');
      if(!displayedCandidates(current.recommendation).some(c=>c.listing.listing_id===id))throw new BusinessError('LISTING_NOT_IN_RESULT','该房源不在当前推荐列表中。',404);
      const result=await workflow.listingPresentation.get(id),latest=await store.state(session);
      if(latest.latest_request_id!==request||latest.requirements_version!==current.requirements_version||latest.active_operation_id!==current.active_operation_id)throw new BusinessError('PRESENTATION_STALE','推荐已更新，请查看当前房源。');
      return send(200,{...result,source_request_id:request});
    }
    if(workflow&&req.method==='GET'&&url.pathname==='/integrations/templates'){const id=z.string().min(1).parse(url.searchParams.get('session_id'));await store.assertOwner(id,owner);return send(200,{templates:await workflow.followup.gateway.templates()});}
    if(workflow&&req.method==='GET'&&url.pathname==='/calendar'){const id=z.string().min(1).parse(url.searchParams.get('session_id'));await store.assertOwner(id,owner);return send(200,await workflow.followup.calendar(id,url.searchParams.get('month')||singaporeDate().slice(0,7)));}
    if(workflow&&req.method==='POST'&&url.pathname==='/followups'){const {session_id,...input}=await readBody(req);const id=z.string().min(1).parse(session_id);await store.assertOwner(id,owner);return send(200,await workflow.followup.task(id,input));}
    if(workflow&&req.method==='POST'&&url.pathname==='/summaries/daily'){const input=z.object({session_id:z.string().min(1),date:z.string()}).strict().parse(await readBody(req));await store.assertOwner(input.session_id,owner);return send(201,await workflow.followup.daily(input.session_id,input.date));}
    if(workflow&&req.method==='GET'&&/^\/actions\/[^/]+\/context$/.test(url.pathname)){
      const id=z.string().uuid().parse(url.pathname.split('/')[2]),sid=z.string().min(1).parse(url.searchParams.get('session_id'));await store.assertOwner(sid,owner);
      const a=(await pool.query('SELECT body FROM workflow_actions WHERE id=$1 AND session_id=$2',[id,sid])).rows[0]?.body as Action|undefined;if(!a)throw new BusinessError('ACTION_NOT_FOUND','动作不存在',404);
      let listing:any=null;if(contactPool)try{listing=(await contactPool.query('SELECT listing_id,agent_name,agency_name,agent_phone,url FROM properties WHERE listing_id=$1',[a.listing_ids[0]])).rows[0]||null;}catch{/* Contact hints unavailable must not block manual verified entry. */}
      const contacts=(await pool.query("SELECT DISTINCT a.body->'recipient' AS recipient FROM workflow_actions a JOIN session_domain_state s ON s.session_id=a.session_id WHERE s.owner_hash=$1 AND a.body->'listing_ids' ? $2 AND a.body->'recipient'->>'verified'='true' AND a.body->'recipient'->>'channel' IN ('whatsapp','telegram') LIMIT 3",[hash(owner),a.listing_ids[0]])).rows.map(r=>r.recipient);
      return send(200,{listing,contacts,wire_body:wireBody(a),note:'历史挂牌电话仅供核对，不能据此判断 WhatsApp 或 Telegram 渠道。已核实渠道仅复用当前用户对同一房源保存的资料。'});
    }
    if(workflow&&req.method==='POST'&&/^\/actions\/[^/]+\/manual-(prepare|sent|reply|analyze|next)$/.test(url.pathname)){
      const id=z.string().uuid().parse(url.pathname.split('/')[2]),body=await readBody(req),session=z.string().min(1).parse(body.session_id);await store.assertOwner(session,owner);const {session_id,...input}=body;
      const kind=url.pathname.split('/')[3];
      if(kind==='manual-prepare')return send(200,await workflow.manual.prepare(session,id,input));
      if(kind==='manual-sent')return send(200,await workflow.manual.sent(session,id,input));
      if(kind==='manual-next')return send(201,await workflow.manual.nextDraft(session,id,input));
      if(kind==='manual-reply'){const reply=await workflow.manual.receive(session,id,input);void workflow.manual.analyze(session,reply.id).catch(()=>console.error('Manual reply analysis deferred'));return send(202,reply);}
      const v=z.object({reply_id:z.string().uuid()}).strict().parse(input);const row=(await pool.query('SELECT id FROM manual_followup_replies WHERE id=$1 AND session_id=$2 AND action_id=$3',[v.reply_id,session,id])).rows[0];if(!row)throw new BusinessError('REPLY_NOT_FOUND','回复不存在',404);
      void workflow.manual.analyze(session,v.reply_id,true).catch(()=>console.error('Manual reply analysis deferred'));return send(202,{status:'processing'});
    }
    if(workflow&&req.method==='POST'&&/^\/actions\/[^/]+\/compose$/.test(url.pathname)){
      const id=z.string().uuid().parse(url.pathname.split('/')[2]),v=z.object({session_id:z.string().min(1),revision:z.number().int(),notes:z.string().max(2000)}).strict().parse(await readBody(req));await store.assertOwner(v.session_id,owner);
      const a=(await pool.query('SELECT body FROM workflow_actions WHERE id=$1 AND session_id=$2',[id,v.session_id])).rows[0]?.body as Action|undefined;if(!a)throw new BusinessError('ACTION_NOT_FOUND','动作不存在',404);if(a.revision!==v.revision)throw new BusinessError('REVISION_CONFLICT','草稿已变化，请刷新。');
      const draft=await workflow.manual.draftFromHistory(await store.state(v.session_id),a.listing_ids,a.action_type,v.notes);return send(200,{body:draft.body,negotiation_notes:draft.negotiation_notes||'',notice:'尚未保存。请核对正文、时间和协商诉求后保存当前修订。'});
    }
    if(workflow&&req.method==='GET'&&url.pathname==='/evidence'){const id=z.string().min(1).parse(url.searchParams.get('session_id')),ref=z.string().uuid().parse(url.searchParams.get('ref'));await store.assertOwner(id,owner);return send(200,{ref,data:await store.loadPayload(ref,id)});}
    if(workflow&&req.method==='GET'&&url.pathname.startsWith('/artifacts/')){
      const id=z.string().uuid().parse(url.pathname.split('/')[2]),sessionId=z.string().min(1).parse(url.searchParams.get('session_id'));await store.assertOwner(sessionId,owner);
      const row=(await pool.query('SELECT content,sha256 FROM workflow_artifacts WHERE id=$1 AND session_id=$2',[id,sessionId])).rows[0];if(!row)throw new BusinessError('ARTIFACT_NOT_FOUND','文档不存在',404);if(hash(row.content)!==row.sha256)throw Error('Artifact integrity mismatch');
      const html=url.searchParams.get('format')==='html';res.writeHead(200,{'content-type':html?'text/html; charset=utf-8':'text/markdown; charset=utf-8','content-disposition':`attachment; filename="rental-summary-${id}.${html?'html':'md'}"`,'x-content-type-options':'nosniff'});return res.end(row.content[html?'html':'markdown']);
    }
    if(workflow&&req.method==='POST'&&/^\/actions\/[^/]+\/(edit|approve|execute|cancel|confirm)$/.test(url.pathname)){
      const parts=url.pathname.split('/'),id=z.string().uuid().parse(parts[2]),body=await readBody(req),sessionId=z.string().min(1).parse(body.session_id);await store.assertOwner(sessionId,owner);const {session_id,...input}=body;return send(200,await workflow.actions.change(sessionId,id,parts[3] as any,input,owner));
    }
    if(workflow&&req.method==='POST'&&url.pathname==='/feedback'){
      const body=z.object({session_id:z.string().min(1),source_request_id:z.string().uuid(),event:feedbackSchema}).strict().parse(await readBody(req));await store.assertOwner(body.session_id,owner);
      if(process.env.RECOMMENDATION_MODE==='native'&&body.event.event_type==='active_view'&&body.event.payload.source==='sample'){
        const p=z.object({source:z.literal('sample'),recommendation_request_id:z.string().uuid(),view_session_id:z.string().uuid(),active_duration_ms:z.number().finite().positive().max(3600000)}).strict().parse(body.event.payload);
        const current=await store.state(body.session_id);
        if(p.recommendation_request_id!==body.source_request_id||!current.recommendation?.native?.ranking.includes(body.event.listing_id))throw new BusinessError('INVALID_FEEDBACK','浏览样例必须引用当前原生推荐中的已验证房源。');
      }
      await store.transaction(async tx=>{const s=await store.lock(tx,body.session_id);if(s.latest_request_id!==body.source_request_id||![...(s.recommendation?.recommendations||[]),...(s.recommendation?.unverified_candidates||[])].some(c=>c.listing.listing_id===body.event.listing_id))throw new BusinessError('FEEDBACK_STALE','反馈必须关联当前列表中的房源。');if(Date.parse(body.event.occurred_at)>Date.now()+60000)throw new BusinessError('INVALID_TIMESTAMP','反馈时间不能在未来。');const old=(await tx.query('SELECT body FROM listing_feedback WHERE session_id=$1 AND event_id=$2',[body.session_id,body.event.event_id])).rows[0];if(old&&hash(old.body)!==hash(body.event))throw new BusinessError('EVENT_ID_CONFLICT','相同反馈 ID 的内容不能改变。');await tx.query('INSERT INTO listing_feedback(session_id,event_id,body) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',[body.session_id,body.event.event_id,JSON.stringify(body.event)]);});return send(202,{status:'recorded',ranking_applied:false});
    }
    if(req.method==='GET'&&url.pathname==='/events'){
      const sessionId=url.searchParams.get('session_id')?.trim();const last=req.headers['last-event-id'];const rawCursor=url.searchParams.get('after')??(Array.isArray(last)?last[0]:last)??'0';const after=Number(rawCursor);
      if(!sessionId||sessionId.length>128||!Number.isSafeInteger(after)||after<0)return send(422,{detail:'Invalid session_id or event cursor'});if(workflow)await store.assertOwner(sessionId,owner);
      const heartbeat=startSse(res),controller=new AbortController();res.on('close',()=>controller.abort());
      try{for await(const {id,event} of master.subscribe(sessionId,after,controller.signal)){if(!res.destroyed)res.write(`id: ${id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);}}finally{clearInterval(heartbeat);if(!res.destroyed)res.end();}return;
    }
    if(req.method==='POST'&&url.pathname==='/run'){
      const parsed=querySchema.parse(await readBody(req));let sessionId=parsed.session_id;
      if(workflow){if(sessionId)await store.assertOwner(sessionId,owner);else sessionId=await store.create(owner);}else sessionId??=randomUUID();
      const heartbeat=startSse(res);
      try{for await(const event of master.stream(parsed.query,sessionId,{requestId:parsed.request_id,sourceRequestId:parsed.source_request_id})){if(!res.destroyed)res.write(`event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);}}finally{clearInterval(heartbeat);if(!res.destroyed)res.end();}return;
    }
    if(req.method==='POST'&&url.pathname==='/runtime/events'){
      if(workflow)throw new BusinessError('RUNTIME_INGRESS_DISABLED','业务模式不接受外部注入内部任务结果',403);
      const body=runtimeEventSchema.parse(await readBody(req));await master.acceptRuntimeEvent(body.session_id,{type:'runtime_event',name:body.name,payload:body.payload});return send(202,{status:'accepted',session_id:body.session_id});
    }
    return send(404,{detail:'Not found'});
  }catch(e){
    if(res.headersSent){if(!res.destroyed){res.write(`event: error\ndata: ${JSON.stringify({type:'error',detail:'请求处理失败，请重新加载会话查看状态。'})}\n\n`);res.end();}return;}
    if(e instanceof BusinessError)return send(e.status,{code:e.code,detail:e.message});
    if(e instanceof DeliveryError)return send(502,{code:e.code,detail:'平台请求未成功完成，请检查配置；发送结果不明时请先核对平台记录。'});
    if(e instanceof z.ZodError)return send(422,{code:'VALIDATION_FAILED',detail:e.issues.map(x=>x.message).join('; ')});
    console.error('Request failed',e instanceof Error?e.name:'UnknownError');return send(500,{code:'INTERNAL_ERROR',detail:'服务处理失败，请检查服务日志。'});
  }
});
const manualReplies=setInterval(()=>{void workflow?.manual.recover().catch(()=>console.error('Manual followup recovery deferred'));},15000);
void workflow?.manual.recover().catch(()=>console.error('Manual followup recovery deferred'));
server.listen(port,host,()=>console.log(`Real Estate LangGraph service: http://${host}:${port}; model=${useModel?'enabled':'rules'}; actions=${workflow?.followup.gateway.capabilities().mode||'simulation'}`));
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>{clearInterval(recovery);clearInterval(reminders);clearInterval(delivery);clearInterval(followups);clearInterval(telegramPolling);clearInterval(manualReplies);server.closeAllConnections();server.close(()=>void Promise.all([pool.end(),contactPool?.end()]).then(()=>process.exit(0)));});
