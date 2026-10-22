import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createModel} from '../src/model.js';
import {extractRequirements,extractRules,mergeRequirements} from '../src/domain/requirements.js';
import {intakeLlmCases,type GoldSlot} from './intake-llm-cases.js';
const get=(flag:string,fallback:string)=>{const i=process.argv.indexOf(flag);return i<0?fallback:process.argv[i+1];};
const tag=get('--tag','v1'),split=get('--split','all'),mode=get('--mode','model'),concurrency=Number(get('--concurrency','3'));
const selected=get('--ids','').split(',').filter(Boolean),limit=Number(get('--limit','0'));
let cases=intakeLlmCases.filter(c=>(split==='all'||c.split===split)&&(!selected.length||selected.includes(c.id)));if(limit)cases=cases.slice(0,limit);
const sources=['src/agents/requirement/contracts.ts','src/agents/requirement/extractor.ts','src/agents/requirement/audit-context.ts','src/agents/requirement/prompt.ts','src/agents/requirement/validation.ts','src/agents/requirement/few-shot.ts','skills/rental-requirement-intake/SKILL.md','evaluation/intake-llm-cases.ts'];
const hashes=Object.fromEntries(await Promise.all(sources.map(async f=>[f,createHash('sha256').update(await readFile(f)).digest('hex')])));
const model=mode==='model'?await createModel():undefined,skill=await readFile('skills/rental-requirement-intake/SKILL.md','utf8');
const knownTargets:Record<string,string>={'nus':'NUS','新加坡国立大学':'NUS','新国立大学':'NUS','nationaluniversityofsingapore':'NUS','ntu':'NTU','南洋理工大学':'NTU','南洋理工':'NTU','nanyangtechnologicaluniversity':'NTU','金文泰':'CLEMENTI','clementi':'CLEMENTI','clementimrt':'CLEMENTI_MRT','clementimrtstation':'CLEMENTI_MRT','金文泰地铁站':'CLEMENTI_MRT','金文泰地铁':'CLEMENTI_MRT','金文泰mrt站':'CLEMENTI_MRT','金文泰mrt':'CLEMENTI_MRT'};
const target=(s:string)=>knownTargets[s.toLowerCase().replace(/[\s_()（）.-]/g,'')]||s;
const equal=(a:unknown,b:unknown):boolean=>typeof a==='number'&&typeof b==='number'?Math.abs(a-b)<.02:Array.isArray(a)&&Array.isArray(b)?a.length===b.length&&[...a].sort().every((v,i)=>equal(v,[...b].sort()[i])):a===b;
const matches=(a:GoldSlot,b:GoldSlot)=>['field','operator','strength','unit','distance_type','transport_mode'].every(k=>(a as any)[k]===(b as any)[k])&&target(a.target||'')===target(b.target||'')&&equal(a.value,b.value)&&(b.weight===undefined||equal(a.weight,b.weight));
function slots(d:Awaited<ReturnType<typeof extractRequirements>>):GoldSlot[]{return [...d.upsert.map(c=>({field:c.field,operator:c.operator,value:c.value,strength:c.strength,...(c.unit?{unit:c.unit}:{}),...(c.target?{target:c.target.name||c.target.category}:{}),...(c.distance_type?{distance_type:c.distance_type}:{}),...(c.transport_mode?{transport_mode:c.transport_mode}:{}),...(c.weight?{weight:c.weight}:{})})),...(d.location_intents||[]).map(l=>({field:'@location',operator:l.operator||(l.radius_m!==undefined?'lte':'near'),value:l.range_m??l.radius_m??null,strength:l.strength,unit:'m',target:l.category||l.name,distance_type:l.distance_type,weight:l.weight??1}))];}
const results:any[]=[];let next=0;
async function worker(){while(next<cases.length){const row=cases[next++],start=Date.now();try{
  const base=row.base||{listing_type:'RENT' as const,criteria:[]};
  const d=mode==='model'?await extractRequirements(row.query,base,model,skill):extractRules(row.query,base);
  const baseSlots=slots({upsert:base.criteria,remove_fields:[],sources:[],clarifications:[],supported:true});
  const actual=slots(d).filter(a=>!baseSlots.some(b=>matches(a,b))||row.expected.some(b=>matches(a,b)));
  const used=new Set<number>(),missing:GoldSlot[]=[];let tp=0;
  for(const e of row.expected){const i=actual.findIndex((a,i)=>!used.has(i)&&matches(a,e));if(i>=0){used.add(i);tp++;}else missing.push(e);}
  const extra=actual.filter((_,i)=>!used.has(i));
  const clarifyCorrect=!!d.clarifications.length===!!row.clarify,supportedCorrect=d.supported===(row.supported??true);
  const merged=mergeRequirements(base,d);
  const actualRemoved=base.criteria.filter(c=>!merged.criteria.some(m=>m.criterion_id===c.criterion_id)).map(c=>c.criterion_id).sort();
  const expectedRemoved=[...new Set([...(row.remove_ids||[]),...base.criteria.filter(c=>row.remove_fields?.includes(c.field)).map(c=>c.criterion_id)])].sort();
  const removalCorrect=equal(actualRemoved,expectedRemoved);
  // Independent state-retention check: a budget edit must not erase other requirements.
  const stateRetained=base.criteria.filter(c=>!d.remove_fields.includes(c.field)&&!d.remove_criterion_ids?.includes(c.criterion_id)&&!d.upsert.some(a=>a.field===c.field&&(a.field!=='amenities'||a.value===c.value))).every(c=>merged.criteria.some(m=>m.criterion_id===c.criterion_id));
  results.push({id:row.id,group:row.group,split:row.split,query:row.query,passed:!missing.length&&!extra.length&&clarifyCorrect&&supportedCorrect&&removalCorrect&&stateRetained,tp,expected:row.expected.length,predicted:actual.length,expected_slots:row.expected,actual_slots:actual,missing,extra,clarifyCorrect,supportedCorrect,removalCorrect,stateRetained,draft:d,latency_ms:Date.now()-start});
}catch(e){results.push({id:row.id,group:row.group,split:row.split,query:row.query,passed:false,tp:0,expected:row.expected.length,predicted:0,exception:e instanceof Error?e.message:String(e),latency_ms:Date.now()-start});}
if(results.length%10===0)console.log(`${tag} ${mode} ${results.length}/${cases.length}`);
}}
await Promise.all(Array.from({length:mode==='model'?concurrency:1},worker));results.sort((a,b)=>a.id.localeCompare(b.id));
function metrics(rows:any[]){const tp=rows.reduce((n,r)=>n+r.tp,0),gold=rows.reduce((n,r)=>n+r.expected,0),pred=rows.reduce((n,r)=>n+r.predicted,0),p=pred?tp/pred:gold?0:1,r=gold?tp/gold:1;return {cases:rows.length,exact_pass:rows.filter(r=>r.passed).length,exact_accuracy:rows.length?rows.filter(r=>r.passed).length/rows.length:0,slot_precision:p,slot_recall:r,slot_f1:p+r?2*p*r/(p+r):0,exceptions:rows.filter(r=>r.exception).length,repair_cases:rows.filter(r=>r.draft?.extraction?.attempts>1).length,p95_latency_ms:rows.map(r=>r.latency_ms).sort((a,b)=>a-b)[Math.ceil(rows.length*.95)-1]};}
for(const [path,sha] of Object.entries(hashes))if(createHash('sha256').update(await readFile(path)).digest('hex')!==sha)throw Error('Evaluation source changed during the run: '+path);
const report={checked_at:new Date().toISOString(),mode,tag,split,model:mode==='model'?process.env.CHAT_MODEL:null,concurrency,hashes,metrics:metrics(results),by_group:Object.fromEntries([...new Set(results.map(r=>r.group))].map(g=>[g,metrics(results.filter(r=>r.group===g))])),by_split:Object.fromEntries(['development','holdout'].map(s=>[s,metrics(results.filter(r=>r.split===s))])),results};
await mkdir('evaluation/reports',{recursive:true});await writeFile(`evaluation/reports/intake-llm-${tag}-${mode}.json`,JSON.stringify(report,null,2));console.log(JSON.stringify(report.metrics));
if(results.some(r=>!r.passed))process.exitCode=1;
