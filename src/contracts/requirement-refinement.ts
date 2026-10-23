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
export type RefinementPlan={id:string;session_id:string;source_request_id:string;source_operation_id:string;requirements_version:number;dataset:string;trigger:'mock';questions:RefinementQuestion[];status:'OPEN'|'SUBMITTED'|'DISMISSED'|'STALE';answers:RefinementAnswer[];answer_summary:string[];submitted_operation_id:string|null};
export function publicRefinement(row:any):RefinementPlan{return {...row.body,id:row.id,session_id:row.session_id,source_request_id:row.source_request_id,source_operation_id:row.source_operation_id,requirements_version:row.requirements_version,status:row.status,answers:row.answers,answer_summary:row.answer_summary,submitted_operation_id:row.submitted_operation_id};}
