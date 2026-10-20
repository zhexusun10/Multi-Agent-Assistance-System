import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {parseEnv} from 'node:util';
import {createModel} from '../src/model.js';
import {IntentRecognizer} from '../src/agents/orchestrator/intent-recognizer.js';
import {planIntent} from '../src/agents/orchestrator/agent-orchestrator.js';
import {INTENT_PARAMETERS} from '../src/agents/orchestrator/intent-templates.js';
import {INTENT_CASES} from '../evaluation/intent-corpus.js';
test('EchoMind three-channel intent gate: actual DeepSeek + Docker Chroma vectors + pattern',{skip:process.env.INTENT_LIVE!=='1',timeout:240000},async()=>{
 const config=parseEnv(await readFile('.env','utf8'));for(const key of ['OPENAI_API_KEY','OPENAI_BASE_URL','CHAT_MODEL'])if(config[key])process.env[key]=config[key];
 const model=await createModel(undefined,{temperature:INTENT_PARAMETERS.temperature,maxTokens:INTENT_PARAMETERS.max_tokens}),recognizer=new IntentRecognizer(model),rows:any[]=[];
 for(let i=0;i<INTENT_CASES.length;i+=3){await Promise.all(INTENT_CASES.slice(i,i+3).map(async([query,group,kind])=>{const r=await recognizer.recognize(query,[{role:'user',text:'之前已推荐了五套房源，第一套Gold Coast Condo，月租1600，要求独立卫浴。'}],{has_recommendation:true,requirements_version:1,source_request_id:'test-version',requirements:{budget:2000}});rows.push({query,expected_group:group,expected_kind:kind,actual_kind:planIntent(r).kind,correct:r.intent_group===group&&planIntent(r).kind===kind,result:r});}));}
 const accuracy=rows.filter(r=>r.correct).length/rows.length,groups=[...new Set(rows.map(r=>r.expected_group))],macroF1=groups.reduce((sum,g)=>{const tp=rows.filter(r=>r.expected_group===g&&r.result.intent_group===g).length,fp=rows.filter(r=>r.expected_group!==g&&r.result.intent_group===g).length,fn=rows.filter(r=>r.expected_group===g&&r.result.intent_group!==g).length;return sum+(2*tp/(2*tp+fp+fn)||0);},0)/groups.length;
 const report={checked_at:new Date().toISOString(),status:accuracy>=.95?'passed':'failed',cases:rows.length,accuracy,macro_f1:macroF1,model_success:rows.filter(r=>!r.result.channels.llm.failed).length,vector_success:rows.filter(r=>!r.result.channels.embedding.failed).length,parameters:INTENT_PARAMETERS,rows};await writeFile('evaluation/reports/orchestrator-intent-live.json',JSON.stringify(report,null,2));
 console.log(JSON.stringify({cases:rows.length,accuracy,macroF1,model_success:report.model_success,vector_success:report.vector_success,failures:rows.filter(r=>!r.correct).map(r=>({query:r.query,intent:r.result.intent,actual:r.actual_kind,expected:r.expected_kind,channels:r.result.channels}))}));
 assert.equal(report.model_success,rows.length);assert.equal(report.vector_success,rows.length);assert(accuracy>=.95,'intent business routing accuracy below 95%');
});
