import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {AIMessage,HumanMessage,SystemMessage} from '@langchain/core/messages';
import {z} from 'zod';
import type {Requirements} from '../../contracts/recommendation-v1.js';
import {modelIntakeSchema,intakeAuditSchema,type RequirementDraft} from './contracts.js';
import {selectIntakeExamples} from './few-shot.js';
import {intakeSystemPrompt,INTAKE_AUDITOR_PROMPT} from './prompt.js';
import {compileIntake} from './validation.js';
import {intakeAuditRequirements} from './audit-context.js';

export const INTAKE_PARAMETERS={max_attempts:3,few_shot_count:2,timeout_ms:30000,max_total_ms:90000,semantic_audit:true} as const;
export type IntakeAttempt={attempt:number;proposal?:unknown;draft?:RequirementDraft;effective_requirements?:Requirements;audit?:z.infer<typeof intakeAuditSchema>;error?:string;duration_ms:number};
export type IntakeOptions={
  prepareDraft?:(draft:RequirementDraft)=>Promise<{draft:RequirementDraft;effective_requirements:Requirements}>;
  onAttempt?:(event:IntakeAttempt)=>Promise<void>;
};
export async function extractModelRequirements(query:string,base:Requirements,model:BaseChatModel,skillText='',options:IntakeOptions={}):Promise<RequirementDraft>{
  const examples=selectIntakeExamples(query,INTAKE_PARAMETERS.few_shot_count);
  const structured=model.withStructuredOutput(modelIntakeSchema,{name:'submit_requirement_intake_v2',method:'functionCalling',strict:true});
  const auditor=model.withStructuredOutput(intakeAuditSchema,{name:'audit_requirement_intake',method:'functionCalling',strict:true});
  const messages=[new SystemMessage(intakeSystemPrompt(base,skillText)),...examples.flatMap(e=>[
    new HumanMessage(JSON.stringify({query:e.query,base_requirements:e.base})),new AIMessage(JSON.stringify(e.output))
  ]),new HumanMessage(JSON.stringify({query,base_requirements:base}))];
  let correction='';const repairs:string[]=[],started=Date.now();
  const timeout=()=>{const remaining=INTAKE_PARAMETERS.max_total_ms-(Date.now()-started);if(remaining<=0)throw Error('EXTRACTION_DEADLINE_EXCEEDED');return AbortSignal.timeout(Math.min(INTAKE_PARAMETERS.timeout_ms,remaining));};
  for(let attempt=1;attempt<=INTAKE_PARAMETERS.max_attempts;attempt++){
    let proposal:unknown;const event:IntakeAttempt={attempt,duration_ms:0},attemptStarted=Date.now();
    try{
      proposal=await structured.invoke(correction?[...messages,new SystemMessage(correction)]:messages,{signal:timeout()});
      event.proposal=proposal;
      const compiled=compileIntake(proposal,query,base,{attempts:attempt,example_ids:examples.map(e=>e.id)});
      // Resolve real place identities and inspect the actual merged transition before auditing.
      // No state is committed here; rejected attempts remain isolated drafts.
      const prepared=options.prepareDraft?await options.prepareDraft(compiled):undefined;
      const draft=prepared?.draft??compiled;
      event.draft=draft;event.effective_requirements=prepared?.effective_requirements;
      const audit=intakeAuditSchema.parse(await auditor.invoke([new SystemMessage(INTAKE_AUDITOR_PROMPT),new HumanMessage(JSON.stringify({query,
        base_requirements:intakeAuditRequirements(base),proposal,
        compiled_draft:{...draft,upsert:intakeAuditRequirements({listing_type:'RENT',criteria:draft.upsert}).criteria},
        ...(prepared?{effective_requirements:intakeAuditRequirements(prepared.effective_requirements)}:{}),
        today:new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Singapore'}).format(new Date())}))],{signal:timeout()}));
      event.audit=audit;
      if(!audit.valid||audit.issues.length)throw Error('SEMANTIC_AUDIT: '+JSON.stringify(audit.issues));
      if(draft.extraction){draft.extraction.semantic_audit='passed';draft.extraction.repair_notes=repairs;}
      return draft;
    }catch(error){
      const message=error instanceof Error?error.message:String(error);
      event.error=message.slice(0,3000);
      const structural=error instanceof z.ZodError||/SEMANTIC_AUDIT:|SOURCE_QUOTE:|COVERAGE:|UNIT:|WEIGHT:|ENUM:|TARGET:|TRANSPORT:|COMMUTE:|LOCATION|NEAR:|REMOVAL:|DUPLICATE:|UNSUPPORTED:|parse|JSON|structured|schema|tool.call/i.test(message);
      if(!structural||attempt===INTAKE_PARAMETERS.max_attempts)throw new Error(`REQUIREMENT_EXTRACTION_FAILED: ${message.slice(0,1800)}`,{cause:error});
      const issues=error instanceof z.ZodError?error.issues.map(i=>({path:i.path,message:i.message})):message.slice(0,1800);
      repairs.push(JSON.stringify(issues).slice(0,2200));
      correction="The previous output failed validation. Examples are not the current input; do not copy their values, wording or conditions. Resubmit a complete output for the single actual input below, without dropping genuine requests to pass validation. Use clarifications for real ambiguity. Validation issue="+JSON.stringify(issues)+"; previous output="+JSON.stringify(proposal??null).slice(0,12000)+"; single actual current input="+JSON.stringify({query,base_requirements:base});
    }finally{
      event.duration_ms=Date.now()-attemptStarted;
      await options.onAttempt?.(event);
    }
  }
  throw Error('REQUIREMENT_EXTRACTION_FAILED');
}
