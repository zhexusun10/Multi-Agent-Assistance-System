import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import pg from 'pg';
import {DomainStore} from '../src/stores/domain.js';
import {PostgresAgentTaskStore} from '../src/agent-task-store.js';
import {PostgresSessionEventStore} from '../src/session-store.js';
import {WorkflowService} from '../src/domain/workflow.js';
import {moreFixture,moreResponse} from './helpers/recommendation-more.js';
import {displayedCandidates} from '../src/shared/recommendation-display.js';
import {AgentOrchestrator} from '../src/agents/orchestrator/agent-orchestrator.js';
import {IntentRecognizer} from '../src/agents/orchestrator/intent-recognizer.js';

test('isolated database: initial five, append five, atomic publication, idempotence, shortage, legacy refresh and reset', {skip:process.env.MORE_DB_TEST!=='1'},async()=>{
  const connectionString=process.env.TEST_DATABASE_URL||process.env.DATABASE_URL;
  const admin=new pg.Pool({connectionString}),schema='more_test_'+randomUUID().replaceAll('-','');
  await admin.query(`CREATE SCHEMA "${schema}"`);
  const pool=new pg.Pool({connectionString,options:`-c search_path=${schema}`});
  const tasks=new PostgresAgentTaskStore(pool),events=new PostgresSessionEventStore(pool),store=new DomainStore(pool,tasks,events);
  try{
    await tasks.setup();await events.setup();await store.setup();
    const model={withStructuredOutput:()=>({invoke:async()=>{throw Error('OFFLINE_TEST_MODEL');}})};
    const workflow=new WorkflowService(store,'synthetic',model as any);
    let retrievals=0,resultCount=10;
    workflow.recommender.recommend=async request=>{retrievals++;assert.equal(request.result_limit,10);return moreResponse(request,resultCount);};
    workflow.explanationEnricher.load=async()=>[];
    const intentModel={invoke:async()=>({text:JSON.stringify({intent:'recommendation_more',confidence:1,requested_tasks:[]})})};
    Object.assign(workflow,{orchestrator:new AgentOrchestrator(undefined,new IntentRecognizer(intentModel as any,async()=>({intent:'recommendation_more',confidence:1})))});
    const draft={upsert:[],remove_fields:[],remove_criterion_ids:[],clarifications:[],sources:[],supported:true};
    async function initial(sid?:string){
      const session=sid||await store.create('isolated-more-test-owner'),op=randomUUID();
      await store.transaction(async tx=>{
        const s=await store.lock(tx,session);s.requirements=moreFixture().req;s.active_operation_id=op;
        await tx.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind,status) VALUES($1,$2,$3,'Find a home','search','queued')",[op,session,randomUUID()]);
        await store.queue(tx,s,op,'a',{input_mode:'structured',draft},'intake');await store.saveState(tx,s);
      });
      await run(session,'a');await run(session,'b');await run(session,'c');return session;
    }
    async function run(sid:string,agent:'a'|'b'|'c'){
      const task=await tasks.claimNext(sid,agent);assert(task);
      const output=await workflow.run(agent,task.prompt,sid,task.id);
      await tasks.finish(task.id,output,null);await workflow.apply({...task,status:'completed',output});return task;
    }
    const sid=await initial(),first=await store.state(sid),firstIds=displayedCandidates(first.recommendation).map(c=>c.listing.listing_id);
    assert.equal(firstIds.length,5);assert.equal(retrievals,1);
    const moreId=randomUUID(),planned=await workflow.plan('Show me five more homes',sid,moreId,first.latest_request_id!);
    assert.equal(planned.assignments[0].agent,'c');
    assert.equal((await store.state(sid)).recommendation!.recommendations.length,5,'no cards published before the explanation is adopted');
    assert.equal((await workflow.plan('Show me five more homes',sid,moreId)).assignments.length,0,'same request is idempotent');
    assert.equal((await workflow.plan('Continue please',sid)).assignments.length,0,'concurrent duplicate expansion is coalesced');
    await run(sid,'c');
    const ten=await store.state(sid),tenIds=displayedCandidates(ten.recommendation).map(c=>c.listing.listing_id);
    assert.equal(tenIds.length,10);assert.deepEqual(tenIds.slice(0,5),firstIds);
    assert.equal(ten.latest_request_id,first.latest_request_id);assert.equal(retrievals,1,'cached expansion does not rerank or call Recommendation again');
    assert.deepEqual((ten.explanation as any).narrative.focus_listing_ids,tenIds.slice(5));
    assert.equal((await workflow.plan('More please',sid)).assignments.length,0,'ten-home maximum never duplicates the first batch');
    assert.equal((await store.state(sid)).recommendation!.recommendations.length,10,'ten survives a fresh database read');
    await initial(sid);assert.equal(displayedCandidates((await store.state(sid)).recommendation).length,5,'a new search resets the display');
    resultCount=7;const short=await initial();await workflow.plan('Continue please',short);await run(short,'c');assert.equal(displayedCandidates((await store.state(short)).recommendation).length,7);
    resultCount=3;const small=await initial();assert.equal((await workflow.plan('More please',small)).assignments.length,0);
    // Remove the reservoir metadata to simulate a persisted pre-feature session.
    resultCount=10;const legacy=await initial();await pool.query("UPDATE session_domain_state SET recommendation=recommendation-'presentation' WHERE session_id=$1",[legacy]);
    const callsBefore=retrievals,oldVersion=(await store.state(legacy)).requirements_version;
    assert.equal((await workflow.plan('More please',legacy)).assignments[0].agent,'b');await run(legacy,'b');await run(legacy,'c');
    assert.equal(retrievals,callsBefore+1);assert.equal(displayedCandidates((await store.state(legacy)).recommendation).length,10);
    assert.equal((await store.state(legacy)).requirements_version,oldVersion,'legacy refresh does not reinterpret requirements');
    // A new search starting during explanation must supersede the old expansion.
    const stale=await initial();await workflow.plan('More please',stale);
    const newOperation=randomUUID();await pool.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind,status) VALUES($1,$2,$3,'new request','search','queued')",[newOperation,stale,randomUUID()]);
    await pool.query('UPDATE session_domain_state SET active_operation_id=$2 WHERE session_id=$1',[stale,newOperation]);
    await run(stale,'c');assert.equal(displayedCandidates((await store.state(stale)).recommendation).length,5);
    assert.equal((await workflow.plan('More please',stale)).assignments.length,0,'pending searches cannot append old results');
    await pool.query("UPDATE domain_operations SET status='failed' WHERE id=$1",[newOperation]);
    await pool.query('UPDATE session_domain_state SET latest_request_id=$2,requirements_version=requirements_version+1 WHERE session_id=$1',[stale,randomUUID()]);
    assert.equal((await workflow.plan('More please',stale)).assignments[0].agent,'b','failed newer search refreshes current requirements rather than expanding stale cards');
    await run(stale,'b');await run(stale,'c');assert.equal(displayedCandidates((await store.state(stale)).recommendation).length,10);

    const statuses=(await pool.query("SELECT status FROM handled_results WHERE status='rejected'")).rows;assert.deepEqual(statuses,[],'no business validation failures');
    console.log('more workflow: all isolated lifecycle checks passed; paid model calls = 0');
  }finally{await pool.end();assert(/^more_test_[a-f0-9]{32}$/.test(schema));await admin.query(`DROP SCHEMA "${schema}" CASCADE`);await admin.end();}
});
