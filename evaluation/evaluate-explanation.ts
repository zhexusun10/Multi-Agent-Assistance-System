import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {SystemMessage,HumanMessage,type BaseMessage} from '@langchain/core/messages';
import {z} from 'zod';
import {createModel} from '../src/model.js';
import {generateExplanation} from '../src/agents/explanation/generator.js';
import {buildExplanation} from '../src/domain/explanation.js';
import {explanationCases,explanationFixture} from './explanation-cases.js';
import {modelTestPlan,budgetTestModel} from './model-test-budget.js';
const arg=(k:string,d:string)=>{const i=process.argv.indexOf(k);return i<0?d:process.argv[i+1];},tag=arg('--tag','cost-controlled-smoke'),split=arg('--split','development'),ids=arg('--ids','').split(',').filter(Boolean);
const plan=modelTestPlan(explanationCases.filter(c=>(split==='all'||c.split===split)&&(!ids.length||ids.includes(c.id))),process.argv);
const cases=plan.cases;
console.log(JSON.stringify({mode:plan.live?'paid-targeted-test':'dry-run-no-api-calls',case_ids:cases.map(c=>c.id),limits:plan.limits,independent_model_judge:plan.modelJudge}));
if(!plan.live)process.exit(0);
const sources=['src/agents/explanation/contracts.ts','src/agents/explanation/evidence.ts','src/agents/explanation/prompt.ts','src/agents/explanation/few-shot.ts','src/agents/explanation/validation.ts','src/agents/explanation/generator.ts','skills/evidence-based-explanation/SKILL.md','evaluation/explanation-cases.ts','evaluation/evaluate-explanation.ts','evaluation/model-test-budget.ts'];
const hashes=Object.fromEntries(await Promise.all(sources.map(async p=>[p,createHash('sha256').update(await readFile(p)).digest('hex')])));
const budget=budgetTestModel(await createModel(undefined,{maxTokens:plan.limits.maxOutputTokens,temperature:0,maxRetries:0})),model=budget.model,skill=await readFile('skills/evidence-based-explanation/SKILL.md','utf8');
const judgeSchema=z.object({grounded:z.boolean(),ranking_correct:z.boolean(),covers_question:z.boolean(),natural:z.boolean(),concise:z.boolean(),note:z.string().max(1000)}).strict();
const judge=model.withStructuredOutput(judgeSchema,{name:'evaluate_rental_explanation',method:'functionCalling',strict:true});
const judgeRepairs:{id:string;error:string}[]=[];
async function grade(messages:BaseMessage[],id:string){
  try{return judgeSchema.parse(await judge.invoke(messages,{signal:AbortSignal.timeout(30000)}));}
  catch(e){
    const error=e instanceof Error?e.message:String(e);if(!/parse|unrecognized_keys|schema|JSON/i.test(error))throw e;
    judgeRepairs.push({id,error});
    return judgeSchema.parse(await judge.invoke([...messages,new SystemMessage('上次验收JSON未通过严格结构校验。保持同一金标准重新独立判断，只返回已定义的六个键，不增加备注字段：'+error.slice(0,1200))],{signal:AbortSignal.timeout(30000)}));
  }
}
const results:any[]=[];let next=0,externalBlock:string|undefined;
async function worker(){while(next<cases.length&&!externalBlock){const c=cases[next++],start=Date.now(),f=explanationFixture(c.scenario);try{
  const result=await generateExplanation(f.req,f.rec,'synthetic',c.query,model,skill);
  const grading=plan.modelJudge?await grade([new SystemMessage('你是独立验收员，只按给定金标准与房源事实评估解释。grounded为不虚构事实/未知满足/步行路线；ranking_correct为原因符合金标准，不混淆筛选排序；covers_question为回答整体/某套/比较且没有漏掉关键未知。泛问整体为什么推荐时，应分项列需求、具体符合情况、缺资料项，再用短排序与注意说明；仅问价格/某套/比较或要求简短不逐条时允许短回答，不强制完整模板。natural为普通用户能懂、指代清楚，可使用纯文本标签与项目符号，无内部字段分数工程日志，不用待确认选项等生硬词。concise为英文不超过250词、中文不超过850字符且不重复；分项需求及对应符合情况是有用内容，不算冗余，排序资料不足只需1句。未知不等于没有/未满足；软偏好未知不否定必须条件。空结果无需availability。允许四舍五入，勿强求逐套报数。返回五个布尔和真实失败原因。'),new HumanMessage(JSON.stringify({question:c.query,gold:c.gold,prices_are_recorded_not_confirmed_today:true,current_availability:'unknown',listings:[...f.rec.recommendations,...f.rec.unverified_candidates].map(l=>({title:l.listing.title,price:l.listing.price,bedrooms:l.listing.bedrooms===0?null:l.listing.bedrooms,area:l.listing.floor_area_sqft,position:l.rank,checks:l.criterion_results.map(r=>({criterion:f.req.criteria.find(x=>x.criterion_id===r.criterion_id),status:r.status,value:r.observed_value}))})),answer:result.summary}))],c.id):null;
  const passed=grading===null||(grading.grounded&&grading.ranking_correct&&grading.covers_question&&grading.natural&&grading.concise);
  results.push({...c,passed,grading,answer:result.summary,generation:result.generation,characters:result.summary.length,legacy_diagnostic_removed:!buildExplanation(f.req,f.rec,'synthetic',c.query).summary.includes('原生融合匹配分'),latency_ms:Date.now()-start});
}catch(e){const error=e instanceof Error?e.message:String(e);if(/402|Insufficient Balance/i.test(error))externalBlock='Provider returned 402 Insufficient Balance; stopped further model calls';if(/MODEL_TEST_(?:REQUEST|CONTEXT)_LIMIT/.test(error))externalBlock='Local model test budget reached; stopped further calls';results.push({...c,passed:false,error,diagnostic:(e as any)?.diagnostic,latency_ms:Date.now()-start});}
if(results.length%12===0)console.log(tag+' '+results.length+'/'+cases.length);}}
await worker();results.sort((a,b)=>a.id.localeCompare(b.id));
for(const [path,sha] of Object.entries(hashes))if(createHash('sha256').update(await readFile(path)).digest('hex')!==sha)throw Error('Source changed during evaluation: '+path);
const metric=(r:any[])=>({cases:r.length,passed:r.filter(x=>x.passed).length,grounded:r.filter(x=>x.grading?.grounded).length,ranking_correct:r.filter(x=>x.grading?.ranking_correct).length,covers_question:r.filter(x=>x.grading?.covers_question).length,natural:r.filter(x=>x.grading?.natural).length,concise:r.filter(x=>x.grading?.concise).length,exceptions:r.filter(x=>x.error).length,repaired:r.filter(x=>x.generation?.attempts>1).length,average_characters:r.reduce((n,x)=>n+(x.characters||0),0)/(r.length||1)});
const report={tag,split,test_budget:{...plan.limits,requests_used:budget.requests()},verification:plan.modelJudge?'production-validation-audit-and-independent-judge':'production-validation-and-audit-only',independent_model_judge:plan.modelJudge,checked_at:new Date().toISOString(),model:process.env.CHAT_MODEL,hashes,judge_repairs:judgeRepairs,external_block:externalBlock??null,planned_cases:cases.length,unattempted:cases.filter(c=>!results.some(r=>r.id===c.id)).map(c=>c.id),metrics:metric(results),by_scenario:Object.fromEntries([...new Set(results.map(r=>r.scenario))].map(s=>[s,metric(results.filter(r=>r.scenario===s))])),results};
await writeFile('evaluation/reports/explanation-'+tag+'.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report.metrics));if(externalBlock||results.some(r=>!r.passed))process.exitCode=1;
