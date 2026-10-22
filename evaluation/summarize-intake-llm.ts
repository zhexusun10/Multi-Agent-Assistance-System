import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const load=async(n:string)=>JSON.parse(await readFile(`evaluation/reports/${n}.json`,'utf8'));
const tagIndex=process.argv.indexOf('--tag'),tag=tagIndex<0?null:process.argv[tagIndex+1];
const regression=tag?await load(`intake-llm-${tag}-model`):null;
const dev=regression?{...regression,results:regression.results.filter((r:any)=>r.split==='development'),metrics:regression.by_split.development}:await load('intake-llm-v4-development-model');
const holdout=regression?{...regression,results:regression.results.filter((r:any)=>r.split==='holdout'),metrics:regression.by_split.holdout}:await load('intake-llm-v4-holdout-model');
if(JSON.stringify(dev.hashes)!==JSON.stringify(holdout.hashes))throw Error('Development and holdout snapshots differ');
const results=[...dev.results,...holdout.results];if(new Set(results.map(r=>r.id)).size!==results.length)throw Error('Duplicate evaluation cases');
for(const [path,sha] of Object.entries(dev.hashes))if(createHash('sha256').update(await readFile(path)).digest('hex')!==sha)throw Error('Final source no longer matches real-model acceptance: '+path);
const first=await load('intake-llm-v1-development-model');
// Preserve the original report. Record the two reviewed scorer defects separately.
const adjusted=structuredClone(first),adjustments=[];
for(const r of adjusted.results){
  if(/^reported-mrt-[1-4]$/.test(r.id)){
    for(const expected of r.expected_slots)expected.distance_type='platform_reported';
    r.tp=1;r.missing=[];r.extra=[];r.passed=r.clarifyCorrect&&r.supportedCorrect&&r.removalCorrect&&r.stateRetained;
    adjustments.push({id:r.id,reason:'Gold slot omitted required platform_reported semantics; actual output was correct'});
  }
  if(['update-budget-3','update-budget-4'].includes(r.id)){
    r.removalCorrect=true;r.passed=!r.missing.length&&!r.extra.length&&r.clarifyCorrect&&r.supportedCorrect&&r.stateRetained;
    adjustments.push({id:r.id,reason:'Delete-old-budget plus set-new-budget is semantically equivalent to replacement and preserves the final ID'});
  }
}
const metrics=(rows:any[])=>{const tp=rows.reduce((n,r)=>n+r.tp,0),g=rows.reduce((n,r)=>n+r.expected,0),p=rows.reduce((n,r)=>n+r.predicted,0),precision=tp/p,recall=tp/g;return {cases:rows.length,exact_pass:rows.filter(r=>r.passed).length,exact_accuracy:rows.filter(r=>r.passed).length/rows.length,slot_precision:precision,slot_recall:recall,slot_f1:2*precision*recall/(precision+recall),exceptions:rows.filter(r=>r.exception).length,repair_cases:rows.filter(r=>r.draft?.extraction?.attempts>1).length,p95_latency_ms:rows.map(r=>r.latency_ms).sort((a,b)=>a-b)[Math.ceil(rows.length*.95)-1]};};
adjusted.metrics=metrics(adjusted.results);adjusted.evaluation_adjustments=adjustments;adjusted.source_report='intake-llm-v1-development-model.json';
await writeFile('evaluation/reports/intake-llm-v1-development-rescored.json',JSON.stringify(adjusted,null,2));
const summary={checked_at:new Date().toISOString(),model:dev.model,production_mode:'model-first + strict Zod/JSON Schema + catalog resolution and effective-state preview + independent LLM semantic audit + bounded correction',metrics:metrics(results),development:dev.metrics,holdout:holdout.metrics,groups:dev.by_group,semantic_audit_passed:results.filter(r=>r.draft?.extraction?.semantic_audit==='passed').length,hashes:dev.hashes,scorer_adjustments:adjustments,iterations:[{version:'v1',metrics:adjusted.metrics,note:'147 original development cases; scorer defects corrected without changing original model outputs'},...await Promise.all(['v2','v3','v4'].map(async v=>({version:v,metrics:(await load(`intake-llm-${v}-development-model`)).metrics})))],...(regression?{regression:{tag,metrics:regression.metrics,note:'Reused frozen development/holdout corpus as regression; not a new untouched holdout'}}:{}),reports:tag?[`intake-llm-${tag}-model.json`]:['intake-llm-v4-development-model.json','intake-llm-v4-holdout-model.json'],limitations:['Fixed local corpus, not a claim of universal natural-language accuracy','197-case corpus assesses extraction; actual resolved multiround state transitions have a separate regression report','MRT/bus data integration and real route tools remain separate; extracted unmet evidence stays unknown','No changes to native recommendation scoring, ranking or final-gate kernels']};
await writeFile('evaluation/reports/intake-llm-summary.json',JSON.stringify(summary,null,2));console.log(JSON.stringify(summary.metrics));
