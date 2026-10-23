// Migration 004 + the sidecar SQL against a real PostgreSQL (TEST_DATABASE_URL), in a private
// schema that is dropped afterwards. Skipped without TEST_DATABASE_URL, like postgres-session.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import pg from 'pg';
import {DomainStore,type DomainState} from '../src/stores/domain.js';
import {loadFeedbackSidecar,coldStartAfterAdoption} from '../src/domain/feedback-sidecar.js';

test('migration 004 (feedback_snapshot, recommendation_cold_start, indexes) and loadFeedbackSidecar on PostgreSQL',{skip:!process.env.TEST_DATABASE_URL},async()=>{
 const schema='p2_sidecar_'+randomUUID().replace(/-/g,'').slice(0,12);
 const admin=new pg.Pool({connectionString:process.env.TEST_DATABASE_URL});
 await admin.query(`CREATE SCHEMA ${schema}`);
 const pool=new pg.Pool({connectionString:process.env.TEST_DATABASE_URL,options:`-c search_path=${schema}`});
 try{
  for(const name of ['001-business.sql','002-followup.sql','003-manual-followup.sql','004-feedback-snapshot.sql','004-feedback-snapshot.sql'])await pool.query(await readFile('migrations/'+name,'utf8'));// 004 twice: idempotent
  const columns=(await pool.query("SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema=$1 AND column_name IN ('feedback_snapshot','recommendation_cold_start') ORDER BY 1",[schema])).rows;
  assert.deepEqual(columns,[{table_name:'recommendation_calls',column_name:'feedback_snapshot',data_type:'jsonb'},{table_name:'session_domain_state',column_name:'recommendation_cold_start',data_type:'jsonb'}]);
  const indexes=(await pool.query('SELECT indexname FROM pg_indexes WHERE schemaname=$1 ORDER BY 1',[schema])).rows.map(r=>r.indexname);
  for(const name of ['listing_feedback_round','recommendation_calls_session','workflow_actions_session'])assert(indexes.includes(name),name);
  // Every sidecar case (native adopted, hardGap, baseline, legacy, empty, no previous round with a
  // stored cold-start exit) over real rows: the SQL path equals the fixture expectation.
  const {cases}=JSON.parse(await readFile('tests/fixtures/recommendation-v2/sidecar-cases.json','utf8'));assert(cases.length>=6);
  const store=new DomainStore(pool,null as any,null as any);
  let session='';
  for(const c of cases){
   session=randomUUID();await pool.query('INSERT INTO session_domain_state(session_id,owner_hash) VALUES($1,$2)',[session,'h']);
   for(const [i,d] of c.input.durations.entries())await pool.query('INSERT INTO listing_feedback(session_id,event_id,body) VALUES($1,$2,$3)',[session,`e${i}`,JSON.stringify({event_id:`e${i}`,listing_id:d.listing_id,event_type:'active_view',occurred_at:'2026-10-23T00:00:00Z',payload:{source:'sample',recommendation_request_id:d.recommendation_request_id,view_session_id:`v${i}`,active_duration_ms:d.active_duration_ms}})]);
   await pool.query('INSERT INTO listing_feedback(session_id,event_id,body) VALUES($1,$2,$3)',[session,'save',JSON.stringify({event_id:'save',listing_id:'fixture-01',event_type:'save',occurred_at:'2026-10-23T00:00:00Z',payload:{}})]);
   for(const a of c.input.actions)await pool.query('INSERT INTO workflow_actions(id,session_id,body) VALUES($1,$2,$3)',[randomUUID(),session,JSON.stringify({action_type:a.action_type,status:a.status,listing_ids:a.listing_ids,id_alias:a.action_id})]);
   if(c.input.cold_start)await store.transaction(async tx=>{const s=await store.lock(tx,session) as DomainState;s.messages=[];s.recommendation_cold_start=c.input.cold_start;await store.saveState(tx,s);});
   const sidecar=await store.transaction(async tx=>{const s=await store.lock(tx,session) as DomainState;s.recommendation=c.input.recommendation;return loadFeedbackSidecar(tx,s);});
   // Action IDs are the DB UUIDs here; compare everything else with the fixture expectation.
   assert.deepEqual(sidecar.previous_round,c.expected.previous_round,c.name);
   assert.deepEqual(sidecar.appointments.map(a=>({listing_id:a.listing_id,statuses:a.statuses,n:a.action_ids.length,sorted:[...a.action_ids].sort().join()===a.action_ids.join()})),c.expected.appointments.map((a:any)=>({listing_id:a.listing_id,statuses:a.statuses,n:a.action_ids.length,sorted:true})),c.name);
   assert.deepEqual(sidecar.cold_start,c.expected.cold_start,c.name);
  }
  session=randomUUID();await pool.query('INSERT INTO session_domain_state(session_id,owner_hash) VALUES($1,$2)',[session,'h']);
  // saveState persists the cold-start column; a fresh lock reads it back.
  await store.transaction(async tx=>{const s=await store.lock(tx,session) as DomainState;s.messages=[];s.recommendation_cold_start=coldStartAfterAdoption(s.recommendation_cold_start,{native:{contract:'2.0',pool:{pool_key:'pk-1'},selection:{cold_start:{evaluated:true,exit_conditions_met:true,pool_key:'pk-1'}}}} as any);await store.saveState(tx,s);});
  const saved=await store.transaction(async tx=>(await store.lock(tx,session)).recommendation_cold_start);
  assert.deepEqual(saved,{exited:true,exited_pool_key:'pk-1'});
  const again=await store.transaction(async tx=>loadFeedbackSidecar(tx,await store.lock(tx,session)));
  assert.deepEqual(again.cold_start,{exited:true,exited_pool_key:'pk-1'});assert.equal(again.previous_round,null);// no adopted recommendation stored
 }finally{await pool.end();await admin.query(`DROP SCHEMA ${schema} CASCADE`);await admin.end();}
});
