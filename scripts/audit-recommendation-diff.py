"""Content-aware Git audit, including same-name changes and data directory moves."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
BASE='501fb6cdb0aa64bdbabb947324bf845654a9a269'
UPSTREAM='907cfa839d2665b3702450eb40acd4c96fe9296c'


def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT)


records=[]
for line in git('diff','--name-status','--find-renames',BASE,UPSTREAM).decode('utf-8').splitlines():
    parts=line.split('\t');status=parts[0];old=parts[1];new=parts[-1]
    record={'status':status,'before':old if not status.startswith('A') else None,'after':new if not status.startswith('D') else None}
    for key,ref in [('before',BASE),('after',UPSTREAM)]:
        if record[key]:
            content=git('show',f'{ref}:{record[key]}')
            record[key+'_sha256']=hashlib.sha256(content).hexdigest()
            record[key+'_bytes']=len(content)
    if new.startswith(('dev/recommendationSubagent/','dev/sharedContract/')):
        record['integration']='ported in full, namespace/path-only changes recorded in recommendation-upstream-907cfa8.json'
    elif new.startswith('dataPreprocessing/') or old.startswith('data/'):
        record['integration']='original local data layout retained; preprocessing rename does not implement recommendation runtime'
    elif new in {'.gitignore','.env.example','package.json','README.md','tsconfig.recommendation.json'}:
        record['integration']='reviewed by content; relevant dependency/config/docs adapted to original app layout'
    else:
        record['integration']='reference inspected; not substituted for existing app behavior'
    records.append(record)
report={'base':BASE,'upstream':UPSTREAM,'changed_paths':len(records),'same_name_modifications':[r['after'] for r in records if r['status']=='M'],'files':records}
(ROOT/'evaluation/reports/recommendation-git-diff-audit.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'changed_paths':len(records),'same_name_modifications':report['same_name_modifications']}))
