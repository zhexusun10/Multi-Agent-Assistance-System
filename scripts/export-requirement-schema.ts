import {z} from 'zod';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {modelIntakeSchema,intakeAuditSchema} from '../src/agents/requirement/contracts.js';
const schema=JSON.stringify(z.toJSONSchema(modelIntakeSchema,{target:'draft-2020-12'}),null,2)+'\n';
const path='contracts/requirements/intake-v2.schema.json';
if(process.argv.includes('--check')){if(await readFile(path,'utf8')!==schema)throw Error('Requirement JSON Schema drift');console.log('Requirement JSON Schema is current');}
else{await mkdir('contracts/requirements',{recursive:true});await writeFile(path,schema);console.log(path);}
const auditPath='contracts/requirements/intake-audit-v2.schema.json',audit=JSON.stringify(z.toJSONSchema(intakeAuditSchema,{target:'draft-2020-12'}),null,2)+'\n';
if(process.argv.includes('--check')){if(await readFile(auditPath,'utf8')!==audit)throw Error('Requirement audit JSON Schema drift');}
else await writeFile(auditPath,audit);
