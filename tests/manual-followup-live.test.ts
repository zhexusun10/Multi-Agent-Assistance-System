import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import pg from 'pg';
import {DomainStore,hash} from '../src/stores/domain.js';
import {PostgresAgentTaskStore} from '../src/agent-task-store.js';
import {PostgresSessionEventStore} from '../src/session-store.js';
import {ActionService,wireBody,type Action} from '../src/domain/actions.js';
import {ManualFollowupService} from '../src/domain/manual-followup.js';
import {FollowupService,singaporeDate} from '../src/domain/followup.js';
import type {Requirements} from '../src/contracts/recommendation-v1.js';

test('manual correspondence business acceptance with real PostgreSQL and no messaging credentials',{skip:process.env.BUSINESS_LIVE!=='1',timeout:90000},async t=>{
 const pool=new pg.Pool({connectionString:process.env.DATABASE_URL}),store=new DomainStore(pool,new PostgresAgentTaskStore(pool),new PostgresSessionEventStore(pool));await store.setup();
 const manual=new ManualFollowupService(store),actions=new ActionService(store),followup=new FollowupService(store),checks:string[]=[];
 const requirements:Requirements={listing_type:'RENT',criteria:[{criterion_id:'monthly-rent',field:'price',operator:'lte',value:2000,unit:'SGD/month',strength:'hard'},{criterion_id:'private-bath',field:'amenities',operator:'eq',value:'private_bathroom',strength:'hard'}]};
 const fixture=async(type:'MESSAGE'|'VIEWING'='MESSAGE')=>{
   const owner=randomUUID()+randomUUID(),session=await store.create(owner),rec=JSON.parse(await readFile('tests/fixtures/recommendation-response.json','utf8')),request=randomUUID(),op=randomUUID();rec.request_id=request;rec.conversation_id=session;rec.requirements_version=1;rec.recommendations[0].listing.price=1900;
   await store.transaction(async tx=>{const s=await store.lock(tx,session);Object.assign(s,{recommendation:rec,latest_request_id:request,requirements_version:1,active_operation_id:op,requirements:structuredClone(requirements)});store.message(s,'user','测试样例：预算2000，要求独立卫浴。');await store.saveState(tx,s);await tx.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind) VALUES($1,$2,$3,'manual-acceptance-test','search')",[op,session,randomUUID()]);await tx.query('INSERT INTO recommendation_calls(id,session_id,operation_id,request,response) VALUES($1,$2,$3,$4,$5)',[request,session,op,'{}',JSON.stringify(rec)]);});
   const a=actions.draft(await store.state(session),[rec.recommendations[0].listing.listing_id],type);if(type==='VIEWING')a.slots={starts_at:new Date(Date.now()+86400000).toISOString(),ends_at:new Date(Date.now()+90000000).toISOString(),time_zone:'Asia/Singapore'};
   await pool.query('INSERT INTO workflow_actions(id,session_id,body) VALUES($1,$2,$3)',[a.id,session,JSON.stringify(a)]);return {a,session,owner};
 };
 const read=async(id:string)=>(await pool.query('SELECT body FROM workflow_actions WHERE id=$1',[id])).rows[0].body as Action;
 const send=async(f:Awaited<ReturnType<typeof fixture>>)=>{const p=await manual.prepare(f.session,f.a.id,{revision:f.a.revision});return manual.sent(f.session,f.a.id,{revision:p.revision,content_hash:p.content_hash,confirmed_sent:true});};
 try{
   const f=await fixture('VIEWING');let prepared:Awaited<ReturnType<typeof manual.prepare>>;
   await t.test('copying preserves draft; unchanged reviewed text can be acknowledged once without an address/token',async()=>{
     prepared=await manual.prepare(f.session,f.a.id,{revision:1});assert.equal(prepared.body,wireBody(f.a));assert.equal((await read(f.a.id)).status,'DRAFT');
     await assert.rejects(manual.sent(f.session,f.a.id,{revision:1,content_hash:'0'.repeat(64),confirmed_sent:true}),/正文已变化/);
     await assert.rejects(manual.sent(f.session,f.a.id,{revision:1,content_hash:prepared.content_hash,confirmed_sent:false}));
     const sent=await send(f);assert.equal(sent.status,'MANUAL_SENT');assert.equal(sent.mode,'manual');assert.equal(sent.execution,null);assert.equal(sent.manual!.message_body,prepared.body);assert.equal(sent.manual!.requirements_snapshot.criteria[0].value,2000);
     await sendAcknowledgementAgain();async function sendAcknowledgementAgain(){await manual.sent(f.session,f.a.id,{revision:1,content_hash:prepared.content_hash,confirmed_sent:true});}
     assert.equal(Number((await pool.query("SELECT count(*) FROM followup_events WHERE action_id=$1 AND kind='manual_sent'",[f.a.id])).rows[0].count),1);
     assert.equal(Number((await pool.query('SELECT count(*) FROM message_outbox WHERE action_id=$1',[f.a.id])).rows[0].count),0);checks.push('copy is not send; exact revision/hash; explicit acknowledgement; no token/address/outbox/provider receipt; duplicate acknowledgement deduplicated');
   });
   const replyId=randomUUID(),text='房源仍可租。月租2200。没有独立卫浴。周六可以看房，具体时间再协商。';
   await t.test('reply belongs to one sent action, persists once, and closes only awaiting-reply reminders',async()=>{
     await followup.task(f.session,{action_id:f.a.id,kind:'await_reply',due_at:new Date(Date.now()+86400000).toISOString()});await followup.task(f.session,{action_id:f.a.id,kind:'viewing_reminder',due_at:new Date(Date.now()+86400000).toISOString()});
     const other=await fixture();await assert.rejects(manual.receive(other.session,other.a.id,{reply_id:randomUUID(),text,confirmed_received:true}),/先核对草稿/);
     await assert.rejects(manual.receive(other.session,f.a.id,{reply_id:randomUUID(),text,confirmed_received:true}),/不存在/);
     await manual.receive(f.session,f.a.id,{reply_id:replyId,text,confirmed_received:true});await manual.receive(f.session,f.a.id,{reply_id:replyId,text,confirmed_received:true});await assert.rejects(manual.receive(f.session,f.a.id,{reply_id:replyId,text:'changed',confirmed_received:true}),/编号已使用/);
     assert.equal((await read(f.a.id)).status,'MANUAL_REPLIED');const tasks=(await pool.query('SELECT kind,status FROM followup_tasks WHERE action_id=$1',[f.a.id])).rows;assert.equal(tasks.find(x=>x.kind==='await_reply').status,'DONE');assert.equal(tasks.find(x=>x.kind==='viewing_reminder').status,'OPEN');
     assert.equal(Number((await pool.query('SELECT count(*) FROM manual_followup_replies WHERE action_id=$1',[f.a.id])).rows[0].count),1);assert.equal(Number((await pool.query('SELECT count(*) FROM messaging_inbound WHERE action_id=$1',[f.a.id])).rows[0].count),0);checks.push('owner/action isolation; no reply before send; durable raw reply; reply ID conflict; selective reminder completion; not a WhatsApp inbound receipt');
   });
   await t.test('recovery analyzes against sent requirements, detects real conflicts and never confirms a viewing',async()=>{
     await store.transaction(async tx=>{const s=await store.lock(tx,f.session);s.requirements.criteria[0].value=3000;await store.saveState(tx,s);});
     await manual.recover();const reply=(await pool.query('SELECT * FROM manual_followup_replies WHERE id=$1',[replyId])).rows[0];assert.equal(reply.analysis_state,'READY');assert.equal(reply.analysis.checks.find((c:any)=>c.criterion_id==='monthly-rent').status,'reported_conflict');assert.equal(reply.analysis.checks.find((c:any)=>c.criterion_id==='private-bath').status,'reported_conflict');assert.equal((await read(f.a.id)).status,'MANUAL_REPLIED');
     const state=await store.state(f.session);assert.equal(state.requirements.criteria[0].value,3000);assert(state.messages.some(m=>m.text.includes('与发送时硬条件冲突')));
     await manual.recover();assert.equal(Number((await pool.query("SELECT count(*) FROM followup_events WHERE action_id=$1 AND kind='reply_analysis'",[f.a.id])).rows[0].count),1);checks.push('restart recovery; immutable sent budget 2000 despite current budget 3000; rent2200 and no private bath both conflict; no automatic booking or property-fact mutation');
   });
   await t.test('subsequent draft is editable and idempotent; manual booking needs both explicit confirmations',async()=>{
     const draft=await manual.nextDraft(f.session,f.a.id,{reply_id:replyId}),again=await manual.nextDraft(f.session,f.a.id,{reply_id:replyId});assert.equal(again.id,draft.id);assert.equal(draft.status,'DRAFT');assert.equal(draft.manual,undefined);assert((draft.body).toLowerCase().includes("differs"));assert.equal(draft.content_hash,hash({source_request_id:draft.source_request_id,requirements_version:draft.requirements_version,listing_ids:draft.listing_ids,action_type:draft.action_type,subject:draft.subject,body:draft.body,recipient:draft.recipient,slots:draft.slots,next_followup_at:draft.next_followup_at,verification_context:draft.verification_context}));
     await assert.rejects(actions.change(f.session,f.a.id,'confirm',{revision:1,client_confirmed:true,salesperson_confirmed:false,evidence_note:'only one party'},f.owner));await actions.change(f.session,f.a.id,'confirm',{revision:1,client_confirmed:true,salesperson_confirmed:true,evidence_note:'人工记录：双方另行明确同意原定时段（验收样例）'},f.owner);assert.equal((await read(f.a.id)).status,'CONFIRMED');
     const calendar=await followup.calendar(f.session,singaporeDate(new Date(f.a.slots!.starts_at)).slice(0,7));assert.equal(calendar.items.find(x=>x.id===f.a.id)?.mode,'manual');checks.push('next inquiry draft is idempotent, editable and unsent; explicit bilateral viewing confirmation recorded as manual');
   });
   await t.test('new revision invalidates copied text; pending reply survives model failure and includes raw source in the day summary',async()=>{
     const x=await fixture(),p=await manual.prepare(x.session,x.a.id,{revision:1});await actions.change(x.session,x.a.id,'edit',{revision:1,subject:x.a.subject,body:'已修改的草稿正文',recipient:x.a.recipient,slots:null,next_followup_at:null},x.owner);await assert.rejects(manual.sent(x.session,x.a.id,{revision:1,content_hash:p.content_hash,confirmed_sent:true}),/草稿已修改/);
     const id=randomUUID();await manual.receive(f.session,f.a.id,{reply_id:id,text:'<script>alert("x")</script> 月租2000，有独立卫浴。',confirmed_received:true});const broken={withStructuredOutput(){throw Error('unavailable');}};await new ManualFollowupService(store,broken as any).analyze(f.session,id);assert.equal((await pool.query('SELECT analysis FROM manual_followup_replies WHERE id=$1',[id])).rows[0].analysis.model_status,'failed');assert.equal((await read(f.a.id)).status,'CONFIRMED');
     const doc=await followup.daily(f.session,singaporeDate()),artifact=(await pool.query('SELECT content FROM workflow_artifacts WHERE id=$1',[doc.id])).rows[0].content;assert(artifact.markdown.includes('manual_sent'));assert(artifact.markdown.includes('manual_reply'));assert(artifact.markdown.includes('reply_analysis'));assert(!artifact.html.includes('<script>'));assert(artifact.html.includes('&lt;script&gt;'));checks.push('copied revision guard; raw reply survives model failure; confirmed state monotonic; day document contains sent snapshot, raw replies and analysis with escaped HTML');
   });
   await t.test('an already sent old copy can be recorded after a new search, preserving original terms; new stale copies and follow-on drafts are blocked',async()=>{
     const x=await fixture(),p=await manual.prepare(x.session,x.a.id,{revision:1});await store.transaction(async tx=>{const s=await store.lock(tx,x.session);s.requirements_version=2;s.requirements.criteria[0].value=3000;s.active_operation_id=randomUUID();await store.saveState(tx,s);});
     await assert.rejects(manual.prepare(x.session,x.a.id,{revision:1}),/推荐已变化/);
     await manual.sent(x.session,x.a.id,{revision:1,content_hash:p.content_hash,confirmed_sent:true});assert.equal((await read(x.a.id)).manual!.requirements_snapshot.criteria[0].value,2000);
     const id=randomUUID();await manual.receive(x.session,x.a.id,{reply_id:id,text:'月租2200，没有独立卫浴。',confirmed_received:true});await manual.analyze(x.session,id);await assert.rejects(manual.nextDraft(x.session,x.a.id,{reply_id:id}),/原回复基于旧需求/);checks.push('manual history remains recordable after a new search; sent requirement snapshot stays immutable; stale new copies/drafts rejected');
   });
   const browser=await fixture();await writeFile('data/.runtime/manual-ui-fixture.json',JSON.stringify({session_id:browser.session,owner:browser.owner,action_id:browser.a.id}));
   await writeFile('evaluation/reports/manual-followup-business.json',JSON.stringify({checked_at:new Date().toISOString(),status:'passed',database:'real PostgreSQL',messages_sent_externally:0,checks},null,2));
 }finally{await pool.end();}
});
