import {readFile,writeFile} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';
import pg from 'pg';
import {createModel} from '../src/model.js';
import {generateExplanation} from '../src/agents/explanation/generator.js';
const pool=new pg.Pool({connectionString:process.env.DATABASE_URL});
const report:any={checked_at:new Date().toISOString(),external_messages_sent:0,checks:[]};
const paths=['src/agents/explanation/contracts.ts','src/agents/explanation/evidence.ts','src/agents/explanation/validation.ts','src/agents/explanation/generator.ts','src/agents/explanation/prompt.ts','skills/evidence-based-explanation/SKILL.md'];
report.source_hashes=Object.fromEntries(await Promise.all(paths.map(async p=>[p,createHash('sha256').update(await readFile(p)).digest('hex')])));
try{
 if(process.argv.includes('--replay')){
  const source=JSON.parse(await readFile('data/.runtime/explanation-failure-source.json','utf8'));
  const {requirements:req,recommendation:rec}=source.state;
  const archive=(await pool.query('SELECT body FROM domain_payloads WHERE ref=$1 AND session_id=$2',[rec.native.exchange_ref,source.task.session_id])).rows[0]?.body;
  const before=JSON.stringify(rec),model=await createModel(undefined,{maxTokens:2400,temperature:0}),skill=await readFile('skills/evidence-based-explanation/SKILL.md','utf8');
  const out=await generateExplanation(req,rec,'actual installed data',source.task.query,model,skill,archive);
  assert.equal(JSON.stringify(rec),before);assert.match(out.summary,/D05/i);assert(!/本轮未完成/.test(out.summary));
  report.checks.push({mode:'read-only replay of actual failure',original_error:source.task.error,query:source.task.query,answer:out.summary,generation:out.generation,eligible:rec.recommendations.length,unknown:rec.unverified_candidates.length});
 }else{
  const reuse=process.argv.includes('--reuse'),path='data/.runtime/explanation-district-session.json';
  const saved=reuse?JSON.parse(await readFile(path,'utf8')):null,owner=saved?.owner||randomUUID()+randomUUID();
  const headers={authorization:'Bearer '+owner,'content-type':'application/json'};
  const api=async(path:string,body?:unknown)=>{
   const r=await fetch('http://127.0.0.1:8010/api/'+path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body)});
   assert(r.ok,await r.clone().text());return r.headers.get('content-type')?.startsWith('text/event-stream')?{}:r.json();
  };
  const sid=saved?.session_id||(await api('sessions',{})).session_id;
  await writeFile(path,JSON.stringify({owner,session_id:sid}));
  const state=()=>api('state?session_id='+sid);
  const questions=reuse?['解释一下为什么推荐这几套房子？']:['帮我找租房，预算不超过5000新币，至少2房，D05','解释一下为什么推荐这几套房子？'];
  for(const query of questions){
   const before=await state(),request_id=randomUUID(),search=query.startsWith('帮我找');
   const calls=Number((await pool.query('SELECT count(*) FROM recommendation_calls WHERE session_id=$1',[sid])).rows[0].count);
   await api('query',{session_id:sid,request_id,query,...(before.latest_request_id?{source_request_id:before.latest_request_id}:{})});
   const deadline=Date.now()+480000;let completed=false;
   while(Date.now()<deadline){
    const op=(await pool.query('SELECT kind,status,intent_plan FROM domain_operations WHERE session_id=$1 AND request_id=$2',[sid,request_id])).rows[0];
    if(op&&['completed','failed','superseded'].includes(op.status)){
     const after=await state();assert.equal(op.status,'completed',after.messages.at(-1)?.text);assert.equal(op.kind,search?'search':'explain');
     assert.equal(after.explanation.generation.mode,'llm_grounded');assert.equal(after.explanation.generation.semantic_audit,'passed');
     assert.match(after.messages.at(-1).text,/D05/);assert.match(after.messages.at(-1).text,/您的要求/);assert.match(after.messages.at(-1).text,/符合的部分/);
     assert(after.recommendation.recommendations.length>0);assert(after.recommendation.unverified_candidates.length>0);
     if(!search){assert.equal(op.intent_plan.agent,'c');assert.deepEqual(after.recommendation,before.recommendation);assert.deepEqual(after.requirements,before.requirements);assert.equal(Number((await pool.query('SELECT count(*) FROM recommendation_calls WHERE session_id=$1',[sid])).rows[0].count),calls);}
     report.checks.push({mode:search?'fresh A/B/C workflow':'direct explanation C',query,answer:after.messages.at(-1).text,generation:after.explanation.generation,operation:op,eligible:after.recommendation.recommendations.length,unknown:after.recommendation.unverified_candidates.length});completed=true;console.log(report.checks.at(-1).mode+' passed');break;
    }await new Promise(r=>setTimeout(r,400));
   }assert(completed,'workflow timeout');
  }
 }
 report.status='passed';
}catch(e){report.status='failed';report.error=e instanceof Error?e.message:String(e);report.diagnostic=(e as any).diagnostic;process.exitCode=1;}
finally{await pool.end();await writeFile('evaluation/reports/explanation-district-'+(process.argv.includes('--replay')?'replay':'workflow')+'.json',JSON.stringify(report,null,2));}
console.log(JSON.stringify(report,null,2));
