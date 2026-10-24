"""RETIRED: 907cfa8-baseline-only parity evidence (spec R15, recommendation v2 P7).

This script compared the original upstream engine (907cfa8) with the migrated copy in this repository, using
contract 1.0 (ViewingEvent feedback, feedback_version, weighted-v1 profile). The native module now implements
contract 2.0 (feedback snapshot, full-pool fused-v2 scoring, select-v1 selection) and every intended difference
is declared in contracts/releases/recommendation-downstream-patches.json, so business-output equality with
907cfa8 is no longer the acceptance criterion. evaluation/reports/native-upstream-parity.json is kept as the
historical record of the migration and is NOT regenerated.

By default the script refuses to run (exit 2). It can still reproduce the historical comparison against an
archived contract-1.0 checkout of this repository (for example commit 4d36507):

  python scripts/compare-recommendation-upstream.py --reference <907cfa8 tree> \
      --legacy-root <contract-1.0 checkout> --output <new report path>

It never writes evaluation/reports/native-upstream-parity.json.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
PROGRAM=r'''
import importlib,json,tempfile
from pathlib import Path
prefix=PREFIX
Service=importlib.import_module(prefix+'.service').RecommendationService
repositories=importlib.import_module(prefix+'.repositories.connections').repositories
StateStore=importlib.import_module(prefix+'.state.store').StateStore
Controller=importlib.import_module(prefix+'.agent.controller').ScriptedController
contract=CONTRACT
Request=importlib.import_module(contract+'.request').RecommendationRequest
ViewingEvent=importlib.import_module(contract+'.feedback').ViewingEvent
folder=Path(importlib.import_module(prefix).__file__).parent
requirements=json.loads((folder/'examples/requirements.json').read_text())
output=[]
with tempfile.TemporaryDirectory() as tmp:
 a,b=repositories('fixture');s=Service(a,b,StateStore(Path(tmp)/'state.sqlite'),Controller())
 request=Request(request_id='initial',conversation_id='comparison',requirements_version=1,requirements=requirements)
 first=s.recommend(request);output.append(first.model_dump(mode='json'))
 event=ViewingEvent.model_validate({'event_id':'event-1','conversation_id':'comparison','recommendation_request_id':'initial','listing_id':first.recommendations[0].listing.listing_id,'event_type':'listing_view_duration','source':'sample','occurred_at':'2026-10-20T06:00:00+00:00','payload':{'view_session_id':'view-1','duration_seconds':90}})
 second_request=request.model_copy(update={'request_id':'feedback','previous_state_id':first.state.state_id,'feedback':[event]})
 second=s.recommend(second_request);output.append(second.model_dump(mode='json'))
 replay=s.recommend(second_request);assert replay==second
 duplicate=s.recommend(second_request.model_copy(update={'request_id':'duplicate','previous_state_id':second.state.state_id}));output.append(duplicate.model_dump(mode='json'))
 assert duplicate.state.feedback_version==1
 tighter=request.requirements.model_copy(deep=True);tighter.criteria[0].value=2300
 third=s.recommend(request.model_copy(update={'request_id':'tightened','previous_state_id':duplicate.state.state_id,'requirements_version':2,'requirements':tighter,'feedback':[]}));output.append(third.model_dump(mode='json'))
 before=s.store.current('comparison');s.close()
 a,b=repositories('fixture');s=Service(a,b,StateStore(Path(tmp)/'state.sqlite'),Controller());assert s.store.current('comparison')==before
 print(json.dumps({'responses':output,'profile':before['profile'],'gate_passed':any(step['kind']=='final_gate' and step.get('passed') for step in s.store.trace('comparison')['steps'])}))
 s.close()
'''


def canonical(value):
    if isinstance(value,list): return [canonical(v) for v in value]
    if isinstance(value,dict):
        return {k:canonical(v) for k,v in value.items() if k not in {'observed_at','duration_ms','trace_id','trace_ref','state_id','candidate_pool_ref'}}
    return value


RETIRED={'status':'retired','upstream':'907cfa8','reason':'907cfa8-baseline-only evidence: the native module implements contract 2.0; declared differences are in contracts/releases/recommendation-downstream-patches.json','historical_report':'evaluation/reports/native-upstream-parity.json (kept, not regenerated)','rerun':'--reference <907cfa8 tree> --legacy-root <contract-1.0 checkout> --output <path>'}
HISTORICAL_REPORT=ROOT/'evaluation/reports/native-upstream-parity.json'


def main():
    p=argparse.ArgumentParser();p.add_argument('--reference');p.add_argument('--legacy-root');p.add_argument('--output');args=p.parse_args()
    if not (args.reference and args.legacy_root and args.output):
        print(json.dumps(RETIRED));sys.exit(2)
    output=Path(args.output).resolve()
    if output==HISTORICAL_REPORT.resolve():
        p.error('the historical report is kept as evidence and never overwritten')
    results=[]
    for folder,prefix,contract in [(Path(args.reference),'dev.recommendationSubagent','dev.sharedContract.recommendationSubagentContract'),(Path(args.legacy_root),'recommendation','recommendation.contracts')]:
        code=PROGRAM.replace('PREFIX',repr(prefix)).replace('CONTRACT',repr(contract))
        result=subprocess.run([sys.executable,'-c',code],cwd=folder,text=True,capture_output=True,check=True)
        results.append(canonical(json.loads(result.stdout)))
    assert results[0]==results[1], 'Native business outputs differ from upstream'
    report={'status':'passed','upstream':'907cfa8','business_outputs_equal':True,'verified':['initial explicit ranking','behavioral fusion and suppressed features','immutable replay','duplicate feedback no double counting','tighter requirements','SQLite restart recovery','fresh final gate'],'ignored_nondeterministic_fields':['observed_at','duration_ms','trace_id','trace_ref','state_id','candidate_pool_ref'],'responses':results[1]['responses']}
    report['legacy_root']=str(Path(args.legacy_root).resolve())
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='responses'}))


if __name__=='__main__':main()
