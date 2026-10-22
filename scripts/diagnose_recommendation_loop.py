"""Read one specific native trace without scanning large immutable snapshots."""
from pathlib import Path
import argparse,json,sqlite3
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--conversation',required=True);p.add_argument('--trace',required=True);args=p.parse_args()
values=dotenv_values(ROOT/'.env');path=Path(values.get('RECOMMENDATION_STATE_PATH') or ROOT/'data/.runtime/recommendation/state.sqlite')
with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
    row=db.execute('SELECT content FROM traces WHERE conversation_id=? AND trace_id=?',(args.conversation,args.trace)).fetchone()
if not row:raise RuntimeError('Trace not found')
trace=json.loads(row[0]);steps=[]
for step in trace['steps']:
    item={'kind':step['kind'],'duration_ms':step.get('duration_ms')}
    if step['kind']=='model':item.update(content=step.get('output',{}).get('content'),tool_names=[c['name'] for c in step.get('output',{}).get('tool_calls',[])])
    elif step['kind']=='tool':item.update(**{k:step['result'].get(k) for k in ['tool','status','candidate_count','verified_count','ranked_ids','error']})
    else:item.update({k:v for k,v in step.items() if k not in ['messages','tool_schemas','snapshot']})
    steps.append(item)
report={'trace_id':trace['trace_id'],'request':trace['request'],'configuration':trace['configuration'],'execution':trace['response']['execution'],'error':trace['response']['error'],'steps':steps,'diagnosis':'Empty verified ranking was correct. Model prose around valid empty JSON caused proposal parse rejection; the next invented nonempty proposal failed final gate, restarted the tools and exhausted eight model turns. This was not a 300-second timeout.'}
(ROOT/'evaluation/reports/recommendation-empty-prefix-before.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'error':report['error'],'execution':report['execution']},ensure_ascii=False))
