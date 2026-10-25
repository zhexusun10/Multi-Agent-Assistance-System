import {randomUUID} from 'node:crypto';
import type {PoolClient} from 'pg';
import {DomainStore,BusinessError,hash,type DomainState} from '../../../stores/domain.js';
import {refinementSubmitSchema,refinementSubmissionText} from '../../../contracts/requirement-refinement.js';
import {planRefinement} from './planner.js';
import {compileRefinement} from './answers.js';
import {refinementTrigger} from './trigger.js';

export class RequirementRefinementService{
  constructor(readonly store:DomainStore,readonly dataset:string){}
  async generate(tx:PoolClient,s:DomainState):Promise<string|null>{
    if(process.env.REQUIREMENT_REFINEMENT_MODE==='off'||!s.recommendation||!s.active_operation_id||s.recommendation.state.coverage.dataset_version!==this.dataset||s.recommendation.request_id!==s.latest_request_id||s.recommendation.requirements_version!==s.requirements_version)return null;
    const trigger=refinementTrigger(s.recommendation);
    if(!trigger){
      await tx.query("UPDATE requirement_refinement_plans SET status='STALE',updated_at=now() WHERE session_id=$1 AND status='OPEN'",[s.session_id]);
      return null;
    }
    const history=(await tx.query('SELECT * FROM requirement_refinement_plans WHERE session_id=$1 ORDER BY created_at',[s.session_id])).rows;
    if(history.some(p=>p.source_request_id===s.latest_request_id)||history.some(p=>p.status==='DISMISSED'))return null;
    const handled=new Set<string>(history.filter(p=>p.status==='SUBMITTED').flatMap(p=>p.body.questions.map((q:any)=>String(q.id))));
    const questions=planRefinement(s.requirements,s.recommendation,handled);if(!questions.length)return null;
    await tx.query("UPDATE requirement_refinement_plans SET status='STALE',updated_at=now() WHERE session_id=$1 AND status='OPEN'",[s.session_id]);
    const id=randomUUID();await tx.query('INSERT INTO requirement_refinement_plans(id,session_id,source_request_id,source_operation_id,requirements_version,body) VALUES($1,$2,$3,$4,$5,$6)',[id,s.session_id,s.latest_request_id,s.active_operation_id,s.requirements_version,JSON.stringify({dataset:this.dataset,...trigger,questions})]);return id;
  }
  private async current(tx:PoolClient,s:DomainState,id:string){
    const row=(await tx.query('SELECT * FROM requirement_refinement_plans WHERE id=$1 AND session_id=$2 FOR UPDATE',[id,s.session_id])).rows[0];
    if(!row)throw new BusinessError('REFINEMENT_NOT_FOUND',"Refinement card not found.",404);return row;
  }
  private assertCurrent(s:DomainState,p:any){
    if(p.status!=='OPEN'||p.source_request_id!==s.latest_request_id||p.requirements_version!==s.requirements_version||p.source_operation_id!==s.active_operation_id||p.body.dataset!==this.dataset||s.pending)throw new BusinessError('REFINEMENT_STALE',"Requirements have changed. Use the latest refinement card.");
  }
  async dismiss(session:string,id:string){return this.store.transaction(async tx=>{
    const s=await this.store.lock(tx,session),row=await this.current(tx,s,id);
    if(row.status==='DISMISSED')return {status:'dismissed'};
    this.assertCurrent(s,row);await tx.query("UPDATE requirement_refinement_plans SET status='DISMISSED',updated_at=now() WHERE id=$1",[id]);
    await this.store.event(tx,session,{type:'workflow_result',payload:{refinement_id:id}},'refinement-dismiss:'+id);return {status:'dismissed'};
  });}
  async submit(session:string,id:string,input:unknown){
    const submitted=refinementSubmitSchema.parse(input),submissionHash=hash(submitted.answers);
    return this.store.transaction(async tx=>{
      const s=await this.store.lock(tx,session),row=await this.current(tx,s,id);
      if(row.status==='SUBMITTED'){
        if(row.submission_id!==submitted.submission_id||row.submission_hash!==submissionHash)throw new BusinessError('REFINEMENT_ALREADY_SUBMITTED',"This card has already been submitted. View the new recommendations.");
        return {status:row.submitted_operation_id?'queued':'unchanged',operation_id:row.submitted_operation_id};
      }
      this.assertCurrent(s,row);
      let compiled:ReturnType<typeof compileRefinement>;try{compiled=compileRefinement(row.body.questions,submitted,s.requirements);}catch(e){throw new BusinessError('REFINEMENT_INVALID',e instanceof Error?e.message:"Invalid refinement information.",422);}
      const changed=compiled.draft.upsert.length>0&&hash(compiled.requirements)!==hash(s.requirements),operation=changed?randomUUID():null;
      if(changed&&(await tx.query('SELECT 1 FROM domain_operations WHERE session_id=$1 AND request_id=$2',[session,submitted.submission_id])).rowCount)throw new BusinessError('REQUEST_ID_CONFLICT',"The submission ID is already in use. Refresh and try again.");
      await tx.query("UPDATE requirement_refinement_plans SET status='SUBMITTED',answers=$2,answer_summary=$3,submission_id=$4,submitted_operation_id=$5,submission_hash=$6,updated_at=now() WHERE id=$1",[id,JSON.stringify(compiled.answers),JSON.stringify(compiled.summary),submitted.submission_id,operation,submissionHash]);
      if(operation){
        const query=refinementSubmissionText(compiled.summary);
        await tx.query("INSERT INTO domain_operations(id,session_id,request_id,query,kind,source_request_id,intent_plan) VALUES($1,$2,$3,$4,'search',$5,$6)",[operation,session,submitted.submission_id,query,s.latest_request_id,JSON.stringify({kind:'search',agent:'a',deferred_summary:false,source:'requirement_card',plan_id:id})]);
        if(s.active_operation_id)await tx.query("UPDATE domain_operations SET status='superseded' WHERE id=$1 AND status='queued'",[s.active_operation_id]);
        s.active_operation_id=operation;
        await this.store.queue(tx,s,operation,'a',{input_mode:'structured',draft:compiled.draft,query,latest_query:query,requirements:s.requirements,pending:null,recommendation:s.recommendation},'initial');
        this.store.message(s,'user',query);await this.store.saveState(tx,s);
      }
      await this.store.event(tx,session,{type:'workflow_result',payload:{refinement_id:id,operation_id:operation}},'refinement-submit:'+id);
      return {status:changed?'queued':'unchanged',operation_id:operation};
    });
  }
}
