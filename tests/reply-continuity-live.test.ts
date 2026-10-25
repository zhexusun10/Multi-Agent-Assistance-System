import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {parseEnv} from 'node:util';
import {readFile,writeFile} from 'node:fs/promises';
import pg from 'pg';
import {DomainStore} from '../src/stores/domain.js';
import {PostgresAgentTaskStore} from '../src/agent-task-store.js';
import {PostgresSessionEventStore} from '../src/session-store.js';
import {ActionService,type Action} from '../src/domain/actions.js';
import {ManualFollowupService} from '../src/domain/manual-followup.js';
import {analyzeReply} from '../src/domain/reply-analysis.js';
import {createModel} from '../src/model.js';

const text='你好，房子最短租6个月，房间配备床、椅子和书桌，被褥还需要自行准备。水电费是包含在房租里的。房间配备独立卫浴。';
const noRepeated=(body:string)=>{assert(!/最短可以租多久|带哪些家具|有独立卫浴吗|水电费包含在房租里吗/.test(body),body);};
test('reply memory acceptance: real PostgreSQL, exact user text, multi-round continuity and isolation',{skip:process.env.BUSINESS_LIVE!=='1',timeout:100000},async t=>{
 const pool=new pg.Pool({connectionString:process.env.DATABASE_URL}),store=new DomainStore(pool,new PostgresAgentTaskStore(pool),new PostgresSessionEventStore(pool));await store.setup();
 const actions=new ActionService(store),manual=new ManualFollowupService(store),checks:string[]=[];
 const original=JSON.parse(await readFile('data/.runtime/listing-presentation-ui-fixture.json','utf8')),base=await store.state(original.session_id);
 const fixture=async()=>{
   const owner=randomUUID()+randomUUID(),session_id=await store.create(owner),request=randomUUID(),op=randomUUID(),rec=structuredClone(base.recommendation!);
   rec.request_id=request;rec.conversation_id=session_id;rec.requirements_version=1;
   const candidates=[...rec.recommendations,...rec.unverified_candidates].slice(0,2);rec.recommendations=[];rec.unverified_candidates=candidates;rec.status='no_match';
   const requirements=structuredClone(base.requirements);requirements.criteria.push({criterion_id:'private-bath-reply-test',field:'amenities',operator:'eq',value:'private_bathroom',strength:'hard'});
   candidates.forEach((c,i)=>{c.rank=i+1;c.criterion_results.push({criterion_id:'private-bath-reply-test',status:'unknown',observed_value:null,evidence_ids:[]});});
   rec.state.counts.eligible=0;rec.state.counts.unverified=candidates.length;
   await store.transaction(async tx=>{const s=await store.lock(tx,session_id);Object.assign(s,{recommendation:rec,latest_request_id:request,requirements_version:1,active_operation_id:op,requirements});await store.saveState(tx,s);await tx.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind) VALUES($1,$2,$3,'reply-continuity-isolated-test','search')",[op,session_id,randomUUID()]);await tx.query('INSERT INTO recommendation_calls(id,session_id,operation_id,request,response) VALUES($1,$2,$3,$4,$5)',[request,session_id,op,'{}',JSON.stringify(rec)]);});
   const a=actions.draft(await store.state(session_id),[candidates[0].listing.listing_id],'MESSAGE');await pool.query('INSERT INTO workflow_actions(id,session_id,body) VALUES($1,$2,$3)',[a.id,session_id,JSON.stringify(a)]);return {owner,session_id,a,requirements,listing_ids:candidates.map(c=>c.listing.listing_id)};
 };
 const sent=async(sid:string,a:Action)=>{const p=await manual.prepare(sid,a.id,{revision:a.revision});await manual.sent(sid,a.id,{revision:p.revision,content_hash:p.content_hash,confirmed_sent:true});};
 try{
   const f=await fixture();await sent(f.session_id,f.a);const rid=randomUUID();await manual.receive(f.session_id,f.a.id,{reply_id:rid,text,confirmed_received:true});await manual.analyze(f.session_id,rid);
   const analysis=(await pool.query('SELECT analysis FROM manual_followup_replies WHERE id=$1',[rid])).rows[0].analysis;
   assert.equal(analysis.checks.find((c:any)=>c.criterion_id==='private-bath-reply-test').status,'reported_match');assert(analysis.checks.some((c:any)=>c.status==='reference_match'));noRepeated(analysis.suggested_reply);
   const first=await manual.nextDraft(f.session_id,f.a.id,{reply_id:rid}),generic=await manual.draftFromHistory(await store.state(f.session_id),f.a.listing_ids,'MESSAGE');noRepeated(first.body);noRepeated(generic.body);assert.equal((await manual.nextDraft(f.session_id,f.a.id,{reply_id:rid})).id,first.id);
   checks.push('exact reported text resolves five rental topics; both reply-specific and generic recompose skip answered questions; place evidence remains distinct; next draft is idempotent');
   await sent(f.session_id,first);const rid2=randomUUID();await manual.receive(f.session_id,first.id,{reply_id:rid2,text:'现在仍可租。月租1600新币。没有其他费用。',confirmed_received:true});await manual.analyze(f.session_id,rid2);
   const second=(await pool.query('SELECT analysis FROM manual_followup_replies WHERE id=$1',[rid2])).rows[0].analysis;noRepeated(second.suggested_reply);assert(second.claims.some((c:any)=>c.topic==='minimum_lease'&&c.amount===6));assert(second.claims.some((c:any)=>c.topic==='monthly_rent'&&c.amount===1600));assert.equal(second.open_questions.length,0);assert(second.checks.every((c:any)=>['reported_match','reference_match'].includes(c.status)));
   await assert.rejects(manual.nextDraft(f.session_id,f.a.id,{reply_id:rid}),/已有较新的回复/);assert.deepEqual((await store.state(f.session_id)).requirements,f.requirements);assert.equal(Number((await pool.query('SELECT count(*) FROM message_outbox WHERE session_id=$1',[f.session_id])).rows[0].count),0);
   checks.push('second message adds current availability/rent/fees without forgetting first-round lease/furniture/private bath; no answered questions repeated; older replies cannot generate stale drafts; no property/requirement mutation or outbound sends');
   const isolated=await fixture(),clean=await manual.draftFromHistory(await store.state(isolated.session_id),isolated.a.listing_ids,'MESSAGE');assert((clean.body).toLowerCase().includes("minimum lease"));assert((clean.body).toLowerCase().includes("private bathroom"));
   const alt=await manual.draftFromHistory(await store.state(f.session_id),[f.listing_ids[1]],'MESSAGE');assert((alt.body).toLowerCase().includes("private bathroom"));
   checks.push('different session and different listing do not inherit correspondence memory');
   // Persist an old-version analysis to prove regeneration upgrades it before using any suggestion.
   const legacy=await fixture();await sent(legacy.session_id,legacy.a);const legacyId=randomUUID();await manual.receive(legacy.session_id,legacy.a.id,{reply_id:legacyId,text,confirmed_received:true});await pool.query("UPDATE manual_followup_replies SET analysis=$2,analysis_state='READY' WHERE id=$1",[legacyId,JSON.stringify({suggested_reply:legacy.a.body})]);const upgraded=await manual.nextDraft(legacy.session_id,legacy.a.id,{reply_id:legacyId});noRepeated(upgraded.body);assert.equal((await pool.query('SELECT analysis FROM manual_followup_replies WHERE id=$1',[legacyId])).rows[0].analysis.analysis_version,2);
   checks.push('old persisted analysis is upgraded on use; already sent snapshots remain unchanged');
   const cached=await fixture();await sent(cached.session_id,cached.a);const cacheReply=randomUUID();await manual.receive(cached.session_id,cached.a.id,{reply_id:cacheReply,text,confirmed_received:true});await manual.analyze(cached.session_id,cacheReply);
   const insertOld=async()=>{const draft=actions.draft(await store.state(cached.session_id),cached.a.listing_ids,'MESSAGE');await pool.query('INSERT INTO workflow_actions(id,session_id,body) VALUES($1,$2,$3)',[draft.id,cached.session_id,JSON.stringify(draft)]);await pool.query('UPDATE manual_followup_replies SET next_action_id=$2 WHERE id=$1',[cacheReply,draft.id]);return draft;};
   const untouched=await insertOld(),repaired=await manual.nextDraft(cached.session_id,cached.a.id,{reply_id:cacheReply});assert.equal(repaired.id,untouched.id);assert.equal(repaired.revision,2);noRepeated(repaired.body);assert.equal(Number((await pool.query("SELECT count(*) FROM followup_events WHERE action_id=$1 AND event_key LIKE 'manual-next-repair:%'",[repaired.id])).rows[0].count),1);
   const copied=await insertOld(),copy=await manual.prepare(cached.session_id,copied.id,{revision:1});const preserved=await manual.nextDraft(cached.session_id,cached.a.id,{reply_id:cacheReply});assert.equal(preserved.body,copied.body);assert.equal(preserved.revision,1);await manual.sent(cached.session_id,copied.id,{revision:1,content_hash:copy.content_hash,confirmed_sent:true});assert.equal((await pool.query('SELECT body FROM workflow_actions WHERE id=$1',[copied.id])).rows[0].body.manual.message_body,copy.body);
   checks.push('untouched cached drafts repair with a new revision and SSE event; copied snapshots remain recordable without automatic rewriting');
   await t.test('configured real model understands the original text, with evidence and no repeated questions',{skip:process.env.MODEL_LIVE!=='1'},async()=>{
     // Match server.ts: a developer shell may carry a different inherited provider key.
     const project=parseEnv(await readFile('.env','utf8')),provider=(process.env.CHAT_MODEL||'openai:gpt-4.1-mini').split(':')[0],key=({openai:'OPENAI_API_KEY',anthropic:'ANTHROPIC_API_KEY','google-genai':'GOOGLE_API_KEY'} as Record<string,string>)[provider];if(key&&Object.hasOwn(project,key))process.env[key]=project[key];
     const a=await analyzeReply(text,f.requirements,await createModel());assert.equal(a.model_status,'ok');assert.equal(a.checks.find(c=>c.criterion_id==='private-bath-reply-test')?.status,'reported_match');noRepeated(a.suggested_reply);assert(a.claims.every(c=>text.includes(c.quote)));checks.push('configured real model returns grounded claims; exact positive bathroom assertion remains positive');});
   const ui=await fixture();await writeFile('data/.runtime/reply-continuity-ui-fixture.json',JSON.stringify({...ui,action_id:ui.a.id}));
   await writeFile('evaluation/reports/reply-continuity-business.json',JSON.stringify({checked_at:new Date().toISOString(),status:'passed',model_verified:process.env.MODEL_LIVE==='1',messages_sent_externally:0,input:text,summary:analysis.summary,next_draft:first.body,checks},null,2));
 }finally{await pool.end();}
});
