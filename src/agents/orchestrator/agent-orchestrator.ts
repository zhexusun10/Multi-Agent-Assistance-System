import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {IntentRecognizer,type IntentResult} from './intent-recognizer.js';
import {INTENT_PARAMETERS} from './intent-templates.js';
export type IntentPlan={kind:'more'|'search'|'knowledge'|'explain'|'message'|'viewing'|'summary'|'daily'|'status'|'clarify';agent:'a'|'c'|'d';deferred_summary:boolean};
export function planIntent(result:Pick<IntentResult,'intent'|'intent_group'|'confidence'|'requested_tasks'>):IntentPlan{
  if(result.intent==='other'||result.confidence<INTENT_PARAMETERS.confidence_threshold)return {kind:'clarify',agent:'d',deferred_summary:false};
  if(result.intent==='recommendation_more')return {kind:'more',agent:'c',deferred_summary:false};
  if(result.intent_group==='search')return {kind:'search',agent:'a',deferred_summary:result.requested_tasks.includes('summary')||result.requested_tasks.includes('daily')};
  const kinds:Record<string,IntentPlan['kind']>={knowledge:'knowledge',rental_knowledge:'knowledge',explanation:'explain',recommendation_explanation:'explain',followup:'message',contact_salesperson:'message',arrange_viewing:'viewing',recommendation_summary:'summary',conversation_summary:'daily',followup_status:'status'};
  const kind=kinds[result.intent]||'clarify';return {kind,agent:kind==='explain'?'c':'d',deferred_summary:false};
}
export class AgentOrchestrator{
  readonly recognizer:IntentRecognizer;
  constructor(model?:BaseChatModel,recognizer?:IntentRecognizer){this.recognizer=recognizer||new IntentRecognizer(model);}
  async recognizeIntent(query:string,state:{messages:{role:string;text:string}[];requirements_version:number;requirements:unknown;latest_request_id:string|null;pending:unknown;recommendation:unknown}){
    return this.recognizer.recognize(query,state.messages,{requirements_version:state.requirements_version,requirements:state.requirements,source_request_id:state.latest_request_id,has_recommendation:!!state.recommendation,pending:state.pending});
  }
  plan(result:IntentResult){return planIntent(result);}
}
