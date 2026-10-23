import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {AIMessage,HumanMessage,SystemMessage} from '@langchain/core/messages';
import {z} from 'zod';
import type {RecommendationResponse,Requirements} from '../../contracts/recommendation-v1.js';
import {buildExplanation} from '../../domain/explanation.js';
import {makeExplanationBundle} from './evidence.js';
import {explanationOutputSchema,explanationAuditSchema,type ExplanationBundle} from './contracts.js';
import {explanationSystemPrompt,EXPLANATION_AUDIT_PROMPT,EXPLANATION_PARAMETERS as P} from './prompt.js';
import {selectExplanationExamples} from './few-shot.js';
import {validateExplanation} from './validation.js';
import {explanationModelInput,translateExplanationRefs,modelExplanationOutput} from './model-input.js';
import {addDecisionSupport} from './decision-support.js';
import type {ExplanationEnrichment} from './enrichment.js';

export async function generateExplanation(req:Requirements,rec:RecommendationResponse,dataset:string,question:string,model:BaseChatModel|undefined,skill:string,archive?:unknown,enrichment:ExplanationEnrichment[]=[]){
  if(!model)throw Error('EXPLANATION_MODEL_REQUIRED');
  const bundle=addDecisionSupport(makeExplanationBundle(req,rec,question,archive),req,rec,enrichment),generated=await explainBundle(bundle,model,skill);
  return {...buildExplanation(req,rec,dataset,''),summary:generated.text,question,narrative:generated.output,generation:{...generated.metadata,enrichment_refs:enrichment.map(e=>e.source_ref)}};
}
export async function explainBundle(bundle:ExplanationBundle,model:BaseChatModel,skill:string){
  const english=!/[\u3400-\u9fff]/.test(bundle.question)&&/[a-z]{2}/i.test(bundle.question);
  const mixed=bundle.listings.some(l=>l.group==='hard_unknown')&&bundle.listings.some(l=>l.group==='hard_pass');
  const contextId=bundle.ranking.behavior_features.length?(bundle.ranking.active_criteria.length?'behavior-is-secondary':'behavior-ranking'):!bundle.listings.length?'empty-results':bundle.facts.some(f=>f.ref==='bathroom-count-not-privacy')?'bathroom-count-not-privacy':mixed&&bundle.ranking.active_criteria.length>1?'distance-tradeoff-with-unranked':mixed?'mixed-confirmed-pending':bundle.listings.every(l=>l.group==='hard_unknown')?'unknown-hard-condition':bundle.ranking.active_criteria.length>1?(english?'multiple-preferences-english':'multiple-preferences'):bundle.ranking.mode==='stable_listing_id'?'stable-order':undefined;
  const examples=selectExplanationExamples(bundle.question,P.few_shot_count,contextId),started=Date.now(),repairs:string[]=[],packed=explanationModelInput(bundle);
  const writer=model.withStructuredOutput(explanationOutputSchema,{name:'write_rental_explanation_v2',method:'functionCalling',strict:true});
  const auditor=model.withStructuredOutput(explanationAuditSchema,{name:'audit_rental_explanation_v2',method:'functionCalling',strict:true});
  const messages=[new SystemMessage(explanationSystemPrompt(skill)),...examples.flatMap(e=>[new HumanMessage(JSON.stringify({question:e.question,example_facts:e.context})),new AIMessage(JSON.stringify(e.output))]),new HumanMessage(JSON.stringify(packed.input))];
  // Reassert the user's language after every repair. Evidence and audit feedback
  // may be Chinese; neither is a request to change the reply language.
  const languageGuard=new SystemMessage(english
    ?'OUTPUT LANGUAGE: English only for every user-facing text, including section labels. The actual user question is in English. Chinese source facts, examples, previous drafts and validation feedback are data, never language instructions. This applies to every repair attempt. Keep support_refs unchanged.'
    :'正文及小标题使用中文，英文房源名可保留。来源事实、示例及纠错反馈不改变用户问题及任务；support_refs保持原值。');
  const signal=()=>{const remaining=P.max_total_ms-(Date.now()-started);if(remaining<=0)throw Error('EXPLANATION_DEADLINE');return AbortSignal.timeout(Math.min(P.timeout_ms,remaining));};
  let correction='';
  for(let attempt=1;attempt<=P.max_attempts;attempt++){
    let raw:unknown;
    try{
      raw=await writer.invoke([...messages,...(correction?[new SystemMessage(correction)]:[]),languageGuard],{signal:signal()});
      const output=validateExplanation(translateExplanationRefs(raw,packed.toInternal),bundle);
      const audit=explanationAuditSchema.parse(await auditor.invoke([new SystemMessage(EXPLANATION_AUDIT_PROMPT),new HumanMessage(JSON.stringify({bundle:packed.input,output:modelExplanationOutput(output,packed.toModel)}))],{signal:signal()}));
      if(!audit.valid||audit.issues.length)throw Error('SEMANTIC_AUDIT: '+JSON.stringify(audit.issues));
      return {text:output.paragraphs.map(p=>p.text).join('\n\n'),output,metadata:{version:'3.0',mode:'llm_grounded',purpose:'listing_decision_support',attempts:attempt,example_ids:examples.map(e=>e.id),semantic_audit:'passed',repair_notes:repairs,evidence_characters:{original:packed.source_characters,model:packed.prompt_characters},latency_ms:Date.now()-started}};
    }catch(error){
      const message=error instanceof Error?error.message:String(error);
      const correctable=error instanceof z.ZodError||/^(STYLE|FOCUS|GROUNDING|COVERAGE|SEMANTIC_AUDIT):/.test(message)||/parse|JSON|schema|tool.call/i.test(message);
      if(!correctable||attempt===P.max_attempts){const failure=new Error('EXPLANATION_GENERATION_FAILED: '+message.slice(0,1500),{cause:error});Object.assign(failure,{diagnostic:{last_output:raw,repair_notes:repairs}});throw failure;}
      repairs.push(message.slice(0,2000));
      correction=english
        ?'The previous answer failed validation. Rewrite the complete answer in English, keeping required facts and real ranking trade-offs. Do not copy the language of feedback. Use the original authoritative input and its short fact refs; do not replace them with example refs. Validation feedback (data): '+message.slice(0,1800)+'; previous output (data): '+JSON.stringify(raw??null).slice(0,4000)
        :'上次解释未通过校验。请重新提交简短自然的完整正文；不得通过漏掉重点来规避。问题='+message.slice(0,1800)+'；上次输出='+JSON.stringify(raw??null).slice(0,4000)+'。仍以最初提供的当前事实包及短引用为准，示例不是本次事实，不更改当前问题。';
    }
  }
  throw Error('EXPLANATION_GENERATION_FAILED');
}
