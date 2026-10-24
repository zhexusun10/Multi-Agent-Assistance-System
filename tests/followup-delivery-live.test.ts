import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID,createHmac} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import pg from 'pg';
import {DomainStore,hash} from '../src/stores/domain.js';
import {PostgresAgentTaskStore} from '../src/agent-task-store.js';
import {PostgresSessionEventStore} from '../src/session-store.js';
import {ActionService,wireBody,type Action} from '../src/domain/actions.js';
import {FollowupService,singaporeDate} from '../src/domain/followup.js';
import {formatViewingTime} from '../src/shared/followup-message.js';
import {MessagingGateway} from '../src/integrations/messaging.js';

test('durable messaging, webhook, reminder and daily-summary business acceptance (fake providers, real PostgreSQL)',{skip:process.env.BUSINESS_LIVE!=='1',timeout:90000},async t=>{
 const pool=new pg.Pool({connectionString:process.env.DATABASE_URL,max:10}),events=new PostgresSessionEventStore(pool),tasks=new PostgresAgentTaskStore(pool),store=new DomainStore(pool,tasks,events);await store.setup();
 const env={EXTERNAL_ACTION_MODE:'live',TELEGRAM_BOT_TOKEN:'fake-test-only',TELEGRAM_WEBHOOK_SECRET:'test-secret',WHATSAPP_APP_SECRET:'wa-secret',WHATSAPP_VERIFY_TOKEN:'verify-token',WHATSAPP_PHONE_NUMBER_ID:'1234'};
 let attempts=0,fail=false,messageId=Date.now();const payloads:any[]=[];
 const gateway=new MessagingGateway(env,(async(_url,options)=>{attempts++;payloads.push(JSON.parse(String(options?.body)));if(fail)throw Error('timeout after possible send');return Response.json({ok:true,result:{message_id:++messageId}});}) as typeof fetch);
 const followup=new FollowupService(store,gateway),actions=new ActionService(store,followup),previous=process.env.EXTERNAL_ACTION_MODE;process.env.EXTERNAL_ACTION_MODE='live';const checks:string[]=[];
 const state=(sid:string)=>store.state(sid),read=async(id:string)=>(await pool.query('SELECT body FROM workflow_actions WHERE id=$1',[id])).rows[0].body as Action;
 const fixture=async(type:'MESSAGE'|'VIEWING'='MESSAGE')=>{
   const owner=randomUUID()+randomUUID(),sid=await store.create(owner),rec=JSON.parse(await readFile('tests/fixtures/recommendation-response.json','utf8')),request=randomUUID(),op=randomUUID();
   rec.request_id=request;rec.conversation_id=sid;rec.requirements_version=1;
   await store.transaction(async tx=>{const s=await store.lock(tx,sid);s.recommendation=rec;s.latest_request_id=request;s.requirements_version=1;s.active_operation_id=op;store.message(s,'user','预算2000，要求独立卫浴，想协商1800。');await store.saveState(tx,s);await tx.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind) VALUES($1,$2,$3,'test-only','search')",[op,sid,randomUUID()]);await tx.query('INSERT INTO recommendation_calls(id,session_id,operation_id,request,response) VALUES($1,$2,$3,$4,$5)',[request,sid,op,'{}',JSON.stringify(rec)]);});
   const a=actions.draft(await state(sid),[rec.recommendations[0].listing.listing_id],type);await pool.query('INSERT INTO workflow_actions(id,session_id,body) VALUES($1,$2,$3)',[a.id,sid,JSON.stringify(a)]);
   const address=String(1000000000+Math.floor(Math.random()*1000000000)),slots=type==='VIEWING'?{starts_at:new Date(Date.now()+86400000).toISOString(),ends_at:new Date(Date.now()+90000000).toISOString(),time_zone:'Asia/Singapore' as const}:null;
   const edited=await actions.change(sid,a.id,'edit',{revision:1,subject:a.subject,body:a.body,recipient:{name:'Synthetic salesperson',channel:'telegram',address,verified:true,opt_in:true},slots,negotiation_notes:'希望协商至1800',next_followup_at:null},owner);
   return {sid,owner,a:edited,address};
 };
 try{
  let f:Awaited<ReturnType<typeof fixture>>;
  await t.test('approval and idempotent enqueue precede one provider attempt; exact notes and time reach payload',async()=>{
   f=await fixture('VIEWING');await actions.change(f.sid,f.a.id,'approve',{revision:2},f.owner);await Promise.all([actions.change(f.sid,f.a.id,'execute',{revision:2},f.owner),actions.change(f.sid,f.a.id,'execute',{revision:2},f.owner)]);assert.equal((await read(f.a.id)).status,'QUEUED');assert.equal(attempts,0);
   await Promise.all([followup.processOutbox(),followup.processOutbox()]);const sent=await read(f.a.id);assert.equal(sent.status,'ACCEPTED');assert.equal(attempts,1);assert.equal(payloads[0].text,wireBody(f.a));assert(payloads[0].text.includes('1800'));assert(payloads[0].text.includes(formatViewingTime(f.a.slots!)));assert(!payloads[0].text.includes(f.a.slots!.starts_at));await actions.change(f.sid,f.a.id,'execute',{revision:2},f.owner);assert.equal(attempts,1);checks.push('approval, durable enqueue, exact wire payload and idempotent single submission');
  });
  await t.test('unauthenticated webhook rejected; authenticated duplicate reply creates one event and completes only awaiting-reply task',async()=>{
   const due=new Date(Date.now()+10000).toISOString();await followup.task(f.sid,{action_id:f.a.id,kind:'await_reply',due_at:due,note:'等回复'});await followup.task(f.sid,{action_id:f.a.id,kind:'viewing_reminder',due_at:due,note:'出发看房'});
   const raw=Buffer.from(JSON.stringify({update_id:Math.floor(Math.random()*1000000000),message:{message_id:912,chat:{id:Number(f.address)},text:'可以周末看房，但独立卫浴还需要核实。',reply_to_message:{message_id:messageId}}}));
   await assert.rejects(followup.webhook('telegram',raw,'wrong'),/secret/);assert.equal((await read(f.a.id)).status,'ACCEPTED');await followup.webhook('telegram',raw,'test-secret');await followup.webhook('telegram',raw,'test-secret');assert.equal((await read(f.a.id)).status,'REPLIED');
   const rows=(await pool.query('SELECT kind,status FROM followup_tasks WHERE action_id=$1',[f.a.id])).rows;assert.equal(rows.find(r=>r.kind==='await_reply').status,'DONE');assert.equal(rows.find(r=>r.kind==='viewing_reminder').status,'OPEN');assert.equal(Number((await pool.query("SELECT count(*) FROM followup_events WHERE action_id=$1 AND kind='reply'",[f.a.id])).rows[0].count),1);checks.push('verified webhook, one reply event, selective follow-up completion without property-fact changes');
  });
  await t.test('viewing confirmation is explicit, remains separate from replies and appears in owner-scoped calendar',async()=>{
   await assert.rejects(actions.change(f.sid,f.a.id,'confirm',{revision:2,client_confirmed:true,salesperson_confirmed:false,evidence_note:'not both parties'},f.owner));await actions.change(f.sid,f.a.id,'confirm',{revision:2,client_confirmed:true,salesperson_confirmed:true,evidence_note:'测试双方明确同意此候选时间，有独立依据。'},f.owner);
   const c=await followup.calendar(f.sid,singaporeDate(new Date(f.a.slots!.starts_at)).slice(0,7));assert.equal(c.items.find(a=>a.id===f.a.id)?.confirmed,true);assert.equal(c.today,singaporeDate());await assert.rejects(store.assertOwner(f.sid,randomUUID()),/无法访问/);checks.push('bilateral viewing confirmation, current Singapore date and owned calendar');
  });
  await t.test('timeout and interrupted sends become UNKNOWN and are never automatically resubmitted',async()=>{
   const x=await fixture();fail=true;await actions.change(x.sid,x.a.id,'approve',{revision:2},x.owner);await actions.change(x.sid,x.a.id,'execute',{revision:2},x.owner);const before=attempts;await followup.processOutbox();assert.equal((await read(x.a.id)).status,'UNKNOWN');await followup.processOutbox();await actions.change(x.sid,x.a.id,'execute',{revision:2},x.owner);assert.equal(attempts,before+1);fail=false;
   const y=await fixture();await actions.change(y.sid,y.a.id,'approve',{revision:2},y.owner);await actions.change(y.sid,y.a.id,'execute',{revision:2},y.owner);await pool.query("UPDATE message_outbox SET state='SENDING',started_at=now()-interval '2 minutes' WHERE action_id=$1",[y.a.id]);const count=attempts;await followup.processOutbox();assert.equal((await read(y.a.id)).status,'UNKNOWN');assert.equal(attempts,count);checks.push('ambiguous timeouts and interrupted sends require reconciliation, with no automatic retry');
  });
  await t.test('cancelled or superseded queued actions do not reach provider',async()=>{
   const before=attempts;for(const mode of ['cancel','stale']){const x=await fixture();await actions.change(x.sid,x.a.id,'approve',{revision:2},x.owner);await actions.change(x.sid,x.a.id,'execute',{revision:2},x.owner);if(mode==='cancel')await actions.change(x.sid,x.a.id,'cancel',{revision:2},x.owner);else await pool.query('UPDATE session_domain_state SET active_operation_id=$2 WHERE session_id=$1',[x.sid,randomUUID()]);await followup.processOutbox();assert.equal((await read(x.a.id)).status,'CANCELLED');}assert.equal(attempts,before);checks.push('cancellation and current-search validation before provider submission');
  });
  await t.test('reminders can be changed after sending and produce exactly one durable due event',async()=>{
   const task=await followup.task(f.sid,{action_id:f.a.id,due_at:new Date(Date.now()+1000).toISOString(),note:'独立提醒，执行后仍可编辑',kind:'general'});await pool.query("UPDATE followup_tasks SET due_at=now()-interval '1 second' WHERE id=$1",[task.id]);await Promise.all([followup.reminders(),followup.reminders()]);assert.equal(Number((await pool.query('SELECT count(*) FROM followup_events WHERE event_key=$1',[`task-reminder:${task.id}:1`])).rows[0].count),1);await followup.task(f.sid,{action_id:f.a.id,id:task.id,revision:1,kind:'general',due_at:new Date(Date.now()+86400000).toISOString(),note:'改到明天'});const row=(await pool.query('SELECT * FROM followup_tasks WHERE id=$1',[task.id])).rows[0];assert.equal(row.revision,2);assert.equal(row.status,'OPEN');checks.push('editable post-send reminders and durable reminder deduplication');
  });
  await t.test('WhatsApp HMAC, challenge, receipt dedup and out-of-order delivery cannot regress read/replied status',async()=>{
   assert.equal(followup.verifyChallenge(new URLSearchParams({'hub.mode':'subscribe','hub.verify_token':'verify-token','hub.challenge':'challenge123'})),'challenge123');assert.throws(()=>followup.verifyChallenge(new URLSearchParams()),/验证失败/);
   const x=await fixture();await actions.change(x.sid,x.a.id,'approve',{revision:2},x.owner);await actions.change(x.sid,x.a.id,'execute',{revision:2},x.owner);await followup.processOutbox();const sent=await read(x.a.id);await pool.query("UPDATE message_outbox SET channel='whatsapp' WHERE action_id=$1",[x.a.id]);
   const deliver=async(status:string)=>{const raw=Buffer.from(JSON.stringify({object:'whatsapp_business_account',entry:[{changes:[{value:{metadata:{phone_number_id:'1234'},statuses:[{id:sent.execution!.external_id,status,timestamp:'1'}]}}]}]}));const sig='sha256='+createHmac('sha256','wa-secret').update(raw).digest('hex');await assert.rejects(followup.webhook('whatsapp',raw,'sha256=bad'),/签名/);await followup.webhook('whatsapp',raw,sig);};
   await deliver('read');await deliver('delivered');await deliver('read');assert.equal((await read(x.a.id)).status,'READ');assert.equal(Number((await pool.query("SELECT count(*) FROM followup_events WHERE action_id=$1 AND kind='delivery'",[x.a.id])).rows[0].count),2);checks.push('WhatsApp signed callbacks, idempotency and monotonic delivery status');
  });
  await t.test('daily artifact includes conversation, negotiation, reply and tasks without HTML injection',async()=>{
   await store.transaction(async tx=>{const s=await store.lock(tx,f.sid);store.message(s,'user','<script>alert("x")</script>');await store.saveState(tx,s);});const doc=await followup.daily(f.sid,singaporeDate()),row=(await pool.query('SELECT * FROM workflow_artifacts WHERE id=$1',[doc.id])).rows[0];assert.equal(row.artifact_type,'daily');assert(row.content.markdown.includes('1800'));assert(row.content.markdown.includes('独立卫浴还需要核实'));assert(row.content.markdown.includes('CONFIRMED'));assert(!row.content.html.includes('<script>'));assert(row.content.html.includes('&lt;script&gt;'));assert.equal(hash(row.content),row.sha256);checks.push('daily conversation and follow-up artifact with faithful unknowns and escaped HTML');
  });
  await t.test('ambiguous contact-only reply is not leaked into either owner; explicit reply id selects one action',async()=>{
   const first=await fixture(),second=await fixture();second.a=await actions.change(second.sid,second.a.id,'edit',{revision:2,subject:second.a.subject,body:second.a.body,recipient:{...second.a.recipient,address:first.address},slots:null,next_followup_at:null},second.owner);
   for(const x of [first,second]){await actions.change(x.sid,x.a.id,'approve',{revision:x.a.revision},x.owner);await actions.change(x.sid,x.a.id,'execute',{revision:x.a.revision},x.owner);}await followup.processOutbox();
   const id=Math.floor(Math.random()*1000000000),plain=Buffer.from(JSON.stringify({update_id:id,message:{message_id:100,chat:{id:Number(first.address)},text:'这条回复没有明确房源'}}));await followup.webhook('telegram',plain,'test-secret');const inbound=(await pool.query('SELECT session_id FROM messaging_inbound WHERE event_key=$1',['tg:'+id])).rows[0];assert.equal(inbound.session_id,null);assert.equal((await read(first.a.id)).status,'ACCEPTED');assert.equal((await read(second.a.id)).status,'ACCEPTED');
   const reply=Buffer.from(JSON.stringify({update_id:id+1,message:{message_id:101,chat:{id:Number(first.address)},text:'明确回复第一条',reply_to_message:{message_id:Number((await read(first.a.id)).execution!.external_id)}}}));await followup.webhook('telegram',reply,'test-secret');assert.equal((await read(first.a.id)).status,'REPLIED');assert.equal((await read(second.a.id)).status,'ACCEPTED');checks.push('ambiguous replies remain unassigned; explicit reply links one owner only');
  });
  await t.test('old WhatsApp timestamps cannot reopen window; a receipt arriving before send response is preserved',async()=>{
   const x=await fixture(),waEnv={...env,WHATSAPP_ACCESS_TOKEN:'fake-wa',WHATSAPP_GRAPH_API_VERSION:'v26.0'};let waAttempts=0;const providerId='wamid.test.'+randomUUID();let wa:FollowupService;
   const hook=async(value:unknown)=>{const raw=Buffer.from(JSON.stringify({object:'whatsapp_business_account',entry:[{changes:[{value:{metadata:{phone_number_id:'1234'},...(value as any)}}]}]}));await wa.webhook('whatsapp',raw,'sha256='+createHmac('sha256','wa-secret').update(raw).digest('hex'));};
   wa=new FollowupService(store,new MessagingGateway(waEnv,(async()=>{waAttempts++;await hook({statuses:[{id:providerId,status:'read',timestamp:String(Math.floor(Date.now()/1000))}]});return Response.json({messages:[{id:providerId}]});}) as typeof fetch));const waActions=new ActionService(store,wa);
   const address='65'+String(80000000+Math.floor(Math.random()*10000000));const edited=await waActions.change(x.sid,x.a.id,'edit',{revision:2,subject:x.a.subject,body:x.a.body,recipient:{name:'Synthetic WA',channel:'whatsapp',address,verified:true,opt_in:true},slots:null,next_followup_at:null},x.owner);await waActions.change(x.sid,x.a.id,'approve',{revision:edited.revision},x.owner);
   await hook({messages:[{id:randomUUID(),from:address,type:'text',timestamp:String(Math.floor(Date.now()/1000)-25*3600),text:{body:'延迟的旧来信'}}]});await assert.rejects(waActions.change(x.sid,x.a.id,'execute',{revision:edited.revision},x.owner),/24 hours/i);assert.equal(waAttempts,0);
   await hook({messages:[{id:randomUUID(),from:address,type:'text',timestamp:String(Math.floor(Date.now()/1000)),text:{body:'当前新来信'}}]});await waActions.change(x.sid,x.a.id,'execute',{revision:edited.revision},x.owner);await wa.processOutbox();assert.equal(waAttempts,1);assert.equal((await read(x.a.id)).status,'READ');checks.push('provider timestamp enforces WhatsApp service window; early read receipt is recovered after message acknowledgement');
  });
  await writeFile('evaluation/reports/followup-delivery-business.json',JSON.stringify({checked_at:new Date().toISOString(),status:'passed',provider_mode:'injected fake HTTP; no actual external messages',checks},null,2));
 }finally{if(previous===undefined)delete process.env.EXTERNAL_ACTION_MODE;else process.env.EXTERNAL_ACTION_MODE=previous;await pool.end();}
});
