/** Exactly one saved request; defaults to offline planning. Never changes user history. */
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {createModel} from '../src/model.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {explainBundle} from '../src/agents/explanation/generator.js';
import {budgetTestModel,MODEL_TEST_LIMITS} from '../evaluation/model-test-budget.js';
import {explanationOutputSchema} from '../src/agents/explanation/contracts.js';
import {explanationModelInput,translateExplanationRefs} from '../src/agents/explanation/model-input.js';
import {validateExplanation} from '../src/agents/explanation/validation.js';
const source=JSON.parse(await readFile('evaluation/inputs/explanation-update-ranking.json','utf8'));
const bundle=makeExplanationBundle(source.requirements,source.recommendation,source.query),before=JSON.stringify(source);
const skill=await readFile('skills/evidence-based-explanation/SKILL.md','utf8');
const paths=['src/agents/explanation/contracts.ts','src/agents/explanation/evidence.ts','src/agents/explanation/prompt.ts','src/agents/explanation/few-shot.ts','src/agents/explanation/generator.ts','src/agents/explanation/model-input.ts','skills/evidence-based-explanation/SKILL.md'];
const report:any={query:source.query,checked_at:new Date().toISOString(),limits:MODEL_TEST_LIMITS,live:process.argv.includes('--live'),requests:0,source_hashes:Object.fromEntries(await Promise.all(paths.map(async p=>[p,createHash('sha256').update(await readFile(p)).digest('hex')])))};
let budget:ReturnType<typeof budgetTestModel>|undefined;
try{
 const writerOnly=process.argv.includes('--writer-only'),previous=writerOnly?JSON.parse(await readFile('evaluation/reports/explanation-update-live.json','utf8')):null;
 if(writerOnly){assert.equal(previous.requests,2,'Writer-only continuation reserves the final 1 of 3 total calls');report.previous_requests=previous.requests;await writeFile('evaluation/reports/explanation-update-live-before-coverage.json',JSON.stringify(previous,null,2));}
 if(report.live)budget=budgetTestModel(await createModel(undefined,{maxTokens:MODEL_TEST_LIMITS.maxOutputTokens,temperature:0,maxRetries:0}),writerOnly?1:MODEL_TEST_LIMITS.requests);
 const sizes:number[]=[],inputs:any[][]=[];
 // Even dry-run executes real prompt packing, with a local mock result, to
 // prove the actual serialized writer/auditor inputs fit the context ceiling.
 const offline={withStructuredOutput:(_s:unknown,o:any)=>({invoke:async(messages:any[])=>{
  inputs.push(messages);
  sizes.push(JSON.stringify(messages).length);
  assert(sizes.at(-1)!<=MODEL_TEST_LIMITS.maxInputCharacters,'Context characters '+sizes.at(-1)+' exceed budget before any paid request');
  if(o.name==='audit_rental_explanation_v2')return {valid:true,issues:[]};
  const input=JSON.parse(messages.findLast((m:any)=>m._getType()==='human').content);
  return {version:'2.0',focus_listing_ids:[],paragraphs:[{text:'同时考虑学校与超市的距离，允许在两项偏好之间取舍。部分房源缺卧室资料，未进入本次排名。当前价格和是否仍可租需要联系确认。',support_refs:input.required_refs}]};
 }})} as any;
 const local=await explainBundle(bundle,offline,skill);report.serialized_prompt_characters=sizes;report.evidence_characters=local.metadata.evidence_characters;
 if(report.live&&writerOnly){
  const writer=budget!.model.withStructuredOutput(explanationOutputSchema,{name:'write_rental_explanation_v2',method:'functionCalling',strict:true});
  const raw=await writer.invoke(inputs[0]),packed=explanationModelInput(bundle),output=validateExplanation(translateExplanationRefs(raw,packed.toInternal),bundle);
  report.answer=output.paragraphs.map(p=>p.text).join('\n\n');report.output=output;report.requests=budget!.requests();report.total_requests=report.previous_requests+report.requests;
  report.verification='real writer + local schema/reference/number checks; independent model audit not rerun under cost cap';
 }else if(report.live){const result=await explainBundle(bundle,budget!.model,skill);report.answer=result.text;report.generation=result.metadata;report.requests=budget!.requests();assert.equal(result.metadata.semantic_audit,'passed');}
 assert.equal(JSON.stringify(source),before);report.status=report.live?'passed':'offline_passed_no_api_calls';
}catch(e){report.status='failed';report.error=e instanceof Error?e.message:String(e);report.diagnostic=(e as any).diagnostic;report.requests=budget?.requests()||0;process.exitCode=1;}
await writeFile('evaluation/reports/explanation-update-'+(report.live?'live':'offline')+'.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
