import {z} from 'zod';
import {behaviorContextSchema} from './behavior-context.js';

// Strict mirror of the native recommendation contract (recommendation/contracts, generated JSON
// Schemas in recommendation/contracts/schemas). Request = the main-branch orchestrator contract 1.0
// (feedback events + behavior_context) plus the recommendation-owned `cold_start`; response =
// recommendation-owned contract 2.0. Python and this mirror change together; the cross-layer tests
// parse recommendation/examples/contracts/*.json and tests/fixtures/recommendation-v2/ with these
// schemas. The UI projection (recommendation-v1.ts) is a separate contract.
export const NATIVE_REQUEST_CONTRACT_VERSION='1.0';
export const NATIVE_CONTRACT_VERSION='2.0'; // response
export const NATIVE_MAX_RESULTS=10; // == Python MAX_RESULT_LIMIT == recommendation.max_result_limit
// behavior_context.book_viewing_listings signals; must equal ranking.behavior.counted_appointment_signals
// in recommendation/settings/recommendation.yaml (cross-layer test on both sides). Sorted.
export const COUNTED_APPOINTMENT_SIGNALS=['viewing_draft_saved'] as const;

const id=z.string().min(1).max(128);
const unit=z.number().min(0).max(1);
const count=z.number().int().nonnegative();
const unique=(values:string[])=>new Set(values).size===values.length;
const UNKNOWN_REASON_KEYS=10; // == Python contracts/summary.UNKNOWN_REASON_KEYS (distinct reasons kept, the rest under "other")

// ---- request 2.0 -------------------------------------------------------------------------
export const nativeCriterionSchema=z.object({criterion_id:z.string().min(1),field:z.enum(['price','bedrooms','floor_area_sqft','district_code','property_type','distance','commute','furnishing','availability','lease_months']),operator:z.enum(['eq','in','gte','lte','between']),value:z.union([z.number(),z.string(),z.array(z.number()),z.array(z.string())]),unit:z.string().nullable().optional(),strength:z.enum(['hard','soft']),weight:z.number().positive().optional(),utility:z.enum(['threshold','proximity','minimize','maximize']).optional(),destination_id:z.string().nullable().optional(),destination_name:z.string().nullable().optional(),category:z.string().nullable().optional(),distance_type:z.enum(['straight_line','walking','transit']).nullable().optional(),transport_mode:z.enum(['walk','transit','drive']).nullable().optional()}).strict();
// Native ViewingEvent (incremental, quality-gated): validated by native, not scored (behavior_context
// holds the same time cumulatively).
export const nativeViewingEventSchema=z.object({event_id:z.string().min(1),conversation_id:z.string().min(1),recommendation_request_id:z.string().min(1),listing_id:z.string().min(1),event_type:z.literal('listing_view_duration').optional(),source:z.enum(['sample','web_detail']).optional(),occurred_at:z.string().datetime({offset:true}),payload:z.object({view_session_id:z.string().min(1),duration_seconds:z.number().finite().nonnegative()}).strict()}).strict();
export const coldStartInputSchema=z.object({exited:z.boolean().optional(),exited_pool_key:id.nullable().optional()}).strict()
  .refine(c=>Boolean(c.exited)===(c.exited_pool_key!=null),{message:'exited_pool_key is required exactly when exited is true'});
export const nativeRequestSchema=z.object({contract_version:z.literal(NATIVE_REQUEST_CONTRACT_VERSION).optional(),request_id:id,conversation_id:id,requirements_version:z.number().int().min(1),requirements:z.object({listing_type:z.literal('RENT').optional(),criteria:z.array(nativeCriterionSchema)}).strict(),feedback:z.array(nativeViewingEventSchema).optional(),previous_state_id:z.string().nullable().optional(),result_limit:z.number().int().min(1).max(NATIVE_MAX_RESULTS).nullable().optional(),behavior_context:behaviorContextSchema.nullable().optional(),cold_start:coldStartInputSchema.optional()}).strict()
  .refine(r=>(r.feedback||[]).every(e=>e.conversation_id===r.conversation_id),{message:'feedback conversation mismatch'})
  .refine(r=>unique((r.feedback||[]).map(e=>e.event_id)),{message:'duplicate feedback event_id'});
export type NativeRequest=z.infer<typeof nativeRequestSchema>;
export type ColdStartInput={exited:boolean;exited_pool_key:string|null};
export const NOT_EXITED:ColdStartInput={exited:false,exited_pool_key:null};

// ---- response 2.0 ------------------------------------------------------------------------
export const nativeResultSchema=z.object({criterion_id:z.string(),outcome:z.enum(['pass','fail','unknown','unsupported']),reason:z.string(),evidence_refs:z.array(z.string())}).strict();
export const nativeListingSchema=z.object({listing_id:z.string(),listing_type:z.string(),title:z.string().nullable(),price:z.number().nonnegative().nullable(),bedrooms:z.number().nonnegative().nullable(),floor_area_sqft:z.number().nonnegative().nullable(),property_type:z.string().nullable(),district_code:z.string().nullable(),postal_code:z.string().nullable(),latitude:z.number().nullable(),longitude:z.number().nullable(),url:z.string().nullable(),observed_at:z.string().nullable(),source:z.string(),normalization_warnings:z.array(z.string())}).strict();
const contribution=z.object({feature:z.string(),branch:z.enum(['soft','appointment','browsing']),reference:z.string().nullable(),raw_difference:z.number().nullable(),scale:z.number().nullable(),similarity:unit.nullable(),weight:unit,contribution:unit,unknown:z.boolean(),method:z.string().nullable()}).strict();
export const nativeScoresSchema=z.object({soft_score:unit.nullable(),behavior_score:unit.nullable(),appointment_score:unit.nullable(),browsing_score:unit.nullable(),combined_score:unit,soft_weight:unit,behavior_weight:unit,appointment_weight:unit,browsing_weight:unit,soft_evidence_coverage:unit,comparison_coverage:unit.nullable(),contributions:z.array(contribution),suppressed_features:z.array(z.string()),ordering:z.string()}).strict();
// Contract 1.0 score shape: only for reading archived V1 responses (session state, exchanges).
const legacyContribution=z.object({feature:z.string(),reference:z.string().nullable(),raw_difference:z.number().nullable(),scale:z.number().nullable(),similarity:unit.nullable(),weight:unit,contribution:unit,unknown:z.boolean()}).strict();
export const legacyNativeScoresSchema=z.object({explicit_score:unit.nullable(),implicit_score:unit.nullable(),combined_score:unit,explicit_weight:unit,implicit_weight:unit,evidence_coverage:unit,contributions:z.array(legacyContribution),suppressed_features:z.array(z.string()),ordering:z.string()}).strict();
export const SELECTION_REASONS=['ranked','leader','tie_group_sample','price_exploration_sample','small_pool'] as const;
export const nativeItemSchema=z.object({listing:nativeListingSchema,rank:z.number().int().min(1).max(NATIVE_MAX_RESULTS),score_rank:z.number().int().min(1),scores:nativeScoresSchema,criterion_results:z.array(nativeResultSchema),evidence_refs:z.array(z.string()),strengths:z.array(z.string()),trade_offs:z.array(z.string()),unknowns:z.array(z.string()),selection_reason:z.enum(SELECTION_REASONS),selection_note:z.string().min(1)}).strict();
const nativeEvidence=z.object({evidence_id:z.string(),listing_id:z.string(),criterion_id:z.string(),source:z.string(),observed_at:z.string(),value:z.union([z.number(),z.string()]).nullable(),unit:z.string().nullable(),distance_type:z.string().nullable(),method:z.string(),coverage_m:z.number().nullable(),destination_id:z.string().nullable(),unknown_reason:z.string().nullable(),mapping_status:z.enum(['known','missing','not_applicable']),snapshot_hash:z.string()}).strict();
export const nativeResolutionSchema=z.object({criterion_id:z.string(),field:z.string(),original:z.object({destination_id:z.string().nullable(),destination_name:z.string().nullable(),category:z.string().nullable()}).strict(),status:z.enum(['resolved','ambiguous','unresolved','not_applicable']),entity_id:z.string().nullable(),entity_kind:z.enum(['lta_station','ura_area','ura_region','osm_place','category']).nullable(),method:z.string(),reason:z.string().nullable(),candidates:z.array(z.object({entity_id:z.string(),name:z.string().nullable(),kind:z.string()}).strict())}).strict()
  .refine(r=>(r.status==='resolved')===(r.entity_id!==null),{message:'entity_id is required exactly when status is resolved'});
export const nativePoolSchema=z.object({pool_key:z.string(),pool_version:z.string(),data_version:z.string(),cache:z.enum(['built','reused','bypassed']),size:count,source_count:count,excluded_hard_fail:count,excluded_hard_unknown:count,unknown_reasons:z.record(z.string(),count)}).strict()
  .refine(p=>p.source_count===p.size+p.excluded_hard_fail+p.excluded_hard_unknown,{message:'pool partition mismatch'})
  .refine(p=>Object.keys(p.unknown_reasons).length<=UNKNOWN_REASON_KEYS+1,{message:`unknown_reasons keeps at most ${UNKNOWN_REASON_KEYS} reasons plus "other"`})
  .refine(p=>!Object.keys(p.unknown_reasons).length||p.excluded_hard_unknown>0,{message:'unknown_reasons require unknown exclusions'});
const coldStart=z.object({evaluated:z.boolean(),active:z.boolean(),reason:z.string(),coverage_c:unit.nullable(),boundary_group_b:z.number().int().min(1).nullable(),stability_s:unit.nullable(),thresholds:z.object({c:unit,b:z.number().int().min(1),s:unit}).strict(),exit_conditions_met:z.boolean(),pool_key:z.string().nullable()}).strict()
  .refine(c=>!c.exit_conditions_met||(c.evaluated&&c.stability_s!==null),{message:'exit_conditions_met requires evaluated diagnostics with stability_s'})
  .refine(c=>!c.active||c.evaluated,{message:'cold start cannot be active without evaluated diagnostics'});
export const nativeSelectionSchema=z.object({mode:z.enum(['ranked','near_tie_sampling','cold_start_exploration','small_pool','empty']),algorithm_version:z.string(),seed:z.string().regex(/^[0-9a-f]{64}$/).nullable(),result_limit:z.number().int().min(1).max(NATIVE_MAX_RESULTS),leaders:count,tie_group:z.object({size:z.number().int().min(1),min_score:unit,max_score:unit,span:z.number().nonnegative(),epsilon:z.number().nonnegative()}).strict().nullable(),exploration:z.object({candidate_count:count,sample_size:count,bins:z.array(z.object({lower:z.number().nullable(),upper:z.number().nullable(),count,sampled:count,quota:count}).strict())}).strict().nullable(),cold_start:coldStart}).strict();
export const nativeResponseSchema=z.object({contract_version:z.literal(NATIVE_CONTRACT_VERSION),request_id:z.string(),conversation_id:z.string(),requirements_version:z.number().int(),status:z.enum(['success','needs_clarification','no_match','error']),recommendations:z.array(nativeItemSchema).max(NATIVE_MAX_RESULTS),evidence:z.array(nativeEvidence),resolution:z.array(nativeResolutionSchema),pool:nativePoolSchema.nullable(),selection:nativeSelectionSchema.nullable(),clarifications:z.array(z.string()),relaxation_suggestions:z.array(z.string()),warnings:z.array(z.string()),state:z.object({state_id:z.string().nullable(),candidate_pool_ref:z.string().nullable(),run_sequence:z.number().int(),scope:z.string(),truncated:z.boolean(),source_count:z.number().int(),prefilter_excluded:z.number().int(),assessed_count:z.number().int()}).strict(),execution:z.object({trace_id:z.string(),trace_ref:z.string(),strategy:z.string(),scoring_version:z.string(),selection_algorithm:z.string(),config_version:z.number().int(),config_hash:z.string(),selection_hash:z.string(),model_identifier:z.string().nullable(),model_calls:z.number().int(),tool_calls:z.number().int(),duration_ms:z.number(),tokens:z.number().nullable(),data_mode:z.string(),controller_mode:z.string()}).strict(),error:z.object({code:z.string(),message:z.string(),retryable:z.boolean()}).strict().nullable()}).strict()
  .refine(r=>(r.status==='success')===(r.recommendations.length>0),{message:'success requires recommendations; other statuses prohibit them'})
  .refine(r=>(r.status==='error')===(r.error!==null),{message:'error details/status mismatch'})
  .refine(r=>r.recommendations.every((x,i)=>x.rank===i+1)&&unique(r.recommendations.map(x=>x.listing.listing_id)),{message:'ranks must be consecutive and listing IDs unique'})
  .refine(r=>!r.selection||r.recommendations.length<=r.selection.result_limit,{message:'more recommendations than the effective result_limit'})
  .refine(r=>!r.pool||r.recommendations.length<=r.pool.size,{message:'more recommendations than eligible pool listings'})
  .refine(r=>unique(r.recommendations.map(x=>String(x.score_rank))),{message:'duplicate score_rank'})
  .refine(r=>{const refs=new Set(r.evidence.map(e=>e.evidence_id)),used=new Set(r.recommendations.flatMap(x=>x.evidence_refs));return [...used].every(x=>refs.has(x))&&[...refs].every(x=>used.has(x));},{message:'evidence must be exactly the evidence referenced by returned recommendations (no unresolved or unreferenced evidence)'});
export type NativeResponse=z.infer<typeof nativeResponseSchema>;
export type NativeListing=z.infer<typeof nativeListingSchema>;
export type NativeScores=z.infer<typeof nativeScoresSchema>;
export type LegacyNativeScores=z.infer<typeof legacyNativeScoresSchema>;
export type NativeItem=z.infer<typeof nativeItemSchema>;

/** Readers that predate response 2.0 (e.g. the Explanation Agent) see one view of both score shapes:
 * stated = soft requirement fit (1.0: explicit), interest = behaviour similarity (1.0: implicit). */
export type AnyNativeScores=NativeScores|LegacyNativeScores;
export const statedWeight=(s:AnyNativeScores)=>'soft_weight' in s?s.soft_weight:s.explicit_weight;
export const interestWeight=(s:AnyNativeScores)=>'behavior_weight' in s?s.behavior_weight:s.implicit_weight;
export const interestScore=(s:AnyNativeScores)=>'behavior_weight' in s?s.behavior_score:s.implicit_score;
