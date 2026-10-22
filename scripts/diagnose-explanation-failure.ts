import pg from 'pg';
import {mkdir,writeFile} from 'node:fs/promises';
const pool=new pg.Pool({connectionString:process.env.DATABASE_URL});
try{
 const tasks=(await pool.query(`SELECT t.id,t.session_id,t.status,t.error,t.metadata,t.created_at,t.updated_at,o.query,o.kind,o.request_id
 FROM agent_tasks t LEFT JOIN domain_operations o ON o.id::text=t.metadata->>'operation_id'
 WHERE t.agent='c' AND t.status='failed' ORDER BY t.updated_at DESC LIMIT 8`)).rows;
 console.log(JSON.stringify(tasks,null,2));
 if(tasks.length){
  const task=tasks.find(t=>/5000|D05/i.test(t.query||''))||tasks[0];
  const state=(await pool.query('SELECT requirements,recommendation,explanation FROM session_domain_state WHERE session_id=$1',[task.session_id])).rows[0];
  await mkdir('data/.runtime',{recursive:true});
  await writeFile('data/.runtime/explanation-failure-source.json',JSON.stringify({task,state},null,2));
  console.log(JSON.stringify({requirements:state?.requirements,rows:[...(state?.recommendation?.recommendations||[]),...(state?.recommendation?.unverified_candidates||[])].map((r:any)=>({title:r.listing.title,price:r.listing.price,criteria:r.criterion_results})),saved:'data/.runtime/explanation-failure-source.json'},null,2));
 }
}finally{await pool.end();}
