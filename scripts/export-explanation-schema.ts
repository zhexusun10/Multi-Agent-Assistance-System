import {writeFile,readFile,mkdir} from 'node:fs/promises';
import {z} from 'zod';
import {explanationOutputSchema,explanationAuditSchema} from '../src/agents/explanation/contracts.js';
await mkdir('contracts/explanation',{recursive:true});
for(const [name,schema] of [['explanation-v2',explanationOutputSchema],['explanation-audit-v2',explanationAuditSchema]] as const){
  const path='contracts/explanation/'+name+'.schema.json',source=JSON.stringify(z.toJSONSchema(schema),null,2)+'\n';
  if(process.argv.includes('--check')){if(await readFile(path,'utf8')!==source)throw Error('Schema out of date: '+path);}else await writeFile(path,source);
}
console.log('Explanation JSON Schemas are current');
