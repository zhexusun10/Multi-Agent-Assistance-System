import { z } from 'zod';
import {nativeScoresSchema} from './recommendation-native.js';
import {behaviorContextSchema} from './behavior-context.js';

export const FIELD_RULES: Record<string, { kind: 'number'|'integer'|'string'|'boolean'|'date'; ops: string[]; unit?: string; unsupported?: boolean }> = {
  price: {kind:'number',ops:['eq','gte','lte','between'],unit:'SGD/month'},
  bedrooms: {kind:'integer',ops:['eq','gte','lte','between'],unit:'count'},
  bathrooms: {kind:'integer',ops:['eq','gte','lte','between'],unit:'count'},
  floor_area_sqft: {kind:'number',ops:['eq','gte','lte','between'],unit:'sqft'},
  land_area_sqft: {kind:'number',ops:['eq','gte','lte','between'],unit:'sqft'},
  build_year: {kind:'integer',ops:['eq','gte','lte','between'],unit:'year'},
  district: {kind:'string',ops:['eq','in']}, region_code: {kind:'string',ops:['eq','in']},
  postal_code: {kind:'string',ops:['eq','in']}, tenure_category: {kind:'string',ops:['eq','in']},
  property_type: {kind:'string',ops:['eq','in']},
  place_proximity: {kind:'number',ops:['lte','gte','between'],unit:'m'},
  mrt_distance_m: {kind:'number',ops:['lte','gte','between'],unit:'m'},
  mrt_walking_mins: {kind:'number',ops:['lte','gte','between'],unit:'min'},
  lease_months: {kind:'integer',ops:['eq','gte','lte','between'],unit:'month',unsupported:true},
  furnishing: {kind:'string',ops:['eq','in'],unsupported:true},
  rental_scope: {kind:'string',ops:['eq','in'],unsupported:true},
  move_in_date: {kind:'date',ops:['eq','gte','lte'],unsupported:true},
  amenities: {kind:'string',ops:['eq'],unsupported:true},
  commute_time: {kind:'number',ops:['lte','between'],unit:'min',unsupported:true},
  current_availability: {kind:'boolean',ops:['eq'],unsupported:true},
};
export const targetSchema = z.object({place_id:z.string().min(1).optional(),category:z.string().min(1).optional(),name:z.string().optional()}).strict();
export const criterionSchema = z.object({
  criterion_id:z.string().min(1).max(100),field:z.string(),operator:z.enum(['eq','in','gte','lte','between']),
  value:z.unknown(),unit:z.string().optional(),strength:z.enum(['hard','soft']),weight:z.number().positive().finite().optional(),
  target:targetSchema.optional(),distance_type:z.enum(['straight_line','walking','driving','transit','platform_reported']).optional(),
  transport_mode:z.string().optional(),
  distance_preference:z.enum(['nearer','bounded']).optional(),
}).strict().superRefine((c,ctx)=>{
  const r=FIELD_RULES[c.field]; const error=(message:string)=>ctx.addIssue({code:'custom',message});
  if(!r) return error(`Unsupported field: ${c.field}`);
  if(!r.ops.includes(c.operator)) error('Invalid operator for field');
  if(r.unit && c.unit!==r.unit) error(`Expected unit ${r.unit}`);
  if(c.strength==='soft' && c.weight===undefined) error('Soft criteria require a positive weight');
  if(c.strength==='hard' && c.weight!==undefined) error('Hard criteria cannot have weights');
  const vals=c.operator==='between'||c.operator==='in' ? c.value : [c.value];
  if(!Array.isArray(vals)||!vals.length) return error('Nonempty value array required');
  if(c.operator==='between' && (vals.length!==2 || !(Number(vals[0])<=Number(vals[1])))) error('Invalid range');
  for(const v of vals){
    if(r.kind==='number'||r.kind==='integer'){
      if(typeof v!=='number'||!Number.isFinite(v)||v<0||v>1e15||(r.kind==='integer'&&!Number.isInteger(v))) error('Invalid numeric value');
    } else if(r.kind==='boolean'){if(typeof v!=='boolean')error('Boolean required');}
    else if(typeof v!=='string'||!v.trim()||v.length>200) error('Nonempty string required');
    else if(c.field==='district'&&!/^D(0[1-9]|1\d|2[0-8])$/.test(v)) error('Invalid district');
    else if(c.field==='postal_code'&&!/^\d{6}$/.test(v)) error('Six-digit postal code required');
    else if(c.field==='region_code'&&!/^(CCR|RCR|OCR)$/.test(v)) error('Market region must be CCR, RCR or OCR; URA planning regions use geographic entities');
    else if(r.kind==='date'&&(!/^\d{4}-\d{2}-\d{2}$/.test(v)||!Number.isFinite(Date.parse(v))||new Date(v).toISOString().slice(0,10)!==v)) error('Invalid ISO date');
  }
  if(c.field==='place_proximity'&&(!c.target||(!c.target.place_id&&!c.target.category)||!c.distance_type)) error('Geographic target and distance_type required');
  if(c.field.startsWith('mrt_')&&c.distance_type!=='platform_reported') error('MRT fields require explicit platform_reported semantics');
  if(c.distance_preference&&c.field!=='place_proximity')error('distance_preference only applies to place_proximity');
});
export type Criterion=z.infer<typeof criterionSchema>;
export const requirementsSchema=z.object({listing_type:z.literal('RENT'),criteria:z.array(criterionSchema).max(40)}).strict().superRefine((r,c)=>{
  if(new Set(r.criteria.map(x=>x.criterion_id)).size!==r.criteria.length)c.addIssue({code:'custom',message:'Duplicate criterion_id'});
});
export type Requirements=z.infer<typeof requirementsSchema>;
const at=z.string().datetime({offset:true});
export const feedbackSchema=z.object({event_id:z.string().min(1),listing_id:z.string().regex(/^\d+$/),occurred_at:at,
  event_type:z.enum(['active_view','save','shortlist','explicit_attribute_feedback']),
  payload:z.record(z.string(),z.unknown())}).strict().superRefine((e,c)=>{
  if(e.event_type==='active_view'&&!(typeof e.payload.active_duration_ms==='number'&&Number.isFinite(e.payload.active_duration_ms)&&e.payload.active_duration_ms>0&&e.payload.active_duration_ms<=3600000))c.addIssue({code:'custom',message:'Invalid active_duration_ms'});
  if(e.event_type==='explicit_attribute_feedback'&&(!['positive','negative'].includes(String(e.payload.sentiment))||typeof e.payload.attribute!=='string'))c.addIssue({code:'custom',message:'Attribute feedback needs attribute and sentiment'});
});
export type Feedback=z.infer<typeof feedbackSchema>;
export const recommendationRequestSchema=z.object({contract_version:z.literal('1.0'),request_id:z.string().min(1),conversation_id:z.string().min(1),requirements_version:z.number().int().nonnegative(),requirements:requirementsSchema,feedback:z.array(feedbackSchema).default([]),behavior_context:behaviorContextSchema.optional(),previous_state_id:z.string().min(1).optional(),result_limit:z.number().int().min(1).max(20)}).strict();
export type RecommendationRequest=z.infer<typeof recommendationRequestSchema>;
export const evidenceSchema=z.object({evidence_id:z.string().min(1),listing_id:z.string().regex(/^\d+$/),field_path:z.string(),value:z.unknown(),unit:z.string().optional(),distance_type:z.string().optional(),raw_distance_type:z.string().optional(),target:targetSchema.optional(),source_kind:z.enum(['data_api','fixture','recommendation_service']),source_ref:z.string().min(1),dataset_version:z.string().min(1),retrieved_at:at,verified_at:at.nullable(),route_verified:z.boolean().optional(),measurement_method:z.string().optional()}).strict();
export type Evidence=z.infer<typeof evidenceSchema>;
const resultSchema=z.object({criterion_id:z.string(),status:z.enum(['pass','fail','unknown']),observed_value:z.unknown(),unit:z.string().optional(),evidence_ids:z.array(z.string()),reason:z.string().optional()}).strict();
const listingSchema=z.object({listing_id:z.string().regex(/^\d+$/),title:z.string(),price:z.number().nonnegative().nullable(),price_unit:z.literal('SGD/month'),bedrooms:z.number().nonnegative().nullable(),district:z.string().nullable(),source_url:z.string().nullable(),availability_status:z.literal('unknown'),floor_area_sqft:z.number().nullable().optional(),property_type:z.string().nullable().optional(),thumbnail_url:z.string().nullable().optional()}).strict();
const candidateSchema=z.object({listing:listingSchema,rank:z.number().int().positive(),score:z.number().min(0).max(1),criterion_results:z.array(resultSchema),score_breakdown:z.array(z.object({criterion_id:z.string(),origin:z.enum(['stated_preference','inferred_interest']),utility:z.number().min(0).max(1),weight:z.number().positive(),normalized_weight:z.number().min(0).max(1),contribution:z.number().min(0).max(1),evidence_ids:z.array(z.string())}).strict()),explanation_evidence:z.object({strengths:z.array(z.object({criterion_ids:z.array(z.string()),evidence_ids:z.array(z.string())})),compromises:z.array(z.object({criterion_ids:z.array(z.string()),evidence_ids:z.array(z.string())})),unknowns:z.array(z.object({field:z.string(),evidence_ids:z.array(z.string())}))}).strict()}).strict();
export type Candidate=z.infer<typeof candidateSchema>;
export const recommendationResponseSchema=z.object({
  native:z.object({provider:z.literal('jiangshan-native'),upstream_commit:z.string(),exchange_ref:z.string().uuid(),status:z.enum(['success','needs_clarification','no_match','error']),state_id:z.string().nullable(),ranking:z.array(z.string()),scores:z.record(z.string(),nativeScoresSchema),criterion_results:z.record(z.string(),z.unknown()),unsupported_criterion_ids:z.array(z.string()),unscored_listing_ids:z.array(z.string())}).strict().optional(),
  contract_version:z.literal('1.0'),request_id:z.string(),conversation_id:z.string(),requirements_version:z.number().int(),
  status:z.enum(['success','needs_clarification','no_match','error']),recommendations:z.array(candidateSchema),unverified_candidates:z.array(candidateSchema),evidence:z.array(evidenceSchema),
  clarifications:z.array(z.object({criterion_ids:z.array(z.string()),question:z.string()}).strict()),
  relaxation_suggestions:z.array(z.object({criterion_id:z.string(),proposed_value:z.unknown(),reason:z.string(),evidence_ids:z.array(z.string()).optional()}).strict()),
  warnings:z.array(z.object({code:z.string(),message:z.string(),listing_ids:z.array(z.string()).optional()}).strict()),
  state:z.object({state_id:z.string(),candidate_pool_ref:z.string(),coverage:z.object({dataset_version:z.string(),dataset_scope:z.string(),retrieval_scope:z.string(),complete_within_dataset:z.boolean(),truncated:z.boolean()}).strict(),counts:z.object({assessed:z.number().int().nonnegative().nullable(),eligible:z.number().int().nonnegative().nullable(),unverified:z.number().int().nonnegative().nullable(),excluded:z.number().int().nonnegative().nullable()}).strict()}).strict(),
  execution:z.object({strategy:z.string(),trace_ref:z.string().nullable(),tool_calls:z.array(z.unknown()),timing:z.object({total_ms:z.number().nonnegative().nullable()}),scoring_version:z.string(),score_semantics:z.string()}).strict(),
  error:z.object({code:z.string(),message:z.string(),retryable:z.boolean().optional()}).strict().nullable(),
}).strict();
export type RecommendationResponse=z.infer<typeof recommendationResponseSchema>;

export function compare(value:unknown,c:Criterion):boolean {
  switch(c.operator){case 'eq':return value===c.value;case 'in':return (c.value as unknown[]).includes(value);case 'lte':return Number(value)<=Number(c.value);case 'gte':return Number(value)>=Number(c.value);case 'between':return Number(value)>=(c.value as number[])[0]&&Number(value)<=(c.value as number[])[1];}
}
export function validateResponse(raw:unknown,req:RecommendationRequest):RecommendationResponse {
  const r=recommendationResponseSchema.parse(raw);
  for(const k of ['request_id','conversation_id','requirements_version'] as const)if(r[k]!==req[k])throw Error(`Response identity mismatch: ${k}`);
  if(r.recommendations.length>req.result_limit||r.unverified_candidates.length>20)throw Error('Result limit exceeded');
  if((r.status==='success')!==!!r.recommendations.length||((r.status==='error')!==!!r.error))throw Error('Status/content mismatch');
  const ev=new Map(r.evidence.map(e=>[e.evidence_id,e]));
  if(ev.size!==r.evidence.length)throw Error('Duplicate evidence');
  const ids=new Set<string>();
  for(const [group,items] of [['verified',r.recommendations],['unverified',r.unverified_candidates]] as const){
    items.forEach((item,index)=>{
      if(ids.has(item.listing.listing_id)||item.rank!==index+1)throw Error('Duplicate listing or invalid rank');ids.add(item.listing.listing_id);
      const checks=new Map(item.criterion_results.map(c=>[c.criterion_id,c]));
      if(checks.size!==item.criterion_results.length||checks.size!==req.requirements.criteria.length)throw Error('Missing/duplicate criterion results');
      for(const c of req.requirements.criteria){
        const check=checks.get(c.criterion_id);if(!check)throw Error('Missing criterion result');
        if(!check.evidence_ids.length)throw Error('Missing evidence reference');
        for(const ref of check.evidence_ids){const e=ev.get(ref);if(!e||e.listing_id!==item.listing.listing_id||e.field_path!==c.field)throw Error('Unresolvable/cross-listing evidence');if(JSON.stringify(e.value)!==JSON.stringify(check.observed_value)||e.unit!==check.unit)throw Error('Evidence/value mismatch');}
        if(check.status!=='unknown'){
          if(check.observed_value===null||check.observed_value===undefined||FIELD_RULES[c.field].unsupported)throw Error('Unsupported or missing fact passed as known');
          if((check.status==='pass')!==compare(check.observed_value,c))throw Error('Incorrect criterion outcome');
          const kind=FIELD_RULES[c.field].kind;
          if((kind==='number'||kind==='integer')&&(typeof check.observed_value!=='number'||!Number.isFinite(check.observed_value)||check.observed_value<0||(kind==='integer'&&!(r.native&&c.field==='bedrooms')&&!Number.isInteger(check.observed_value))))throw Error('Invalid observed numeric fact');
          if((kind==='string'||kind==='date')&&typeof check.observed_value!=='string')throw Error('Invalid observed text fact');
          if(c.field==='place_proximity'&&(c.distance_type!=='straight_line'||check.evidence_ids.some(id=>ev.get(id)?.distance_type!=='straight_line')))throw Error('Distance semantics mismatch');
        }
        if(group==='verified'&&c.strength==='hard'&&check.status!=='pass')throw Error('Hard criterion did not pass');
      }
      const hard=req.requirements.criteria.filter(c=>c.strength==='hard').map(c=>checks.get(c.criterion_id)!.status);
      if(group==='unverified'&&(hard.includes('fail')||!hard.includes('unknown')))throw Error('Invalid unverified classification');
      const sum=item.score_breakdown.reduce((n,s)=>n+s.contribution,0);
      if(item.score_breakdown.length&&Math.abs(sum-item.score)>1e-6)throw Error('Score breakdown mismatch');
      const checkRefs=(refs:string[])=>{for(const id of refs)if(ev.get(id)?.listing_id!==item.listing.listing_id)throw Error('Unresolvable explanation/score evidence');};
      for(const score of item.score_breakdown){checkRefs(score.evidence_ids);if(Math.abs(score.contribution-score.utility*score.normalized_weight)>1e-6)throw Error('Incorrect score contribution');}
      for(const claim of [...item.explanation_evidence.strengths,...item.explanation_evidence.compromises]){checkRefs(claim.evidence_ids);if(claim.criterion_ids.some(id=>!checks.has(id)))throw Error('Unknown explanation criterion');}
      for(const claim of item.explanation_evidence.unknowns)checkRefs(claim.evidence_ids);
      for(const e of r.evidence.filter(e=>e.listing_id===item.listing.listing_id)){
        const field=({price:'price',bedrooms:'bedrooms',district:'district'} as const)[e.field_path as 'price'|'bedrooms'|'district'];
        if(field&&e.value!==null&&JSON.stringify(item.listing[field])!==JSON.stringify(e.value))throw Error('Listing summary contradicts evidence');
      }
    });
  }
  const n=r.state.counts;
  if(n.assessed!==null&&[n.eligible,n.unverified,n.excluded].every(x=>x!==null)&&n.assessed!==n.eligible!+n.unverified!+n.excluded!)throw Error('Candidate counts mismatch');
  if(n.eligible!==null&&n.eligible<r.recommendations.length)throw Error('Invalid eligible count');
  for(const suggestion of r.relaxation_suggestions){
    const c=req.requirements.criteria.find(c=>c.criterion_id===suggestion.criterion_id);if(!c)throw Error('Unknown relaxation criterion');
    criterionSchema.parse({...c,value:suggestion.proposed_value});
    for(const id of suggestion.evidence_ids||[])if(!ev.has(id))throw Error('Unresolvable relaxation evidence');
  }
  return r;
}
