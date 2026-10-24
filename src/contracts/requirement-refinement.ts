import {z} from 'zod';
import type {Criterion} from './recommendation-v1.js';

export type RefinementQuestion={
  id:string;kind:'budget'|'bedrooms'|'property_type'|'area'|'ideal_rent'|'closer';
  title:string;help?:string;options?:{value:string;label:string}[];upper_bound?:number;target?:Criterion['target'];distance_type?:Criterion['distance_type'];
};
export const refinementAnswerSchema=z.object({
  question_id:z.string().min(1).max(180),action:z.enum(['set','any','skip']),
  strength:z.enum(['hard','soft']).optional(),min:z.number().finite().nonnegative().optional(),max:z.number().finite().positive().optional(),
  count:z.number().int().min(1).max(20).optional(),operator:z.enum(['eq','gte','lte']).optional(),
  values:z.array(z.string().min(1).max(100)).min(1).max(12).optional(),unit:z.enum(['sqm','sqft']).optional(),
}).strict();
export const refinementSubmitSchema=z.object({submission_id:z.string().uuid(),answers:z.array(refinementAnswerSchema).max(3)}).strict();
export type RefinementAnswer=z.infer<typeof refinementAnswerSchema>;
export type RefinementPlan={id:string;session_id:string;source_request_id:string;source_operation_id:string;requirements_version:number;dataset:string;trigger:'mock'|'native_cold_start';cold_start?:import('zod').infer<typeof import('./recommendation-native.js').nativeSelectionSchema>['cold_start'];questions:RefinementQuestion[];status:'OPEN'|'SUBMITTED'|'DISMISSED'|'STALE';answers:RefinementAnswer[];answer_summary:string[];submitted_operation_id:string|null};
export function unchangedRefinementSummary(q:RefinementQuestion,action:'any'|'skip'){
  if(action==='skip')return q.title+' 暂未补充';
  if(q.kind==='ideal_rent')return `保留原预算${q.upper_bound?`上限 ${q.upper_bound.toLocaleString('en-SG')} 新币`:''}，不新增理想月租偏好`;
  if(q.kind==='closer')return '保留已有地点范围，不额外优先更近';
  return q.title+' 不限';
}
export function refinementSubmissionText(summary:string[]){return '我补充了找房需求：\n'+summary.join('\n')+'\n请按更新后的需求重新推荐。';}
export function publicRefinement(row:any):RefinementPlan{
  const summary=(row.answer_summary||[]).map((line:string,i:number)=>{
    const q=row.body.questions[i],a=row.answers?.find((a:RefinementAnswer)=>a.question_id===q?.id);
    return q&&a&&a.action!=='set'?unchangedRefinementSummary(q,a.action):line;
  });
  return {...row.body,id:row.id,session_id:row.session_id,source_request_id:row.source_request_id,source_operation_id:row.source_operation_id,requirements_version:row.requirements_version,status:row.status,answers:row.answers,answer_summary:summary,submitted_operation_id:row.submitted_operation_id};
}
