import {Pool} from 'pg';
import {writeFile} from 'node:fs/promises';

const pool=new Pool({connectionString:process.env.DATABASE_URL});
try{
  const prefix=process.argv[2]||'那我修改要求，我要租裕廊东附近5公里内的房子';
  const rows=(await pool.query(`SELECT o.id,o.session_id,o.query,o.created_at,o.status,t.id AS task_id,t.error,t.prompt,t.updated_at
    FROM domain_operations o JOIN agent_tasks t ON t.metadata->>'operation_id'=o.id::text
    WHERE o.query LIKE $1 AND t.agent='a' ORDER BY o.created_at DESC LIMIT 3`,[prefix+'%'])).rows;
  const result=[];
  for(const row of rows){
    const env=JSON.parse(row.prompt);
    const input=(await pool.query('SELECT body FROM domain_payloads WHERE ref=$1 AND session_id=$2',[env.input_ref,row.session_id])).rows[0]?.body;
    result.push({...row,prompt:undefined,input:{query:input?.query,requirements:input?.requirements,pending:input?.pending}});
  }
  await writeFile('evaluation/reports/requirement-update-failure-before.json',JSON.stringify(result,null,2));
  console.log(JSON.stringify(result,null,2));
}finally{await pool.end();}
