import {Pool} from 'pg';
import {readFile,writeFile} from 'node:fs/promises';
const fixture=JSON.parse(await readFile('data/.runtime/requirement-update-session.json','utf8'));
const pool=new Pool({connectionString:process.env.DATABASE_URL});
try{
  const tasks=(await pool.query('SELECT agent,status,error,metadata,created_at,updated_at FROM agent_tasks WHERE session_id=$1 ORDER BY created_at',[fixture.session_id])).rows;
  const traces=(await pool.query("SELECT operation_id,body FROM tool_traces WHERE session_id=$1 AND body->>'tool'='requirement_intake_attempt' ORDER BY created_at",[fixture.session_id])).rows;
  const report={session_id:fixture.session_id,tasks,traces};
  await writeFile('evaluation/reports/requirement-update-workflow-traces.json',JSON.stringify(report,null,2));
  console.log(JSON.stringify({tasks:tasks.map(t=>({agent:t.agent,status:t.status,error:t.error})),traces:traces.map(t=>({attempt:t.body.attempt,error:t.body.error,audit:t.body.audit,clarifications:t.body.draft?.clarifications}))},null,2));
}finally{await pool.end();}
