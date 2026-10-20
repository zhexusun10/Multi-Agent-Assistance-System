import {writeFile,mkdir} from 'node:fs/promises';
import {INTENT_TEMPLATES} from '../src/agents/orchestrator/intent-templates.js';
import {TEMPLATE_VERSION} from '../src/agents/orchestrator/intent-recognizer.js';
await mkdir('data/.runtime',{recursive:true});
await writeFile('data/.runtime/intent-templates.json',JSON.stringify({version:TEMPLATE_VERSION,templates:INTENT_TEMPLATES},null,2));
console.log(JSON.stringify({intents:Object.keys(INTENT_TEMPLATES).length,templates:Object.values(INTENT_TEMPLATES).flat().length,version:TEMPLATE_VERSION}));
