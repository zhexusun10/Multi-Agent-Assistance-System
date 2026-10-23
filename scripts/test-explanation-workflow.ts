import {readFile,writeFile} from 'node:fs/promises';
import {randomUUID,createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import pg from 'pg';
const saved=process.argv.includes('--reuse')?JSON.parse(await readFile('data/.runtime/explanation-model-session.json','utf8')):null;
const owner=saved?.owner||randomUUID()+randomUUID(),headers={authorization:'Bearer '+owner,'content-type':'application/json'},origin='http://127.0.0.1:8010/api/';
const pool=new pg.Pool({connectionString:process.env.DATABASE_URL});
const api=async(path:string,body?:unknown)=>{const r=await fetch(origin+path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body)});assert(r.ok,await r.clone().text());return r.headers.get('content-type')?.startsWith('text/event-stream')?{}:r.json();};
const s=saved||await api('sessions',{}),sid=s.session_id,rows:any[]=[],report:any={external_messages_sent:0,checks:rows,reused_full_data_search:!!saved};
report.source_hashes=Object.fromEntries(await Promise.all(['src/agents/explanation/contracts.ts','src/agents/explanation/evidence.ts','src/agents/explanation/prompt.ts','src/agents/explanation/few-shot.ts','src/agents/explanation/validation.ts','src/agents/explanation/generator.ts','skills/evidence-based-explanation/SKILL.md'].map(async path=>[path,createHash('sha256').update(await readFile(path)).digest('hex')])));
await writeFile('data/.runtime/explanation-model-session.json',JSON.stringify({owner,session_id:sid}));
const state=()=>api('state?session_id='+sid);
async function query(text:string,kind:string){
  const before=await state(),request_id=randomUUID();
  await api('query',{session_id:sid,request_id,query:text,...(before.latest_request_id?{source_request_id:before.latest_request_id}:{})});
  const deadline=Date.now()+480000;
  while(Date.now()<deadline){const op=(await pool.query('SELECT kind,status,intent_plan FROM domain_operations WHERE session_id=$1 AND request_id=$2',[sid,request_id])).rows[0];if(op&&['completed','failed','superseded'].includes(op.status)){
    const after=await state();rows.push({query:text,operation:op,answer:after.messages.at(-1)?.text,generation:after.explanation?.generation});
    assert.equal(op.status,'completed',after.messages.at(-1)?.text);assert.equal(op.kind,kind);assert.equal(op.intent_plan.agent,kind==='search'?'a':'c');assert.equal(after.explanation.generation.mode,'llm_grounded');assert.equal(after.explanation.generation.semantic_audit,'passed');
    assert(!/原生|融合匹配分|显式|隐式|权重|快照|已评估|Exact criterion|native:|21431|21,431|408/.test(after.messages.at(-1).text));
    if(!/[\u3400-\u9fff]/.test(text))assert(!/[\u3400-\u9fff]/.test(after.messages.at(-1).text),'English explanation must use English');
    if(kind==='explain'){assert.equal(after.latest_request_id,before.latest_request_id);assert.equal(after.requirements_version,before.requirements_version);assert.deepEqual(after.recommendation,before.recommendation);}
    console.log('workflow '+rows.length+' passed');return after;
  }await new Promise(r=>setTimeout(r,500));}throw Error('Explanation workflow timeout');
}
try{
  let current=saved?await state():await query('你好，我要租南洋理工大学附近5公里内的房子，预算在2000新币每月，房子附近1公里内最好有MRT或公交站','search');
  const before=Number((await pool.query('SELECT count(*) FROM recommendation_calls WHERE session_id=$1',[sid])).rows[0].count);
  const exchange=(await api('evidence?session_id='+sid+'&ref='+current.recommendation.native.exchange_ref)).data;
  await writeFile('evaluation/reports/explanation-real-snapshot.json',JSON.stringify({requirements:current.requirements,recommendation:current.recommendation,archive:exchange},null,2));
  for(const q of ['给我解释一下为什么推荐这几套房子？','为什么第一套放最前面？','帮我比较前两套，简短说说各自适合我的地方。','现在这个顺序，是按租金还是按离学校的距离排的？','Explain why you recommended these properties.'])current=await query(q,'explain');
  assert.equal(Number((await pool.query('SELECT count(*) FROM recommendation_calls WHERE session_id=$1',[sid])).rows[0].count),before);
  report.status='passed';report.no_rerun_or_requirement_change=true;report.session_id=sid;
}catch(e){report.status='failed';report.error=e instanceof Error?e.message:String(e);process.exitCode=1;}
finally{await writeFile('evaluation/reports/explanation-model-workflow.json',JSON.stringify(report,null,2));await pool.end();}
console.log(JSON.stringify({status:report.status,checks:rows.length,external_messages_sent:0}));
