import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {writeFile} from 'node:fs/promises';
import pg from 'pg';

test('deployed workflow routes knowledge, search, explanation, viewing, contact and conversation summary independently',{skip:process.env.ORCHESTRATOR_LIVE!=='1',timeout:360000},async()=>{
 const owner=randomUUID()+randomUUID(),headers={authorization:'Bearer '+owner,'content-type':'application/json'},origin='http://127.0.0.1:8010/api/';
 const api=async(path:string,body?:unknown)=>{const r=await fetch(origin+path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body)});assert(r.ok,await r.clone().text());return r.json();};
 const pool=new pg.Pool({connectionString:process.env.DATABASE_URL}),s=await api('sessions',{}),sid=s.session_id,rows:any[]=[];
 const state=()=>api('state?session_id='+sid);
 const run=async(query:string,expected:string)=>{
   const before=await state(),request_id=randomUUID(),response=await fetch(origin+'query',{method:'POST',headers,body:JSON.stringify({query,session_id:sid,request_id,...(before.latest_request_id?{source_request_id:before.latest_request_id}:{})})});assert(response.ok);const events=await response.text();assert(!events.includes('"event":"error"'),events);
   const deadline=Date.now()+90000;
   while(Date.now()<deadline){const op=(await pool.query('SELECT kind,status,intent_plan FROM domain_operations WHERE session_id=$1 AND request_id=$2',[sid,request_id])).rows[0];if(op&&['completed','failed','superseded'].includes(op.status)){const after=await state();rows.push({query,expected,operation:op,assistant:after.messages.at(-1)});assert.equal(op.status,'completed',after.messages.at(-1)?.text);assert.equal(op.kind,expected);assert(!op.intent_plan.intent.channels.llm.failed);assert(!op.intent_plan.intent.channels.embedding.failed);return after;}await new Promise(r=>setTimeout(r,200));}throw Error('Workflow timeout');
 };
 try{
   let r=await run('HDB卧室和普通公寓最短租期分别是多少？','knowledge');assert.equal(r.requirements_version,0);assert.equal(r.recommendation,null);assert(r.messages.at(-1).knowledge.citations.length>=2);assert(/三|3/.test(r.messages.at(-1).text)&&/六|6/.test(r.messages.at(-1).text));
   r=await run('我要租新加坡国立大学三公里内的房子，预算每月2000新币，必须独立卫浴','search');assert(r.recommendation);assert(r.requirements.criteria.some((c:any)=>c.field==='place_proximity'&&c.strength==='hard'&&c.value===3000));assert(r.recommendation.recommendations.length+r.recommendation.unverified_candidates.length>0);const request=r.latest_request_id,version=r.requirements_version,calls=Number((await pool.query('SELECT count(*) FROM recommendation_calls WHERE session_id=$1',[sid])).rows[0].count);
   r=await run('请解释第一套为什么排在前面，用目前的推荐就好','explain');assert.equal(r.latest_request_id,request);assert.equal(r.requirements_version,version);assert.equal(Number((await pool.query('SELECT count(*) FROM recommendation_calls WHERE session_id=$1',[sid])).rows[0].count),calls);assert(r.messages.at(-1).text.includes(r.recommendation.unverified_candidates[0].listing.title));assert((r.messages.at(-1).text).toLowerCase().includes("to confirm"));
   const listing=[...r.recommendation.recommendations,...r.recommendation.unverified_candidates][0].listing.listing_id;
   r=await run(`请为房源ID ${listing} 生成看房询问草稿，逐项询问尚未核实的条件。`,'viewing');assert(r.actions.some((a:any)=>a.listing_ids.includes(listing)&&a.action_type==='VIEWING'));
   r=await run(`给房源ID ${listing} 的经纪人写一份联系草稿，询问押金能不能协商。`,'message');assert(r.actions.some((a:any)=>a.action_type==='MESSAGE'));
   r=await run('请根据我们前面的对话和跟进记录生成当天的会话总结文档。','daily');assert.equal(r.latest_request_id,request);assert(r.messages.at(-1).text.includes('已生成'));
   r=await run('为什么有一个卫生间，独立卫浴还是待核实？','knowledge');assert(r.messages.at(-1).knowledge.citations.length);assert.equal(r.latest_request_id,request);assert.equal(r.requirements_version,version);
   const stranger=await fetch(origin+'state?session_id='+sid,{headers:{authorization:'Bearer '+randomUUID()}});assert.equal(stranger.status,403);
   // Business boundary: an overlong unresolved request history is rejected once, not leased forever.
   const overflow=await api('sessions',{}),previous=randomUUID(),overflowRequest=randomUUID();
   await pool.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind,context) VALUES($1,$2,$3,'未完成的预算协商','search',$4)",[previous,overflow.session_id,randomUUID(),JSON.stringify(Array.from({length:32},()=>({message_id:randomUUID(),text:'预算待确认'})))]);
   await pool.query('UPDATE session_domain_state SET active_operation_id=$2 WHERE session_id=$1',[overflow.session_id,previous]);
   const rejected=await fetch(origin+'query',{method:'POST',headers,body:JSON.stringify({session_id:overflow.session_id,request_id:overflowRequest,query:'预算改为1500新币，我要找房'})});await rejected.text();
   const failed=(await pool.query('SELECT status FROM domain_operations WHERE session_id=$1 AND request_id=$2',[overflow.session_id,overflowRequest])).rows[0];assert.equal(failed.status,'failed');
   await pool.query("UPDATE domain_operations SET status='superseded' WHERE id=$1",[previous]);rows.push({query:'超过32条未完成上下文的编排',expected:'failed_without_recovery_loop',correct:true});
   await writeFile('data/.runtime/orchestrator-rag-ui-fixture.json',JSON.stringify({owner,session_id:sid,listing_id:listing}));
 }finally{await writeFile('evaluation/reports/orchestrator-rag-workflow.json',JSON.stringify({rows},null,2));await pool.end();}
});
