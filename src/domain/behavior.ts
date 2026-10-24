import {z} from 'zod';
import type {PoolClient} from 'pg';
import {DomainStore,BusinessError,hash,type DomainState} from '../stores/domain.js';
import type {Feedback,RecommendationResponse} from '../contracts/recommendation-v1.js';

const identity={event_id:z.string().uuid(),session_id:z.string().uuid(),source_request_id:z.string().uuid(),listing_id:z.string().regex(/^\d{1,20}$/),tab_id:z.string().uuid(),requirements_version:z.number().int().nonnegative()};
export const behaviorEventSchema=z.discriminatedUnion('kind',[
  z.object({...identity,kind:z.literal('exposure'),position:z.number().int().min(1).max(100)}).strict(),
  z.object({...identity,kind:z.enum(['begin','heartbeat','complete']),view_session_id:z.string().uuid(),segment_id:z.string().uuid(),sequence:z.number().int().min(0).max(100000),active_duration_ms:z.number().int().min(0).max(3600000),end_reason:z.enum(['return','hidden','blur','idle','other_area','query','session','pagehide','gap','lease','heartbeat','segment_limit']).optional()}).strict()
]);
export type BehaviorEvent=z.infer<typeof behaviorEventSchema>;
export const INTEREST_POLICY={minimum_listing_ms:15000,minimum_listings:2,minimum_total_ms:60000,lease_ms:25000} as const;

export function browserInterestNotice(ready:boolean,response:RecommendationResponse){
  if(!ready)return null;
  // Native response 2.0 reports behaviour as behavior_score (browsing_score: cumulative browsing); archived 1.0 scores use implicit_score.
  const applied=Object.values(response.native?.scores||{}).some((s:any)=>'behavior_score' in s?s.behavior_score!==null:s.implicit_score!==null);
  return applied?{code:'WEB_INTEREST',message:"Your earlier active viewing was also considered. Explicit requirements still take priority."}:{code:'WEB_INTEREST_NOT_APPLIED',message:"Viewing activity is saved. No usable behavioural score is available this round; explicit requirements determine recommendations."};
}

export class BehaviorService{
  constructor(readonly store:DomainStore){}
  async record(owner:string,input:unknown){
    const e=behaviorEventSchema.parse(input);await this.store.assertOwner(e.session_id,owner);
    return this.store.transaction(async tx=>{
      const s=await this.store.lock(tx,e.session_id),fingerprint=hash(e);
      const receipt=(await tx.query('SELECT payload_hash,response FROM browser_behavior_receipts WHERE session_id=$1 AND event_id=$2',[e.session_id,e.event_id])).rows[0];
      if(receipt){if(receipt.payload_hash!==fingerprint)throw new BusinessError('EVENT_ID_CONFLICT',"A viewing event’s contents cannot be changed.");return receipt.response;}
      // Source proof is immutable and session-owned; delayed events may reference an older result.
      const call=(await tx.query('SELECT request,response FROM recommendation_calls WHERE id=$1 AND session_id=$2',[e.source_request_id,e.session_id])).rows[0];
      const rec=call?.response,items=[...(rec?.recommendations||[]),...(rec?.unverified_candidates||[])];
      if(!rec||call.request.requirements_version!==e.requirements_version||!items.some(x=>x.listing.listing_id===e.listing_id))throw new BusinessError('BEHAVIOR_SOURCE_INVALID',"Viewing activity must refer to a home actually displayed in this conversation.",422);
      const verified=!!rec.native?.ranking?.includes(e.listing_id)&&rec.recommendations.some((x:any)=>x.listing.listing_id===e.listing_id);
      let result:any={status:'recorded',ranking_applied:false};
      if(e.kind==='exposure'){
        await tx.query('INSERT INTO listing_exposures(session_id,source_request_id,listing_id,tab_id,position) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING',[e.session_id,e.source_request_id,e.listing_id,e.tab_id,e.position]);
      }else{
        // An owner-wide lease prevents two tabs/sessions from accumulating simultaneously.
        await tx.query('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',[s.owner_hash]);
        const lease=(await tx.query('SELECT *,expires_at>clock_timestamp() AS alive FROM browser_reading_leases WHERE owner_hash=$1 FOR UPDATE',[s.owner_hash])).rows[0];
        const row=(await tx.query('SELECT * FROM listing_view_segments WHERE segment_id=$1 FOR UPDATE',[e.segment_id])).rows[0];
        if(e.kind==='begin'){
          if(e.sequence!==0||e.active_duration_ms!==0)throw new BusinessError('INVALID_VIEW_BEGIN',"A viewing segment must start at zero elapsed time.",422);
          if(row){if(row.session_id!==e.session_id||row.tab_id!==e.tab_id||row.source_request_id!==e.source_request_id||row.listing_id!==e.listing_id)throw new BusinessError('VIEW_ID_CONFLICT',"This viewing identifier is already in use.");result={...result,granted:!row.completed_at&&lease?.alive&&lease.segment_id===e.segment_id};}
          else if(lease?.alive&&lease.segment_id!==e.segment_id){return {status:'paused',granted:false,ranking_applied:false};}
          else{
            await tx.query('INSERT INTO listing_view_segments(segment_id,view_session_id,session_id,source_request_id,requirements_version,listing_id,tab_id,verified,last_hash) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)',[e.segment_id,e.view_session_id,e.session_id,e.source_request_id,e.requirements_version,e.listing_id,e.tab_id,verified,fingerprint]);
            await tx.query("INSERT INTO browser_reading_leases VALUES($1,$2,$3,clock_timestamp()+interval '25 seconds') ON CONFLICT(owner_hash) DO UPDATE SET tab_id=$2,segment_id=$3,expires_at=excluded.expires_at",[s.owner_hash,e.tab_id,e.segment_id]);
            result={...result,granted:true};
          }
        }else{
          if(!row||row.session_id!==e.session_id||row.source_request_id!==e.source_request_id||row.listing_id!==e.listing_id||row.tab_id!==e.tab_id||row.view_session_id!==e.view_session_id)throw new BusinessError('VIEW_NOT_FOUND',"The viewing record does not exist or its source does not match.",422);
          if(row.completed_at||e.sequence<row.last_sequence)result={...result,active_duration_ms:row.active_duration_ms,completed:!!row.completed_at};
          else if(e.sequence===row.last_sequence){if(row.last_hash!==fingerprint)throw new BusinessError('VIEW_SEQUENCE_CONFLICT',"The contents of the same viewing sequence cannot change.");}
          else{
            if(e.active_duration_ms<row.active_duration_ms)throw new BusinessError('VIEW_DURATION_REVERSED',"Active viewing time cannot decrease.",422);
            const owns=lease?.alive&&lease.segment_id===e.segment_id&&lease.tab_id===e.tab_id;
            const now=Date.now(),elapsed=Math.max(0,now-new Date(row.started_at).getTime());
            if(e.active_duration_ms>elapsed+1500)throw new BusinessError('VIEW_DURATION_INVALID',"The reported duration exceeds actual elapsed time.",422);
            // Expired/unowned intervals are not invented on reconnection.
            const accepted=owns?e.active_duration_ms:row.active_duration_ms,completed=e.kind==='complete'||!owns;
            await tx.query('UPDATE listing_view_segments SET active_duration_ms=$2,last_sequence=$3,last_hash=$4,updated_at=clock_timestamp(),completed_at=CASE WHEN $5 THEN clock_timestamp() ELSE NULL END,end_reason=$6,quality_note=$7 WHERE segment_id=$1',[e.segment_id,accepted,e.sequence,fingerprint,completed,e.end_reason||null,owns?null:'lease_expired_or_replaced']);
            if(owns){if(completed)await tx.query('DELETE FROM browser_reading_leases WHERE owner_hash=$1 AND segment_id=$2',[s.owner_hash,e.segment_id]);else await tx.query("UPDATE browser_reading_leases SET expires_at=clock_timestamp()+interval '25 seconds' WHERE owner_hash=$1 AND segment_id=$2",[s.owner_hash,e.segment_id]);}
            result={...result,active_duration_ms:accepted,completed,restart_required:!owns};
          }
        }
      }
      await tx.query('INSERT INTO browser_behavior_receipts(session_id,event_id,payload_hash,response) VALUES($1,$2,$3,$4)',[e.session_id,e.event_id,fingerprint,JSON.stringify(result)]);
      return result;
    });
  }
  async summary(session:string){
    const r=(await this.store.pool.query('SELECT count(*)::int AS segments,count(DISTINCT listing_id)::int AS listings,COALESCE(sum(active_duration_ms),0)::float AS active_duration_ms FROM listing_view_segments WHERE session_id=$1 AND completed_at IS NOT NULL',[session])).rows[0];
    return {...r,policy:INTEREST_POLICY,update_mode:'on_user_recommendation_request',ranking_applied:false};
  }
}

/** Called only while constructing a USER-initiated search, under the session lock. */
export async function prepareBrowserFeedback(tx:PoolClient,s:DomainState,cutoff:Date|string){
  // A crashed document may never send its final packet. Seal only confirmed
  // heartbeats after its lease expires; never extrapolate unseen wall time.
  await tx.query("UPDATE listing_view_segments v SET completed_at=v.updated_at,end_reason='lease',quality_note='abandoned_after_last_confirmed_heartbeat' WHERE v.session_id=$1 AND v.completed_at IS NULL AND v.updated_at+interval '25 seconds'<=$2 AND NOT EXISTS(SELECT 1 FROM browser_reading_leases l WHERE l.segment_id=v.segment_id AND l.expires_at>$2)",[s.session_id,cutoff]);
  const rows=(await tx.query('SELECT * FROM listing_view_segments WHERE session_id=$1 AND verified AND completed_at <= $2 AND active_duration_ms>0 ORDER BY completed_at,segment_id',[s.session_id,cutoff])).rows;
  const totals=new Map<string,number>();for(const r of rows)totals.set(r.listing_id,(totals.get(r.listing_id)||0)+r.active_duration_ms);
  const qualified=new Set([...totals].filter(([,ms])=>ms>=INTEREST_POLICY.minimum_listing_ms).map(([id])=>id));
  const total=[...totals].filter(([id])=>qualified.has(id)).reduce((n,[,ms])=>n+Math.min(ms,120000),0);
  const ready=qualified.size>=INTEREST_POLICY.minimum_listings&&total>=INTEREST_POLICY.minimum_total_ms;
  if(ready)for(const r of rows.filter(r=>qualified.has(r.listing_id))){
    const feedback:Feedback={event_id:`web:${r.segment_id}`,listing_id:r.listing_id,occurred_at:new Date(r.completed_at).toISOString(),event_type:'active_view',payload:{source:'web_detail',recommendation_request_id:r.source_request_id,view_session_id:r.segment_id,active_duration_ms:r.active_duration_ms}};
    await tx.query('INSERT INTO listing_feedback(session_id,event_id,body,created_at) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING',[s.session_id,feedback.event_id,JSON.stringify(feedback),r.completed_at]);
  }
  return {source:'web_detail',ready,qualified_listings:qualified.size,active_duration_ms:total,cutoff:new Date(cutoff).toISOString(),policy:INTEREST_POLICY};
}
