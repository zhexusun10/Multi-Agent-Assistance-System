import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import {parseEnv} from 'node:util';
import {extractRequirements,mergeRequirements,criterion} from '../src/domain/requirements.js';
import {createModel} from '../src/model.js';
import {LocationService} from '../src/adapters/locations.js';
import {DataHttpAdapter} from '../src/adapters/data-http.js';
import {ToolExecutor} from '../src/tools/executor.js';
import {rentalCases,type Slot} from './rental-cases.js';
const mode=process.argv.includes('--model')?'model':'rules',tag=process.argv.find(v=>v.startsWith('--tag='))?.slice(6)||'latest';
const selectedIds=process.argv.find(v=>v.startsWith('--ids='))?.slice(6).split(',');
const cases=selectedIds?rentalCases.filter(c=>selectedIds.includes(c.id)):rentalCases;
if(!cases.length||selectedIds&&cases.length!==new Set(selectedIds).size)throw Error('Unknown or empty evaluation case selection');
// Match server.ts: the user's project provider config wins over an inherited host key.
const config=parseEnv(await readFile('.env','utf8'));for(const k of ['CHAT_MODEL','OPENAI_API_KEY','OPENAI_BASE_URL'])if(Object.hasOwn(config,k))process.env[k]=config[k];
const model=mode==='model'?await createModel():undefined,skill=await readFile('skills/rental-requirement-intake/SKILL.md','utf8');
const tools=new ToolExecutor(),locations=new LocationService(new DataHttpAdapter('http://127.0.0.1:8089',tools,async()=>randomUUID()),tools,async()=>randomUUID());
const toCriterion=(s:Slot)=>criterion(s.field,s.operator as any,s.value,s.unit,s.strength==='soft',{...(s.target?{target:{name:s.target}}:{}),...(s.distance_type?{distance_type:s.distance_type as any}:{}),...(s.transport_mode?{transport_mode:s.transport_mode}:{})});
const clean=(s:Slot)=>JSON.stringify(Object.fromEntries(Object.entries(s).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>[k,typeof v==='number'?Math.round(v*100)/100:Array.isArray(v)?v.map(x=>typeof x==='number'?Math.round(x*100)/100:x).sort((a,b)=>String(a).localeCompare(String(b))):v])));
const results:any[]=[];let next=0;
async function worker(){while(next<cases.length){const row=cases[next++],started=Date.now();try{
 const base=mergeRequirements({listing_type:'RENT',criteria:[]},{upsert:(row.base||[]).map(toCriterion),remove_fields:[],clarifications:[],sources:[],supported:true});
 const draft=await extractRequirements(row.query,base,model,skill),actual:Slot[]=draft.upsert.map(c=>({field:c.field,operator:c.operator,value:c.value,strength:c.strength,...(c.unit?{unit:c.unit}:{}),...(c.target?{target:c.target.place_id||c.target.category||c.target.name}:{}),...(c.distance_type?{distance_type:c.distance_type}:{}),...(c.transport_mode?{transport_mode:c.transport_mode}:{})}));
 for(const l of draft.location_intents||[]){const found=await locations.resolve({sessionId:randomUUID(),operationId:randomUUID(),agent:'a',dataset:'evaluation',deadline:Date.now()+30000,calls:0},l.name,l.explicit_station);actual.push({field:'@location',operator:l.radius_m?'lte':'near',value:l.radius_m??null,strength:l.strength,target:found.entity?.entity_id||'UNRESOLVED',unit:'m',distance_type:l.distance_type});}
 const expected=new Set(row.expected.map(clean)),predicted=new Set(actual.map(clean)),tp=[...expected].filter(x=>predicted.has(x)).length,missing=[...expected].filter(x=>!predicted.has(x)),extra=[...predicted].filter(x=>!expected.has(x));
 const clarifyCorrect=!!draft.clarifications.length===!!row.clarify,supportedCorrect=draft.supported===(row.supported??true),removalCorrect=JSON.stringify([...draft.remove_fields].sort())===JSON.stringify([...(row.remove||[])].sort());
 results.push({id:row.id,split:row.split,group:row.group,query:row.query,base_slots:row.base||[],expected_slots:row.expected,actual_slots:actual,expected_remove_fields:row.remove||[],passed:!missing.length&&!extra.length&&clarifyCorrect&&supportedCorrect&&removalCorrect,tp,expected:expected.size,predicted:predicted.size,missing,extra,clarifyCorrect,supportedCorrect,removalCorrect,clarifications:draft.clarifications,remove_fields:draft.remove_fields,remove_criterion_ids:draft.remove_criterion_ids||[],latency_ms:Date.now()-started});
 }catch(e){results.push({id:row.id,split:row.split,group:row.group,query:row.query,passed:false,tp:0,expected:row.expected.length,predicted:0,exception:e instanceof Error?e.message:String(e),latency_ms:Date.now()-started});}
 if(results.length%20===0)console.log(`${mode} ${tag}: ${results.length}/${cases.length}`);
}}
await Promise.all(Array.from({length:mode==='model'?4:1},worker));results.sort((a,b)=>a.id.localeCompare(b.id));
function metrics(rows:any[]){const tp=rows.reduce((n,r)=>n+r.tp,0),expected=rows.reduce((n,r)=>n+r.expected,0),predicted=rows.reduce((n,r)=>n+r.predicted,0),precision=predicted?tp/predicted:0,recall=expected?tp/expected:0;return {cases:rows.length,exact_pass:rows.filter(r=>r.passed).length,exact_accuracy:rows.filter(r=>r.passed).length/rows.length,slot_precision:precision,slot_recall:recall,slot_f1:precision+recall?2*precision*recall/(precision+recall):0,exceptions:rows.filter(r=>r.exception).length,clarification_accuracy:rows.filter(r=>r.clarifyCorrect).length/rows.length,removal_accuracy:rows.filter(r=>r.removalCorrect).length/rows.length,p95_latency_ms:rows.map(r=>r.latency_ms).sort((a,b)=>a-b)[Math.ceil(rows.length*.95)-1]};}
const report={checked_at:new Date().toISOString(),mode,tag,model:mode==='model'?process.env.CHAT_MODEL:null,corpus_sha256:createHash('sha256').update(await readFile('evaluation/rental-cases.ts')).digest('hex'),metrics:metrics(results),by_split:Object.fromEntries(['development','validation'].map(s=>[s,metrics(results.filter(r=>r.split===s))])),by_group:Object.fromEntries([...new Set(results.map(r=>r.group))].map(g=>[g,metrics(results.filter(r=>r.group===g))])),results};
await mkdir('evaluation/reports',{recursive:true});await writeFile(`evaluation/reports/intake-${tag}-${mode}.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report.metrics,null,2));
