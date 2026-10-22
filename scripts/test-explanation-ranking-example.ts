import {readFile,writeFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';
import pg from 'pg';
const pool=new pg.Pool({connectionString:process.env.DATABASE_URL});
const fixturePath='data/.runtime/explanation-ranking-session.json';
const saved=process.argv.includes('--reuse')?JSON.parse(await readFile(fixturePath,'utf8')):null;
const owner=saved?.owner||randomUUID()+randomUUID(),headers={authorization:'Bearer '+owner,'content-type':'application/json'};
async function api(path:string,body?:unknown){const r=await fetch('http://127.0.0.1:8010/api/'+path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body)});assert(r.ok,await r.clone().text());return r.headers.get('content-type')?.startsWith('text/event-stream')?null:r.json();}
const sid=saved?.session_id||(await api('sessions',{})).session_id;
await writeFile(fixturePath,JSON.stringify({owner,session_id:sid}));
const state=()=>api('state?session_id='+sid);
const search='我要租南洋理工大学附近5公里内的房子，月租不超过2000新币，符合条件后优先推荐离学校更近的房子。';
const question='为什么这样推荐？请解释学校距离如何影响排序，以及前两套有什么区别。';
const report:any={search,question,external_messages_sent:0};
async function query(text:string,kind:string){
  const before=await state(),request_id=randomUUID();
  await api('query',{query:text,session_id:sid,request_id,...(before.latest_request_id?{source_request_id:before.latest_request_id}:{})});
  const deadline=Date.now()+480000;
  while(Date.now()<deadline){const op=(await pool.query('SELECT kind,status,intent_plan FROM domain_operations WHERE session_id=$1 AND request_id=$2',[sid,request_id])).rows[0];
    if(op&&['completed','failed','superseded'].includes(op.status)){
      const result=await state();assert.equal(op.status,'completed',result.messages.at(-1)?.text);assert.equal(op.kind,kind);assert.equal(op.intent_plan.agent,kind==='search'?'a':'c');return result;
    }await new Promise(r=>setTimeout(r,400));
  }throw Error('Workflow timed out');
}
try{
  const first=saved?await state():await query(search,'search'),rec=first.recommendation;
  assert(rec.recommendations.length>=2);
  const near=first.requirements.criteria.find((c:any)=>c.field==='place_proximity'&&c.strength==='soft'&&c.distance_preference==='nearer');assert(near);
  const rows=rec.recommendations.map((r:any)=>({title:r.listing.title,rent:r.listing.price,distance_m:r.criterion_results.find((c:any)=>c.criterion_id===near.criterion_id)?.observed_value,score:rec.native.scores[r.listing.listing_id]}));
  assert(rows.every((r:any)=>typeof r.distance_m==='number'));
  assert(new Set(rows.map((r:any)=>r.distance_m)).size>1,'Test needs differing observed distances');
  assert(rows.every((r:any,i:number)=>!i||r.distance_m>=rows[i-1].distance_m));
  assert(rows.every((r:any)=>r.score.contributions.some((c:any)=>c.feature===near.criterion_id&&!c.unknown&&c.weight>0)));
  const count=async()=>Number((await pool.query('SELECT count(*) FROM recommendation_calls WHERE session_id=$1',[sid])).rows[0].count);
  const before=await count();const explanation=await query(question,'explain');
  assert.deepEqual(explanation.recommendation,rec);assert.deepEqual(explanation.requirements,first.requirements);assert.equal(await count(),before);
  report.results=rows.map(({score,...rest}:any)=>rest);report.answer=explanation.messages.at(-1).text;report.generation=explanation.explanation.generation;
  report.verified_actual_distance_sort=true;report.no_recommendation_rerun=true;report.status='passed';
}catch(e){report.status='failed';report.error=e instanceof Error?e.message:String(e);process.exitCode=1;}
finally{await writeFile('evaluation/reports/explanation-ranking-example.json',JSON.stringify(report,null,2));await pool.end();}
console.log(JSON.stringify(report));
