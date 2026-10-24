/** Opt-in integration acceptance: real SQL/KG/native tools, isolated session schema.
 * No API interception. Only native tool scheduling is deterministic. A card's
 * structured answers bypass language extraction, exactly as in production.
 * REFINEMENT_REAL_MODEL=1 enables at most THREE paid requests (SDK retries=0).
 */
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {createServer} from 'node:http';
import {spawn,type ChildProcess} from 'node:child_process';
import {createWriteStream} from 'node:fs';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import pg from 'pg';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {DomainStore,BusinessError} from '../src/stores/domain.js';
import {PostgresAgentTaskStore} from '../src/agent-task-store.js';
import {PostgresSessionEventStore} from '../src/session-store.js';
import {WorkflowService} from '../src/domain/workflow.js';
import {draftSchema} from '../src/domain/requirements.js';
import {createModel} from '../src/model.js';
import {BehaviorService} from '../src/domain/behavior.js';
import {nativeSelectionSchema} from '../src/contracts/recommendation-native.js';

if(process.env.REFINEMENT_REAL_TEST!=='1')throw Error('Opt in with REFINEMENT_REAL_TEST=1; see the acceptance document.');
const root=process.cwd(),runId=randomUUID().replaceAll('-',''),runtime=resolve(root,'data/.runtime/refinement-real-'+runId);
await mkdir(runtime,{recursive:true});
const nativePort=Number(process.env.REFINEMENT_NATIVE_PORT||8114),graphPort=Number(process.env.REFINEMENT_GRAPH_PORT||3024),webPort=Number(process.env.REFINEMENT_WEB_PORT||8031);
const schema='refinement_real_'+runId,owner=randomUUID()+randomUUID(),children:ChildProcess[]=[],logs:ReturnType<typeof createWriteStream>[]=[],calls:string[]=[];
const admin=new pg.Pool({connectionString:process.env.DATABASE_URL});
let pool:pg.Pool|undefined,server:ReturnType<typeof createServer>|undefined,worker:Promise<void>|undefined,paidEnabled=false;
const usePaid=process.env.REFINEMENT_REAL_MODEL==='1';
const report:any={status:'running',native_controller:'scripted scheduling; unchanged native filtering/scoring/selection/gate',real_services:['PostgreSQL','Neo4j','data HTTP','native Recommendation HTTP','application workflow','web API proxy','Chromium'],paid_request_limit:3,checks:[]};
function child(name:string,command:string,args:string[],extra:Record<string,string>={}){
  const log=createWriteStream(resolve(runtime,name+'.log'));logs.push(log);
  const proc=spawn(command,args,{cwd:root,env:{...process.env,...extra},windowsHide:true,stdio:['ignore','pipe','pipe']});children.push(proc);
  proc.stdout!.pipe(log,{end:false});proc.stderr!.pipe(log,{end:false});
  return proc;
}
async function ready(url:string,proc?:ChildProcess){
  for(let i=0;i<100;i++){
    if(proc?.exitCode!=null)throw Error('Integration child exited; see '+runtime);
    try{const r=await fetch(url,{signal:AbortSignal.timeout(1000)});if(r.ok)return await r.json();}catch{}
    await new Promise(r=>setTimeout(r,250));
  }throw Error('Service readiness timeout: '+url);
}
async function finished(proc:ChildProcess){return await new Promise<number|null>((ok,no)=>{proc.once('error',no);proc.once('exit',ok);});}
try{
  assert.equal((await ready('http://127.0.0.1:8102/health')).controller,'live','production controller must stay live');
  await admin.query(`CREATE SCHEMA "${schema}"`);
  pool=new pg.Pool({connectionString:process.env.DATABASE_URL,options:`-c search_path=${schema}`});
  const tasks=new PostgresAgentTaskStore(pool),events=new PostgresSessionEventStore(pool),store=new DomainStore(pool,tasks,events);
  await tasks.setup();await events.setup();await store.setup();
  const python=resolve(root,'.venv/Scripts/python.exe');
  const native=child('native',python,['-X','utf8','-m','uvicorn','recommendation.app:app','--host','127.0.0.1','--port',String(nativePort)],{
    RECOMMENDATION_CONTROLLER:'scripted',RECOMMENDATION_DATA_MODE:'database',RECOMMENDATION_STATE_PATH:resolve(runtime,'native.sqlite')});
  assert.equal((await ready(`http://127.0.0.1:${nativePort}/health`,native)).controller,'scripted');
  process.env.RECOMMENDATION_MODE='native';process.env.RECOMMENDATION_API_URL=`http://127.0.0.1:${nativePort}`;process.env.REQUIREMENT_REFINEMENT_MODE='native';
  const realModel=usePaid?await createModel(undefined,{maxTokens:4096,temperature:0,maxRetries:0}):undefined;
  // Explicitly exercise the existing source-validated recovery on the first
  // round, saving the paid budget for one real final writer/auditor scenario.
  const guardedModel={withStructuredOutput(shape:any,options:any){return {invoke:async(messages:any,config:any)=>{
    if(!paidEnabled||!realModel)throw Error('OFFLINE_ACCEPTANCE_RECOVERY');
    if(calls.length>=3)throw Error('ACCEPTANCE_MODEL_BUDGET_REACHED');
    calls.push(options.name);console.log(JSON.stringify({model_request:calls.length,kind:options.name}));
    return realModel.withStructuredOutput(shape,options).invoke(messages,config);
  }};}} as unknown as BaseChatModel;
  const dataset=process.env.DATASET_VERSION!;
  assert(dataset);const workflow=new WorkflowService(store,dataset,guardedModel),behavior=new BehaviorService(store);
  // Regression for real runtime error: deleted conversation must not poison
  // the reminder batch or receive a new message; active reminders stay idempotent.
  const active=await store.create(owner),deleted=await store.create(owner);
  for(const sid of [active,deleted])await pool.query('INSERT INTO workflow_actions(id,session_id,body) VALUES($1,$2,$3)',[randomUUID(),sid,JSON.stringify({status:'DRAFT',revision:1,subject:'Isolated acceptance reminder',next_followup_at:'2026-01-01T00:00:00Z'})]);
  await store.deleteSession(deleted,owner);await workflow.emitDueReminders();await workflow.emitDueReminders();
  assert.equal((await store.state(active)).messages.length,1);
  assert.equal((await pool.query('SELECT jsonb_array_length(messages) AS n FROM session_domain_state WHERE session_id=$1',[deleted])).rows[0].n,0);
  report.checks.push('deleted-session reminders skipped; active reminder delivered once');
  const session=await store.create(owner),op=randomUUID(),query='Find homes within 3 km of NUS for at most SGD 2500 per month';
  const draft=draftSchema.parse({upsert:[
    {criterion_id:'monthly-rent',field:'price',operator:'lte',value:2500,unit:'SGD/month',strength:'hard'},
    {criterion_id:'nus-radius',field:'place_proximity',operator:'lte',value:3000,unit:'m',strength:'hard',target:{place_id:'OSM:way:54519165',name:'National University of Singapore'},distance_type:'straight_line',distance_preference:'bounded'}
  ],remove_fields:[],clarifications:[],sources:[],supported:true});
  await store.transaction(async tx=>{const s=await store.lock(tx,session);
    await tx.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind) VALUES($1,$2,$3,$4,'search')",[op,session,randomUUID(),query]);
    s.active_operation_id=op;store.message(s,'user',query);
    await store.queue(tx,s,op,'a',{input_mode:'structured',draft,query,latest_query:query,requirements:s.requirements,pending:null,recommendation:null},'initial');await store.saveState(tx,s);
  });
  async function runRound(){for(const agent of ['a','b','c'] as const){
    const task=await tasks.claimNext(session,agent);assert(task,'missing '+agent+' task');
    const output=await workflow.run(agent,task.prompt,session,task.id);await tasks.finish(task.id,output,null);await workflow.apply({...task,status:'completed',output});
    const failed=(await pool!.query("SELECT status FROM domain_operations WHERE id=($1::jsonb->>'operation_id')::uuid",[JSON.stringify(JSON.parse(task.prompt))])).rows[0];
    assert.notEqual(failed?.status,'failed','workflow rejected '+agent);console.log(JSON.stringify({stage:agent,status:'passed'}));
  }}
  await runRound();
  const before=await store.state(session),firstSelection=nativeSelectionSchema.parse(before.recommendation?.native?.selection);
  assert.equal(before.refinement_plans.length,1);assert.equal(firstSelection.cold_start.active,true);
  assert.equal(before.recommendation!.recommendations.length,5);assert.equal(before.recommendation!.state.counts.eligible,239);
  const kinds=before.refinement_plans[0].questions.map(q=>q.kind);
  assert(kinds.includes('bedrooms')&&kinds.includes('ideal_rent'));assert(kinds.length<=3);
  assert.equal((before.explanation as any).generation.mode,'evidence_recovery');assert.equal(calls.length,0);
  report.initial={pool:239,displayed:5,questions:kinds,cold_start:firstSelection.cold_start,explanation_mode:'evidence_recovery'};
  report.checks.push('real first A/B/C: source 21431, eligible 239, Top5, active=true, three-question card');
  let submitCount=0;const submitted:any[]=[];
  server=createServer(async(req,res)=>{
    const send=(status:number,value:any)=>{res.writeHead(status,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify(value));};
    const url=new URL(req.url||'/',`http://127.0.0.1:${graphPort}`);
    try{
      if(url.pathname==='/health')return send(200,{status:'ok'});
      if(url.pathname==='/config')return send(200,{dataset,today:'2026-10-24',action_mode:'simulation',recommendation_mode:'native',browser_interest:{enabled:true}});
      assert.equal(req.headers.authorization,'Bearer '+owner);await store.assertOwner(session,owner);
      if(url.pathname==='/behavior/views'&&req.method==='POST'){
        const chunks=[];for await(const b of req)chunks.push(b);return send(200,await behavior.record(owner,JSON.parse(Buffer.concat(chunks).toString())));
      }
      if(url.pathname==='/behavior/summary')return send(200,await behavior.summary(session));
      if(url.pathname==='/state')return send(200,{...await store.state(session),...await workflow.followup.snapshot(session)});
      if(url.pathname==='/events'){
        res.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-cache','connection':'keep-alive'});res.flushHeaders();res.write(': connected\n\n');
        let cursor=Number(url.searchParams.get('after')||0),busy=false;
        const tick=setInterval(async()=>{if(busy)return;busy=true;try{for(const entry of await events.list(session,cursor)){cursor=entry.id;if(!res.destroyed)res.write(`id: ${entry.id}\nevent: ${entry.event.type}\ndata: ${JSON.stringify(entry.event)}\n\n`);}}finally{busy=false;}},200);
        res.on('close',()=>clearInterval(tick));return;
      }
      const match=url.pathname.match(/^\/refinements\/([^/]+)\/submit$/);
      if(match&&req.method==='POST'){
        const chunks=[];for await(const b of req)chunks.push(b);const {session_id,...body}=JSON.parse(Buffer.concat(chunks).toString());assert.equal(session_id,session);
        submitCount++;submitted.push(body);const result=await workflow.refinement.submit(session,match[1],body);send(202,result);
        paidEnabled=usePaid;worker=runRound();worker.catch(()=>{});return;
      }
      throw Error('Unexpected browser route '+url.pathname);
    }catch(error){send(error instanceof BusinessError?error.status:500,{detail:error instanceof Error?error.message:String(error)});}
  });
  await new Promise<void>(ok=>server!.listen(graphPort,'127.0.0.1',ok));
  const web=child('web',python,['-X','utf8','-m','uvicorn','api.main:app','--host','127.0.0.1','--port',String(webPort)],{GRAPH_SERVICE_URL:`http://127.0.0.1:${graphPort}`});
  await ready(`http://127.0.0.1:${webPort}/health`,web);
  const identity=resolve(runtime,'browser-session.json');await writeFile(identity,JSON.stringify({owner,session,source:before.latest_request_id,plan:before.refinement_plans[0],url:`http://127.0.0.1:${webPort}`}));
  const browser=child('browser',python,['-X','utf8','scripts/test_refinement_real_ui.py',identity]);
  const browserExit=await finished(browser);await worker;
  assert.equal(browserExit,0,'Browser acceptance failed; see '+runtime);
  const after=await store.state(session);assert.equal(submitCount,1);assert.equal(after.requirements_version,before.requirements_version+1);
  assert.notEqual(after.latest_request_id,before.latest_request_id);assert.equal(after.recommendation!.state.counts.eligible,5);
  const lastSelection=nativeSelectionSchema.parse(after.recommendation?.native?.selection);
  assert.equal(after.recommendation!.recommendations.length,5);assert.equal(lastSelection.cold_start.active,false);
  assert.equal(after.refinement_plans.length,1);assert.equal(after.refinement_plans[0].status,'SUBMITTED');
  assert.equal(after.requirements.criteria.find(c=>c.field==='price'&&c.strength==='hard')!.value,2500);
  assert(!after.requirements.criteria.some(c=>c.field==='price'&&c.strength==='soft'));
  assert(!after.messages.some(m=>m.text.includes('This turn could not be completed')));assert.equal(after.pending_operations.length,0);
  const exchange:any=await store.loadPayload(after.recommendation!.native!.exchange_ref,session);
  assert(exchange.request.previous_state_id,'second native request must continue the persisted state');
  const initialExchange:any=await store.loadPayload(before.recommendation!.native!.exchange_ref,session);
  assert.equal(exchange.request.previous_state_id,initialExchange.response.state.state_id);
  assert.equal(exchange.response.execution.model_calls,0);assert.equal(initialExchange.response.execution.model_calls,0);
  assert.deepEqual(await workflow.refinement.submit(session,before.refinement_plans[0].id,submitted[0]),{status:'queued',operation_id:after.active_operation_id});
  await assert.rejects(()=>workflow.refinement.submit(session,before.refinement_plans[0].id,{...submitted[0],submission_id:randomUUID()}),/already been submitted/);
  const statuses=(await pool.query('SELECT status FROM domain_operations WHERE session_id=$1',[session])).rows;assert(statuses.every(r=>r.status==='completed'));
  // Retain diagnostics before dropping the schema, even if a strict live-model
  // acceptance assertion fails. This avoids paying to reproduce lost evidence.
  const explanation=after.explanation as any;
  await writeFile(resolve(runtime,'after-state.json'),JSON.stringify(after));
  if(explanation.generation.diagnostic_ref)await writeFile(resolve(runtime,'explanation-diagnostic.json'),JSON.stringify(await store.loadPayload(explanation.generation.diagnostic_ref,session),null,2));
  assert.equal((await ready('http://127.0.0.1:8102/health')).controller,'live');
  report.final={pool:5,displayed:5,cold_start:lastSelection.cold_start,explanation_generation:explanation.generation};
  report.model_requests=calls;report.browser=JSON.parse(await readFile(resolve(runtime,'browser-report.json'),'utf8'));
  report.checks.push('real browser POST -> structured A -> native HTTP B -> source-backed C -> SSE state refresh','native SQLite chain continues across rounds','budget retained without unwanted rent preference','active=false stops further questions','one submission and server idempotency; conflicting replay rejected','both operations completed, no unfinished message','production live controller unchanged');
  if(usePaid){assert.equal(explanation.generation.mode,'llm_grounded');assert.equal(explanation.generation.semantic_audit,'passed');}
  report.status='passed';
}catch(error){report.status='failed';report.error=error instanceof Error?error.message:String(error);throw error;
}finally{
  if(server){server.closeAllConnections();await new Promise<void>(ok=>server!.close(()=>ok()));}
  for(const proc of children.reverse())if(proc.exitCode===null){const ended=new Promise<void>(ok=>proc.once('exit',()=>ok()));proc.kill();await ended;}
  for(const log of logs)log.end();
  await pool?.end();assert(/^refinement_real_[a-f0-9]{32}$/.test(schema));await admin.query(`DROP SCHEMA IF EXISTS "${schema}" CASCADE`);await admin.end();
  report.schema_cleanup=true;report.model_requests=calls;await writeFile('evaluation/reports/requirement-refinement-real.json',JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({status:report.status,model_calls:calls.length,logs:runtime}));
}
