import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import pg from 'pg';
import {DomainStore,hash} from '../src/stores/domain.js';
import {PostgresAgentTaskStore} from '../src/agent-task-store.js';
import {PostgresSessionEventStore} from '../src/session-store.js';
import {WorkflowService} from '../src/domain/workflow.js';
import {compare,validateResponse,type RecommendationRequest,type RecommendationResponse} from '../src/contracts/recommendation-v1.js';
import {classify} from '../src/adapters/recommendation.js';

test('isolated PostgreSQL card lifecycle preserves requirements, resumes A/B/C, rejects stale plans and stops after all questions', {skip:process.env.REFINEMENT_DB_TEST!=='1'},async()=>{
  const admin=new pg.Pool({connectionString:process.env.TEST_DATABASE_URL||process.env.DATABASE_URL}),schema='refinement_test_'+randomUUID().replaceAll('-','');
  await admin.query(`CREATE SCHEMA "${schema}"`);
  const pool=new pg.Pool({connectionString:process.env.TEST_DATABASE_URL||process.env.DATABASE_URL,options:`-c search_path=${schema}`});
  const tasks=new PostgresAgentTaskStore(pool),events=new PostgresSessionEventStore(pool),store=new DomainStore(pool,tasks,events);
  try{
    await tasks.setup();await events.setup();await store.setup();
    const f=JSON.parse(await readFile('evaluation/inputs/explanation-update-ranking.json','utf8')),dataset=f.recommendation.state.coverage.dataset_version;
    const workflow=new WorkflowService(store,dataset),session=await store.create('refinement-isolated-test-owner'),operation=randomUUID(),source=randomUUID();
    f.recommendation.request_id=source;f.recommendation.conversation_id=session;
    await pool.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind,status) VALUES($1,$2,$3,'fixture','search','completed')",[operation,session,randomUUID()]);
    await pool.query('INSERT INTO recommendation_calls(id,session_id,operation_id,request,response) VALUES($1,$2,$3,$4,$5)',[source,session,operation,JSON.stringify({}),JSON.stringify(f.recommendation)]);
    await store.transaction(async tx=>{const s=await store.lock(tx,session);s.requirements=f.requirements;s.requirements_version=f.recommendation.requirements_version;s.active_operation_id=operation;s.latest_request_id=source;s.recommendation=f.recommendation;
      store.message(s,'assistant','这是本轮房源的推荐说明。');const id=await workflow.refinement.generate(tx,s);assert(id);s.messages.at(-1)!.refinement_id=id!;await store.saveState(tx,s);});
    const before=await store.state(session),plan=before.refinement_plans[0];assert.deepEqual(plan.questions.map(q=>q.kind),['property_type','area','ideal_rent']);
    assert.equal(await store.transaction(async tx=>workflow.refinement.generate(tx,await store.lock(tx,session))),null);
    const answers=[{question_id:'property_type',action:'set',values:['Apartment'],strength:'soft'},{question_id:'area',action:'set',min:30,unit:'sqm',strength:'soft'},{question_id:'ideal_rent',action:'set',max:2000}],input={submission_id:randomUUID(),answers};
    const queued=await workflow.refinement.submit(session,plan.id,input);assert.equal(queued.status,'queued');
    assert.deepEqual(await workflow.refinement.submit(session,plan.id,input),queued);
    assert.equal(Number((await pool.query("SELECT count(*) FROM domain_operations WHERE kind='search'")).rows[0].count),2);
    await assert.rejects(()=>workflow.refinement.submit(session,plan.id,{...input,submission_id:randomUUID()}),/已经提交/);
    await assert.rejects(()=>workflow.refinement.submit(awaitDummySession(),plan.id,input),/会话不存在|不存在/);
    let calls=0;const all=[...f.recommendation.recommendations,...f.recommendation.unverified_candidates];
    workflow.recommender.recommend=async(req:RecommendationRequest)=>{
      assert.deepEqual(req.behavior_context?.listing_view_durations,[]);assert.deepEqual(req.behavior_context?.book_viewing_listings,[]);
      calls++;assert.equal(req.requirements.criteria.find(c=>c.field==='price'&&c.strength==='hard')!.value,5000);
      assert(req.requirements.criteria.some(c=>c.field==='price'&&c.strength==='soft'&&c.value===2000));
      assert(req.requirements.criteria.some(c=>c.field==='floor_area_sqft'&&c.strength==='soft'));
      const response:RecommendationResponse={...structuredClone(f.recommendation),request_id:req.request_id,conversation_id:req.conversation_id,requirements_version:req.requirements_version,recommendations:[],unverified_candidates:[],evidence:[],native:undefined};
      let eligible=0,unknown=0,excluded=0;
      for(const original of all){
        const candidate=structuredClone(original);candidate.score=0;candidate.score_breakdown=[];
        candidate.criterion_results=req.requirements.criteria.map(c=>{
          const previous=original.criterion_results.find((r:any)=>r.criterion_id===c.criterion_id);
          const value=c.field==='place_proximity'?previous?.observed_value??null:(candidate.listing as any)[c.field]??null;
          const eid='fixture:'+candidate.listing.listing_id+':'+c.criterion_id;
          response.evidence.push({evidence_id:eid,listing_id:candidate.listing.listing_id,field_path:c.field,value,...(c.unit?{unit:c.unit}:{}),...(c.field==='place_proximity'?{distance_type:c.distance_type}:{}),source_kind:'fixture',source_ref:'isolated-fixture',dataset_version:dataset,retrieved_at:new Date().toISOString(),verified_at:null});
          return {criterion_id:c.criterion_id,status:value===null?'unknown':compare(value,c)?'pass':'fail',observed_value:value,...(c.unit?{unit:c.unit}:{}),evidence_ids:[eid]};
        });candidate.explanation_evidence={strengths:[],compromises:[],unknowns:[]};
        const group=classify(candidate.criterion_results,req.requirements.criteria);if(group==='excluded'){excluded++;continue;}
        if(group==='eligible'){eligible++;candidate.rank=response.recommendations.length+1;if(response.recommendations.length<4)response.recommendations.push(candidate);}
        else{unknown++;candidate.rank=response.unverified_candidates.length+1;if(response.unverified_candidates.length<4)response.unverified_candidates.push(candidate);}
      }
      response.state.counts={assessed:all.length,eligible,unverified:unknown,excluded};response.status=response.recommendations.length?'success':'no_match';return validateResponse(response,req);
    };
    async function run(agent:'a'|'b'|'c'){
      const task=await tasks.claimNext(session,agent);assert(task);
      let output:string;
      if(agent==='c'){
        const {env,body}=await store.verifyTask(task.prompt,session,task.id,agent),payload={explanation:{source_request_id:body.recommendation.request_id,summary:'已根据您补充的房屋类型、面积和理想月租更新推荐。'}};
        const ref=await store.savePayload(session,env.operation_id,payload);output=JSON.stringify({schema_version:'1.1',agent,operation_id:env.operation_id,requirements_version:env.requirements_version,payload_ref:ref,payload_hash:hash(payload)});
      }else output=await workflow.run(agent,task.prompt,session,task.id);
      await tasks.finish(task.id,output,null);await workflow.apply({...task,status:'completed',output});
    }
    await run('a');await run('b');await run('c');assert.equal(calls,1);
    const after=await store.state(session);assert.equal(after.requirements_version,before.requirements_version+1);assert.notEqual(after.latest_request_id,source);
    assert.equal(after.refinement_plans.length,1);assert.equal(after.refinement_plans[0].status,'SUBMITTED');assert(after.messages.at(-1)!.text.includes('更新推荐'));
    assert.equal((await pool.query('SELECT status FROM domain_operations WHERE id=$1',[queued.operation_id])).rows[0].status,'completed');
    // Ownership and stale version checks use independent session rows in this schema.
    const other=await store.create('other-owner');await assert.rejects(()=>workflow.refinement.submit(other,plan.id,input),/不存在/);
    await pool.query("UPDATE requirement_refinement_plans SET status='OPEN',submission_id=NULL WHERE id=$1",[plan.id]);
    await assert.rejects(()=>workflow.refinement.submit(session,plan.id,{...input,submission_id:randomUUID()}),/条件已更新/);
    await pool.query("UPDATE requirement_refinement_plans SET status='SUBMITTED' WHERE id=$1",[plan.id]);
    async function fresh(){
      const sid=await store.create('isolated-other-card-owner'),op=randomUUID(),src=randomUUID(),recommendation={...f.recommendation,request_id:src,conversation_id:sid};
      await pool.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind,status) VALUES($1,$2,$3,'fixture','search','completed')",[op,sid,randomUUID()]);
      await pool.query('INSERT INTO recommendation_calls(id,session_id,operation_id,request,response) VALUES($1,$2,$3,$4,$5)',[src,sid,op,JSON.stringify({}),JSON.stringify(recommendation)]);
      await store.transaction(async tx=>{const s=await store.lock(tx,sid);Object.assign(s,{requirements:f.requirements,requirements_version:recommendation.requirements_version,active_operation_id:op,latest_request_id:src,recommendation});await store.saveState(tx,s);await workflow.refinement.generate(tx,s);});
      return store.state(sid);
    }
    const skipped=await fresh(),skipPlan=skipped.refinement_plans[0];
    const unchanged=await workflow.refinement.submit(skipped.session_id,skipPlan.id,{submission_id:randomUUID(),answers:skipPlan.questions.map(q=>({question_id:q.id,action:'skip'}))});
    assert.equal(unchanged.status,'unchanged');const retained=await store.state(skipped.session_id);
    assert.equal(retained.latest_request_id,skipped.latest_request_id);assert.equal(retained.active_operation_id,skipped.active_operation_id);assert(retained.last_event_id>skipped.last_event_id);
    assert.equal((await tasks.activeAgents(skipped.session_id)).length,0);
    const dismissed=await fresh();await workflow.refinement.dismiss(dismissed.session_id,dismissed.refinement_plans[0].id);
    const dismissedState=await store.state(dismissed.session_id);assert(dismissedState.last_event_id>dismissed.last_event_id);
    assert.equal(await store.transaction(async tx=>workflow.refinement.generate(tx,await store.lock(tx,dismissed.session_id))),null);
    await writeFile('data/.runtime/refinement-ui-fixture.json',JSON.stringify({before,after,answers,dataset}));
    await writeFile('evaluation/reports/requirement-refinement-workflow.json',JSON.stringify({status:'passed',model_calls:0,recommender:'recorded fixture, no native algorithm changes',checks:['persistent card after explanation','deduplicated card','structured A without LLM','complete B request and fresh source','C completion and exhaustion','double-submit idempotence','cross-session rejected','old version rejected','all skipped keeps current results without queued work','dismissal suppresses further cards'],requirements_before:before.requirements,requirements_after:after.requirements},null,2));
  }finally{await pool.end();assert(/^refinement_test_[a-f0-9]{32}$/.test(schema));await admin.query(`DROP SCHEMA "${schema}" CASCADE`);await admin.end();}
});
function awaitDummySession(){return randomUUID();}
