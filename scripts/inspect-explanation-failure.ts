import pg from 'pg';
import {readFile} from 'node:fs/promises';
const pool=new pg.Pool({connectionString:process.env.DATABASE_URL});
try{
 const f=JSON.parse(await readFile(process.argv[2]||'data/.runtime/explanation-ranking-session.json','utf8'));
 console.log(JSON.stringify((await pool.query("SELECT agent,status,error,updated_at FROM agent_tasks WHERE session_id=$1 AND status='failed' ORDER BY updated_at DESC LIMIT 3",[f.session_id])).rows,null,2));
 const s=(await pool.query('SELECT requirements,recommendation FROM session_domain_state WHERE session_id=$1',[f.session_id])).rows[0];
 console.log(JSON.stringify({requirements:s.requirements,rows:s.recommendation?.recommendations.map((r:any)=>({title:r.listing.title,price:r.listing.price,criteria:r.criterion_results})),native:s.recommendation?.native},null,2));
}finally{await pool.end();}
