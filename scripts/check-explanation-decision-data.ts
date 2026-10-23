/** Read-only acceptance check: recorded recommendation + actual local SQL/KG
 * APIs. Deliberately imports no model factory and performs zero LLM requests. */
import {readFile,writeFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';
import {requirementsSchema,recommendationResponseSchema} from '../src/contracts/recommendation-v1.js';
import {ExplanationEnricher} from '../src/agents/explanation/enrichment.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {explanationModelInput} from '../src/agents/explanation/model-input.js';
import {ToolExecutor,type ToolContext} from '../src/tools/executor.js';
const input=JSON.parse(await readFile('evaluation/inputs/explanation-update-ranking.json','utf8'));
const req=requirementsSchema.parse(input.requirements),rec=recommendationResponseSchema.parse(input.recommendation),before=JSON.stringify({req,rec});
const paths:string[]=[],archives:unknown[]=[],traces:unknown[]=[];
const url=process.env.DATA_API_URL||'http://127.0.0.1:8089';
const reader={async request(path:string){paths.push(path);const r=await fetch(url+path,{signal:AbortSignal.timeout(12000)});if(!r.ok)throw Error(`DATA_HTTP_${r.status}`);return r.json();}};
const ctx:ToolContext={sessionId:randomUUID(),operationId:randomUUID(),agent:'c',dataset:rec.state.coverage.dataset_version,deadline:Date.now()+120000,calls:0};
const service=new ExplanationEnricher(reader,new ToolExecutor(async trace=>{traces.push(trace);}),async(_ctx,body)=>{archives.push(body);return `local-read-check:${archives.length}`;});
const enrichment=await service.load(rec,ctx),bundle=addDecisionSupport(makeExplanationBundle(req,rec,'这些房子各有什么值得考虑的地方？'),req,rec,enrichment),packed=explanationModelInput(bundle);
assert.equal(JSON.stringify({req,rec}),before);
assert.equal(paths.length,bundle.listings.length*2);
assert(paths.every(p=>!p.includes('search')&&!p.includes('images')));
assert(enrichment.every(e=>e.sections.details==='available'&&e.sections.nearby==='available'));
assert(bundle.decision_support!.profiles.every(p=>p.additional_refs.length>0));
const report={status:'passed',checked_at:new Date().toISOString(),model_calls:0,source:'recorded NUS recommendation; current local SQL detail and Neo4j context',
  recommendation_unchanged:true,read_only_requests:paths.length,tool_traces:traces.length,source_archives:archives.length,
  prompt_characters:packed.prompt_characters,original_bundle_characters:packed.source_characters,
  listings:bundle.decision_support!.profiles.map(p=>({listing_id:p.listing_id,title:p.title,position:p.position,details:enrichment.find(e=>e.listing_id===p.listing_id)!.sections,
    additional_fields:p.additional_refs.map(r=>r.split(':').slice(2).join(':')),highlights:p.highlight_refs,important_cautions:p.caution_refs})),
  limits:['No paid model generation or semantic audit was run.','No actual bus/MRT route or travel time was verified.','Supplementary fields never upgrade recommendation criterion outcomes.']};
await writeFile('evaluation/reports/explanation-decision-support-data.json',JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
