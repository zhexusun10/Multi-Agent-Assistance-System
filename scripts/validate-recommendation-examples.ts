import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {z} from 'zod';
import {
  recommendationRequestSchema,recommendationResponseSchema,validateResponse,criterionSchema,
} from '../src/contracts/recommendation-v1.js';

const read=async(path:string)=>JSON.parse(await readFile(path,'utf8'));
const folder='contracts/examples/current-v1/';
const request=recommendationRequestSchema.parse(await read(folder+'request.standard.json'));
const privateRequest=recommendationRequestSchema.parse(await read(folder+'request.private-bathroom.json'));
const feedbackRequest=recommendationRequestSchema.parse(await read(folder+'request.feedback.json'));
const conflictRequest=recommendationRequestSchema.parse(await read(folder+'request.conflict.json'));
const success=validateResponse(await read(folder+'response.success.json'),request);
const unverified=validateResponse(await read(folder+'response.unverified.json'),privateRequest);
const failed=validateResponse(await read(folder+'response.error.json'),request);
const clarified=validateResponse(await read(folder+'response.needs-clarification.json'),conflictRequest);
assert.equal(success.status,'success');assert.equal(success.recommendations.length,1);
assert.equal(unverified.status,'no_match');assert.equal(unverified.recommendations.length,0);
assert.equal(unverified.unverified_candidates.length,1);
assert.equal(unverified.unverified_candidates[0].criterion_results.at(-1)?.status,'unknown');
assert.equal(failed.status,'error');assert(Object.values(failed.state.counts).every(x=>x===null));
assert.equal(clarified.status,'needs_clarification');assert(clarified.clarifications.length>0);
assert(Object.values(clarified.state.counts).every(x=>x===null));
assert.equal(privateRequest.requirements_version,request.requirements_version+1);
assert.equal(privateRequest.previous_state_id,success.state.state_id);
assert.equal(feedbackRequest.requirements_version,request.requirements_version);
assert.notEqual(feedbackRequest.request_id,request.request_id);assert.equal(feedbackRequest.feedback.length,4);

// Check exported contracts are still the schemas this implementation actually uses.
const requestJson=z.toJSONSchema(recommendationRequestSchema,{unrepresentable:'any'});
const responseJson=z.toJSONSchema(recommendationResponseSchema,{unrepresentable:'any'});
assert.deepEqual(await read('contracts/releases/recommendation-request-1.0.schema.json'),requestJson);
assert.deepEqual(await read('contracts/releases/recommendation-response-1.0.schema.json'),responseJson);
function paths(node:any,prefix:string):string[]{
  const result:string[]=[];
  for(const [key,value] of Object.entries(node.properties||{}) as [string,any][]){
    const path=prefix+'.'+key;result.push(path);result.push(...paths(value,path));
  }
  if(node.items)result.push(...paths(node.items,prefix+'[]'));
  for(const branch of node.anyOf||[])result.push(...paths(branch,prefix));
  return [...new Set(result)];
}
const schemaPaths=[...paths(requestJson,'request'),...paths(responseJson,'response')];
const fieldIndex=await read(folder+'field-index.json');
assert.deepEqual(Object.keys(fieldIndex.field_index).sort(),schemaPaths.sort(),'Documentation must cover every actual property, including shared candidate/target structures');

// Every JSON code block is syntactically valid. Criterion fragments also use
// real typed values and valid hard/soft/unit combinations.
const document=await readFile('docs/Recommendation_Agent_双向接口与V1差异.md','utf8');
let jsonBlocks=0,criterionFragments=0;
for(const match of document.matchAll(/```json\s*\n([\s\S]*?)\n```/g)){
  const value=JSON.parse(match[1]);jsonBlocks++;
  const check=(v:any)=>{if(v&&typeof v==='object'&&'criterion_id' in v&&'field' in v&&'strength' in v){criterionSchema.parse(v);criterionFragments++;}};
  if(Array.isArray(value))value.forEach(check);else check(value);
}
const report={status:'passed',checked_at:new Date().toISOString(),request_examples:4,response_examples:4,
  schema_properties_documented:schemaPaths.length,json_blocks:jsonBlocks,criterion_fragments:criterionFragments,
  runtime_code_modified:false,synthetic_examples:true};
await writeFile('evaluation/reports/recommendation-handoff-document.json',JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report));
