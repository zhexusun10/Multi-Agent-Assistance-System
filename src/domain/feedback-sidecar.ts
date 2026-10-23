import type {PoolClient} from 'pg';
import type {RecommendationResponse} from '../contracts/recommendation-v1.js';
import {COUNTED_APPOINTMENT_STATUSES,EMPTY_SIDECAR,feedbackSidecarSchema,type FeedbackSidecar} from '../contracts/recommendation-native.js';

// Feedback sidecar for native contract 2.0 (spec R7). Built ONCE inside the agent-A apply
// transaction from persisted rows, stored in recommendation_calls.feedback_snapshot and agent B's
// queued body, and never recomputed on retry. Deterministic: no clocks, sorted outputs.
export type DurationRow={listing_id:string;recommendation_request_id:string;active_duration_ms:number};
export type ActionRow={action_id:string;action_type:string;status:string;listing_ids:string[]};
export type ColdStartState={exited:boolean;exited_pool_key:string|null};
const counted=new Set<string>(COUNTED_APPOINTMENT_STATUSES);
const byText=(a:string,b:string)=>a<b?-1:a>b?1:0;

/** previous_round is null unless the adopted round is a native contract-2.0 projection with a
 * non-empty native ranking (baseline, legacy 1.0 and empty rounds never yield INVALID_FEEDBACK).
 * Shown IDs are the NATIVE ranking: hardGap may have moved them into the V1 unverified group,
 * but the user still saw them. Browsing per shown ID = summed sample durations of that round
 * (recorded), else missing. Appointments: one entry per listing, statuses within the counted set. */
export function buildFeedbackSidecar(rec:RecommendationResponse|null|undefined,durations:DurationRow[],actions:ActionRow[],coldStart:ColdStartState|null|undefined):FeedbackSidecar{
  const native=rec?.native;
  let previous_round:FeedbackSidecar['previous_round']=null;
  if(rec&&native?.contract==='2.0'&&native.ranking.length){
    const shown=[...native.ranking],total=new Map<string,number>();
    for(const d of durations)if(d.recommendation_request_id===rec.request_id&&shown.includes(d.listing_id)&&Number.isFinite(d.active_duration_ms)&&d.active_duration_ms>0)total.set(d.listing_id,(total.get(d.listing_id)??0)+d.active_duration_ms);
    previous_round={request_id:rec.request_id,shown_listing_ids:shown,browsing:shown.map(listing_id=>total.has(listing_id)?{listing_id,status:'recorded' as const,duration_seconds:total.get(listing_id)!/1000}:{listing_id,status:'missing' as const,duration_seconds:null})};
  }
  const grouped=new Map<string,{statuses:Set<string>;action_ids:Set<string>}>();
  for(const a of actions){
    if(a.action_type!=='VIEWING'||!counted.has(a.status))continue;
    for(const listing_id of new Set(a.listing_ids)){const g=grouped.get(listing_id)??{statuses:new Set(),action_ids:new Set()};g.statuses.add(a.status);g.action_ids.add(a.action_id);grouped.set(listing_id,g);}
  }
  const appointments=[...grouped].sort(([a],[b])=>byText(a,b)).map(([listing_id,g])=>({listing_id,statuses:[...g.statuses].sort(byText),action_ids:[...g.action_ids].sort(byText)}));
  const cold_start=coldStart?.exited&&coldStart.exited_pool_key?{exited:true,exited_pool_key:coldStart.exited_pool_key}:EMPTY_SIDECAR.cold_start;
  return feedbackSidecarSchema.parse({previous_round,appointments,cold_start});
}

export async function loadFeedbackSidecar(tx:PoolClient,s:{session_id:string;recommendation:RecommendationResponse|null;recommendation_cold_start?:ColdStartState|null}):Promise<FeedbackSidecar>{
  const rec=s.recommendation;
  const durations=rec?(await tx.query("SELECT body->>'listing_id' AS listing_id,body->'payload'->>'recommendation_request_id' AS recommendation_request_id,(body->'payload'->>'active_duration_ms')::float8 AS active_duration_ms FROM listing_feedback WHERE session_id=$1 AND body->>'event_type'='active_view' AND body->'payload'->>'source'='sample' AND body->'payload'->>'recommendation_request_id'=$2 ORDER BY event_id",[s.session_id,rec.request_id])).rows as DurationRow[]:[];
  const actions=(await tx.query("SELECT id::text AS action_id,body->>'action_type' AS action_type,body->>'status' AS status,ARRAY(SELECT jsonb_array_elements_text(body->'listing_ids')) AS listing_ids FROM workflow_actions WHERE session_id=$1 AND body->>'action_type'='VIEWING' AND body->>'status'=ANY($2::text[]) ORDER BY id",[s.session_id,[...COUNTED_APPOINTMENT_STATUSES]])).rows as ActionRow[];
  return buildFeedbackSidecar(rec,durations,actions,s.recommendation_cold_start);
}

/** Cold-start exit bookkeeping at B-apply for an ADOPTED response; returns the new session value
 * (unchanged unless native evaluated the diagnostics and all exit conditions were met). Re-entry
 * on pool rebuild is decided natively from exited_pool_key. */
export function coldStartAfterAdoption(current:ColdStartState|null|undefined,rec:RecommendationResponse):ColdStartState|null{
  const selection=rec.native?.contract==='2.0'?rec.native.selection as {cold_start?:{evaluated?:unknown;exit_conditions_met?:unknown;pool_key?:unknown}}|null|undefined:null;
  const cs=selection?.cold_start,poolKey=typeof cs?.pool_key==='string'?cs.pool_key:typeof rec.native?.pool?.pool_key==='string'?rec.native.pool.pool_key as string:null;
  if(cs?.evaluated===true&&cs.exit_conditions_met===true&&poolKey)return {exited:true,exited_pool_key:poolKey};
  return current??null;
}
