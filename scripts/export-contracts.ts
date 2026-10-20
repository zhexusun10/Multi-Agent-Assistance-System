import {z} from 'zod';
import {writeFile,mkdir} from 'node:fs/promises';
import {recommendationRequestSchema,recommendationResponseSchema,FIELD_RULES} from '../src/contracts/recommendation-v1.js';
await mkdir('contracts/releases',{recursive:true});
for(const [name,schema] of [['request',recommendationRequestSchema],['response',recommendationResponseSchema]] as const){await writeFile(`contracts/releases/recommendation-${name}-1.0.schema.json`,JSON.stringify(z.toJSONSchema(schema,{unrepresentable:'any'}),null,2)+'\n');}
await writeFile('contracts/releases/executable-field-rules.json',JSON.stringify(FIELD_RULES,null,2)+'\n');
console.log('Exported contract schemas. Cross-field/business invariants remain mandatory in validateResponse.');
