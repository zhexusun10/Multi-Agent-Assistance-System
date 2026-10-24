import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import pg from 'pg';
import {DomainStore,hash} from '../src/stores/domain.js';
import {PostgresAgentTaskStore} from '../src/agent-task-store.js';
import {PostgresSessionEventStore} from '../src/session-store.js';

test('customer message acceptance: real HTTP orchestration, time, negotiation and legacy copy snapshots',{skip:process.env.BUSINESS_LIVE!=='1',timeout:100000},async t=>{
 const pool=new pg.Pool({connectionString:process.env.DATABASE_URL}),store=new DomainStore(pool,new PostgresAgentTaskStore(pool),new PostgresSessionEventStore(pool));await store.setup();
 const owner=randomUUID()+randomUUID(),sid=await store.create(owner),request=randomUUID(),op=randomUUID(),rec=JSON.parse(await readFile('tests/fixtures/recommendation-response.json','utf8'));
 const headers={authorization:'Bearer '+owner,'content-type':'application/json'},checks:string[]=[],query='请为房源ID 23583402 生成看房询问草稿，逐项询问尚未核实的条件。';
 const api=async(path:string,body?:unknown)=>{const r=await fetch('http://127.0.0.1:8010/api/'+path,{method:body?'POST':'GET',headers,body:body?JSON.stringify(body):undefined});return {status:r.status,body:await r.json()};};
 const state=async()=>(await api('state?session_id='+sid)).body;
 rec.request_id=request;rec.conversation_id=sid;rec.requirements_version=1;rec.recommendations[0].listing={...rec.recommendations[0].listing,title:'Gold Coast Condo',listing_id:'23583402',price:1600};
 rec.recommendations[0].criterion_results=[{criterion_id:'budget',status:'pass',observed_value:1600,unit:'SGD/month',evidence_ids:[]},{criterion_id:'bath',status:'unknown',observed_value:null,evidence_ids:['test-private-bath-unknown']}];rec.unverified_candidates=rec.recommendations;rec.recommendations=[];
 await store.transaction(async tx=>{const s=await store.lock(tx,sid);Object.assign(s,{recommendation:rec,latest_request_id:request,requirements_version:1,active_operation_id:op,requirements:{listing_type:'RENT',criteria:[{criterion_id:'budget',field:'price',operator:'lte',value:2000,unit:'SGD/month',strength:'hard'},{criterion_id:'bath',field:'amenities',operator:'eq',value:'private_bathroom',strength:'hard'}]}});await store.saveState(tx,s);await tx.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind) VALUES($1,$2,$3,'customer-message-acceptance','search')",[op,sid,randomUUID()]);await tx.query('INSERT INTO recommendation_calls(id,session_id,operation_id,request,response) VALUES($1,$2,$3,$4,$5)',[request,sid,op,'{}',JSON.stringify(rec)]);});
 let action:any;
 try{
 await t.test('the actual card command routes to Follow-up without leaking commands into a negotiation',async()=>{
   const r=await fetch('http://127.0.0.1:8010/api/query',{method:'POST',headers,body:JSON.stringify({session_id:sid,request_id:randomUUID(),source_request_id:request,query})});assert.equal(r.status,200);await r.text();
   const until=Date.now()+60000;while(Date.now()<until){action=(await state()).actions.at(-1);if(action)break;await new Promise(resolve=>setTimeout(resolve,150));}
   assert(action,'Follow-up draft not created');assert.equal(action.action_type,'VIEWING');assert(!action.negotiation_notes);assert(action.body.includes('Gold Coast Condo'));assert((action.body).toLowerCase().includes("does the room have a private bathroom"));assert(!action.body.includes('生成看房询问草稿'));assert(!action.body.includes('快照'));assert.equal(action.verification_context[0].unresolved[0].criterion_id,'bath');assert.equal((await state()).recommendation.recommendations.length,0);checks.push('actual UI query → D agent → natural inquiry, with private-bath unknown retained internally');
 });
 await t.test('the copy payload has real negotiation and Singapore local time, and the reviewed body remains unsent',async()=>{
   const slots={starts_at:'2026-10-21T06:00:00Z',ends_at:'2026-10-21T07:00:00Z',time_zone:'Asia/Singapore'};
   const r=await api(`actions/${action.id}/edit`,{session_id:sid,revision:1,subject:action.subject,body:action.body,recipient:action.recipient,slots,next_followup_at:null,negotiation_notes:'希望月租协商到1500新币。'});assert.equal(r.status,200);
   const copy=await api(`actions/${action.id}/manual-prepare`,{session_id:sid,revision:2});assert.equal(copy.status,200);assert(copy.body.body.includes('协商诉求：希望月租协商到1500新币。'));assert(copy.body.body.includes('2026年10月21日（周三） 2:00 PM–3:00 PM（新加坡时间）'));assert(!copy.body.body.includes('2026-10-21T'));assert(!copy.body.body.includes('需双方'));assert.equal((await state()).actions.find((a:any)=>a.id===action.id).status,'PENDING_REVIEW');checks.push('copy body contains genuine price negotiation once; UTC06–07 converted to SG14–15; copying does not send');
 });
 const legacy={...action,id:randomUUID(),revision:1,body:`您好，我对 Gold Coast Condo（房源 23583402，快照月租 SGD 1600） 感兴趣。请核实当前是否仍可租，以及租期、家具、额外费用和看房安排。\n我希望进一步了解并协商看房。以下时间仅为候选，需双方明确确认；这不是已经预约成功。\n我的咨询与协商诉求（待双方协商）：${query}\n数据来自历史快照，尚未核实当前可租状态。谢谢。`,slots:null,negotiation_notes:undefined,manual_prepared:undefined};
 await pool.query('INSERT INTO workflow_actions(id,session_id,body) VALUES($1,$2,$3)',[legacy.id,sid,JSON.stringify(legacy)]);
 await t.test('legacy drafts require rewriting before a new copy; regeneration is read-only until save',async()=>{
   const rejected=await api(`actions/${legacy.id}/manual-prepare`,{session_id:sid,revision:1});assert.equal(rejected.body.code,'DRAFT_REWRITE_REQUIRED');
   const compose=await api(`actions/${legacy.id}/compose`,{session_id:sid,revision:1,notes:query});assert.equal(compose.status,200);assert.equal(compose.body.negotiation_notes,'');assert(!compose.body.body.includes('逐项询问'));assert(!compose.body.body.includes('数据来自'));assert.equal((await state()).actions.find((a:any)=>a.id===legacy.id).body,legacy.body);checks.push('legacy draft copy blocked until rewritten; recompose removes router notes without overwriting a user edit');
 });
 await t.test('already copied historical text is still acknowledged exactly after a formatting upgrade',async()=>{
   const id=randomUUID(),body=legacy.body+'\n候选看房时间：2026-10-21T06:00:00Z 至 2026-10-21T07:00:00Z。',content_hash=hash({revision:1,body});
   const a={...legacy,id,manual_prepared:{revision:1,body,content_hash,requirements_snapshot:(await state()).requirements,prepared_at:new Date().toISOString()}};
   await pool.query('INSERT INTO workflow_actions(id,session_id,body) VALUES($1,$2,$3)',[id,sid,JSON.stringify(a)]);
   const r=await api(`actions/${id}/manual-sent`,{session_id:sid,revision:1,content_hash,confirmed_sent:true});assert.equal(r.status,200);assert.equal(r.body.manual.message_body,body);assert.equal(r.body.mode,'manual');checks.push('historical copy snapshot remains recordable verbatim; no retrospective rewriting of an already sent message');
 });
 await writeFile('data/.runtime/customer-message-ui-fixture.json',JSON.stringify({session_id:sid,owner,action_id:action.id,legacy_id:legacy.id}));
 await writeFile('evaluation/reports/customer-message-business.json',JSON.stringify({checked_at:new Date().toISOString(),status:'passed',fixture:'isolated controlled Gold Coast example from the user report',messages_sent_externally:0,checks},null,2));
 }finally{await pool.end();}
});
