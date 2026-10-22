import pg from 'pg';
const pool=new pg.Pool({connectionString:process.env.DATABASE_URL});
try{
  const rows=(await pool.query(`SELECT o.session_id,o.kind,o.query,o.status,o.created_at,
    (SELECT json_agg(json_build_object('agent',t.agent,'status',t.status,'created',t.created_at,'updated',t.updated_at)) FROM agent_tasks t WHERE t.metadata->>'operation_id'=o.id::text) AS tasks,
    (SELECT json_agg(json_build_object('id',e.event_id,'type',e.event->>'type','at',e.created_at)) FROM session_events e WHERE e.session_id=o.session_id AND e.created_at>=o.created_at AND e.created_at<o.created_at+interval '3 minutes' AND e.event->>'type' IN ('workflow_result','workflow_error','stage')) AS events
    FROM domain_operations o WHERE o.kind='explain' ORDER BY o.created_at DESC LIMIT 12`)).rows;
  console.log(JSON.stringify(rows,null,2));
}finally{await pool.end();}
