import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import pg from 'pg';
import {DomainStore} from '../src/stores/domain.js';
import {BehaviorService,prepareBrowserFeedback} from '../src/domain/behavior.js';

test('browser ledger: owner proof, cumulative retries, two-tab exclusion, history, watermark and no unsolicited recommendation',{skip:process.env.BROWSER_INTEREST_LIVE!=='1'},async()=>{
 const pool=new pg.Pool({connectionString:process.env.DATABASE_URL});const store=new DomainStore(pool,{} as any,{} as any);await store.setup();const service=new BehaviorService(store),owner=randomUUID()+randomUUID(),sid=await store.create(owner),source=randomUUID(),operation=randomUUID(),a='910001',b='910002',u='910003';
 try{
  const response={recommendations:[a,b].map(id=>({listing:{listing_id:id}})),unverified_candidates:[{listing:{listing_id:u}}],native:{ranking:[a,b]}};
  await pool.query('INSERT INTO recommendation_calls(id,session_id,operation_id,request,response) VALUES($1,$2,$3,$4,$5)',[source,sid,operation,JSON.stringify({requirements_version:1}),JSON.stringify(response)]);
  const before=await store.state(sid),tab=randomUUID();
  const start=(id:string,tid=tab)=>({event_id:randomUUID(),session_id:sid,source_request_id:source,listing_id:id,tab_id:tid,requirements_version:1,kind:'begin',view_session_id:randomUUID(),segment_id:randomUUID(),sequence:0,active_duration_ms:0});
  const x=start(a);assert.equal((await service.record(owner,x)).granted,true);
  assert.equal((await service.record(owner,start(b,randomUUID()))).granted,false);
  await assert.rejects(service.record('wrong-owner',x),/会话/);
  await assert.rejects(service.record(owner,{...x,event_id:randomUUID(),segment_id:randomUUID(),listing_id:'999999'}),/关联/);
  await pool.query("UPDATE listing_view_segments SET started_at=clock_timestamp()-interval '80 seconds' WHERE segment_id=$1",[x.segment_id]);
  const heartbeat={...x,event_id:randomUUID(),kind:'heartbeat',sequence:1,active_duration_ms:16000};await service.record(owner,heartbeat);await service.record(owner,heartbeat);
  await assert.rejects(service.record(owner,{...heartbeat,active_duration_ms:17000}),/同一浏览事件/);
  await assert.rejects(service.record(owner,{...heartbeat,event_id:randomUUID(),sequence:2,active_duration_ms:500000}),/实际经过时间/);
  const complete={...x,event_id:randomUUID(),kind:'complete',sequence:2,active_duration_ms:32000,end_reason:'return'};await service.record(owner,complete);await service.record(owner,complete);
  assert.equal((await service.summary(sid)).active_duration_ms,32000);
  const cutoff=(await pool.query('SELECT clock_timestamp() AS t')).rows[0].t;
  const y=start(b);assert.equal((await service.record(owner,y)).granted,true);await pool.query("UPDATE listing_view_segments SET started_at=clock_timestamp()-interval '80 seconds' WHERE segment_id=$1",[y.segment_id]);await service.record(owner,{...y,event_id:randomUUID(),kind:'complete',sequence:1,active_duration_ms:32000,end_reason:'return'});
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM listing_feedback WHERE session_id=$1',[sid])).rows[0].n,0);
  assert.deepEqual(await store.state(sid),before);
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM agent_tasks WHERE session_id=$1',[sid])).rows[0].n,0);
  const early=await store.transaction(tx=>prepareBrowserFeedback(tx,before,cutoff));assert.equal(early.ready,false);
  const late=(await pool.query('SELECT clock_timestamp() AS t')).rows[0].t;
  const ready=await store.transaction(tx=>prepareBrowserFeedback(tx,before,late));assert.equal(ready.ready,true);await store.transaction(tx=>prepareBrowserFeedback(tx,before,late));
  const fb=(await pool.query('SELECT body FROM listing_feedback WHERE session_id=$1',[sid])).rows.map(r=>r.body);assert.equal(fb.length,2);assert(fb.every(f=>f.payload.source==='web_detail'&&f.payload.active_duration_ms===32000));
  // A current request changing does not invalidate legitimate delayed historic source proof.
  await pool.query('UPDATE session_domain_state SET latest_request_id=$2 WHERE session_id=$1',[sid,randomUUID()]);const pending=start(u);assert.equal((await service.record(owner,pending)).granted,true);
  await pool.query("UPDATE listing_view_segments SET started_at=clock_timestamp()-interval '80 seconds' WHERE segment_id=$1",[pending.segment_id]);await service.record(owner,{...pending,event_id:randomUUID(),kind:'complete',sequence:1,active_duration_ms:32000});
  const all=(await pool.query('SELECT clock_timestamp() AS t')).rows[0].t;await store.transaction(tx=>prepareBrowserFeedback(tx,before,all));assert.equal((await pool.query('SELECT count(*)::int AS n FROM listing_feedback WHERE session_id=$1',[sid])).rows[0].n,2);
  const abandoned=start(a);assert.equal((await service.record(owner,abandoned)).granted,true);
  await pool.query("UPDATE listing_view_segments SET active_duration_ms=16000,started_at=clock_timestamp()-interval '80 seconds',updated_at=clock_timestamp()-interval '60 seconds' WHERE segment_id=$1",[abandoned.segment_id]);
  await pool.query("UPDATE browser_reading_leases SET expires_at=clock_timestamp()-interval '30 seconds' WHERE segment_id=$1",[abandoned.segment_id]);
  const recoveredAt=(await pool.query('SELECT clock_timestamp() AS t')).rows[0].t;await store.transaction(tx=>prepareBrowserFeedback(tx,before,recoveredAt));
  const recovered=(await pool.query('SELECT body FROM listing_feedback WHERE session_id=$1 AND event_id=$2',[sid,`web:${abandoned.segment_id}`])).rows[0].body;assert.equal(recovered.payload.active_duration_ms,16000);
 }finally{
  await pool.query('DELETE FROM browser_reading_leases WHERE owner_hash=(SELECT owner_hash FROM session_domain_state WHERE session_id=$1)',[sid]);
  for(const table of ['browser_behavior_receipts','listing_view_segments','listing_exposures','listing_feedback','recommendation_calls'])await pool.query(`DELETE FROM ${table} WHERE session_id=$1`,[sid]);await pool.query('DELETE FROM session_domain_state WHERE session_id=$1',[sid]);await pool.end();
 }
});
