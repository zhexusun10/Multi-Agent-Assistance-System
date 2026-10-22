import {readFile,writeFile} from 'node:fs/promises';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {createModel} from '../src/model.js';
import {extractRequirements} from '../src/domain/requirements.js';

const [fixture]=JSON.parse(await readFile('evaluation/reports/requirement-update-failure-before.json','utf8'));
const trace:unknown[]=[],model=await createModel();
const observed={withStructuredOutput:(schema:any,options:any)=>{
  const runner=model.withStructuredOutput(schema,options);
  return {invoke:async(messages:any,config:any)=>{
    const output=await runner.invoke(messages,config);
    trace.push({name:options.name,output});return output;
  }};
}} as unknown as BaseChatModel;
const started=Date.now();let draft:unknown,error:string|undefined;
try{draft=await extractRequirements(fixture.query,fixture.input.requirements,observed,await readFile('skills/rental-requirement-intake/SKILL.md','utf8'));}
catch(e){error=e instanceof Error?e.message:String(e);}
const tag=process.argv[2]||'before';
await writeFile(`evaluation/reports/requirement-update-replay-${tag}.json`,JSON.stringify({query:fixture.query,base:fixture.input.requirements,trace,draft,error,duration_ms:Date.now()-started},null,2));
console.log(JSON.stringify({tag,passed:!error,error,calls:trace.length}));
