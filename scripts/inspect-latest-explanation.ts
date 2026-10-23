import pg from 'pg';
import {writeFile} from 'node:fs/promises';
const pool=new pg.Pool({connectionString:process.env.DATABASE_URL});
try{
 const tasks=(await pool.query(`SELECT t.id,t.session_id,t.error,t.metadata,t.updated_at,o.query,o.kind FROM agent_tasks t LEFT JOIN domain_operations o ON o.id::text=t.metadata->>'operation_id' WHERE t.agent='c' AND t.status='failed' ORDER BY t.updated_at DESC LIMIT 3`)).rows;
 console.log(JSON.stringify(tasks.map(t=>({id:t.id,query:t.query,error:t.error,at:t.updated_at})),null,2));
 const task=tasks[0];
 const state=(await pool.query('SELECT requirements,recommendation,explanation FROM session_domain_state WHERE session_id=$1',[task.session_id])).rows[0];
 const input=(await pool.query('SELECT body FROM domain_payloads WHERE ref=$1 AND session_id=$2',[task.metadata.input_ref,task.session_id])).rows[0]?.body;
 await writeFile('data/.runtime/explanation-update-failure.json',JSON.stringify({task,state,input},null,2));
 const rec=state.recommendation;
 console.log(JSON.stringify({input_keys:Object.keys(input||{}),requirements:state.requirements,ranking:Object.entries(rec.native.scores).map(([id,s]:[string,any])=>({id,ordering:s.ordering,features:s.contributions.filter((c:any)=>!c.unknown).map((c:any)=>c.feature)})),first:rec.recommendations[0]},null,2));
}finally{await pool.end();}
