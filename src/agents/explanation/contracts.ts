import {z} from 'zod';

export const explanationOutputSchema=z.object({
  version:z.literal('2.0'),
  focus_listing_ids:z.array(z.string()).max(10),
  // Keep the public wire version compatible; listing_id binds each new guide
  // paragraph to its candidate, while old persisted narratives remain readable.
  paragraphs:z.array(z.object({text:z.string().min(1).max(1000),support_refs:z.array(z.string()).min(1).max(30),listing_id:z.string().nullable().optional()}).strict()).min(1).max(12),
}).strict();
export const explanationAuditSchema=z.object({valid:z.boolean(),issues:z.array(z.object({code:z.enum(['grounding','ranking','coverage','focus','style']),detail:z.string().max(600)}).strict()).max(12)}).strict();
export type ExplanationOutput=z.infer<typeof explanationOutputSchema>;
export type ExplanationFact={ref:string;kind:'requirement'|'condition'|'listing'|'ranking'|'limitation'|'comparison'|'availability';listing_ids:string[];text:string;source_refs:string[];numbers:number[];literals?:string[];derived_from?:string[];subject_listing_id?:string;cohort?:import('./cohort.js').CohortSummary&{field:string;group:'recommended'|'unverified'};distance_metric?:string;proximity?:{target_name:string;target_key:string;distance_type:string;transport_mode?:string}};
export type ExplanationBundle={
  question:string;
  requirements:{ref:string;label:string;strength:'hard'|'soft';comparison_only:boolean;criterion_ids:string[];results:{listing_id:string;position:number;title:string;status:'pass'|'fail'|'unknown';observed_value:unknown;unit:string|null;fact_refs:string[]}[]}[];
  listings:{listing_id:string;position:number;title:string;group:'hard_pass'|'hard_unknown';ranking_status:'ranked'|'not_ranked';fact_refs:string[]}[];
  facts:ExplanationFact[];
  required_refs:string[];
  ranking:{mode:string;active_criteria:string[];behavior_features:string[];ranked_listing_ids:string[];filter_only_criteria:string[];unscored_listing_ids:string[]};
  decision_support?:{
    continuation?:{previous_count:number;listing_ids:string[]};
    mode:'guide'|'focused';target_listing_ids:string[];explain_order:boolean;
    profiles:{listing_id:string;position:number;title:string;known_refs:string[];extra_refs:string[];additional_refs:string[];highlight_refs:string[];caution_refs:string[];core_refs?:string[]}[];
  };
};
