import type {PoolClient} from 'pg';
import type {Action} from './actions.js';
import {INTEREST_POLICY} from './behavior.js';
import {behaviorContextSchema,type BehaviorContext} from '../contracts/behavior-context.js';
export function qualifiesViewingIntent(a:Pick<Action,'action_type'|'body'|'slots'|'status'>,now=Date.now()){
 const s=a.slots;if(a.action_type!=='VIEWING'||a.status==='CANCELLED'||!a.body.trim()||!s||s.time_zone!=='Asia/Singapore')return false;
 const from=Date.parse(s.starts_at),to=Date.parse(s.ends_at);return Number.isFinite(from)&&Number.isFinite(to)&&from>now&&to>from&&to-from<=4*3600000;
}
/** Explicit save/cancel only: opening or generating the default draft is not a booking signal. */
export async function recordViewingIntent(tx:PoolClient,a:Action){
 const active=qualifiesViewingIntent(a);
 for(const listing of a.listing_ids)await tx.query(`INSERT INTO listing_viewing_intents(session_id,action_id,listing_id,source_request_id,requirements_version,revision,state,slots)
 SELECT $1,$2,$3,$4,$5,$6,$7,$8 WHERE $7='active' OR EXISTS(SELECT 1 FROM listing_viewing_intents WHERE session_id=$1 AND action_id=$2 AND listing_id=$3)
 ON CONFLICT DO NOTHING`,[a.session_id,a.id,listing,a.source_request_id,a.requirements_version,a.revision,active?'active':'withdrawn',active?JSON.stringify(a.slots):null]);
}
export async function collectBehaviorContext(tx:PoolClient,sessionId:string,cutoff:Date|string,interestReady:boolean):Promise<BehaviorContext>{
 const rows=(await tx.query(`SELECT * FROM listing_view_segments WHERE session_id=$1 AND completed_at<=$2 AND active_duration_ms>0 ORDER BY completed_at,segment_id`,[sessionId,cutoff])).rows;
 const groups=new Map<string,{ms:number;verified_ms:number;views:Set<string>;requests:Set<string>;last:string}>();
 for(const r of rows){const g=groups.get(r.listing_id)||{ms:0,verified_ms:0,views:new Set<string>(),requests:new Set<string>(),last:''};
  g.ms+=r.active_duration_ms;if(r.verified)g.verified_ms+=r.active_duration_ms;g.views.add(r.view_session_id);g.requests.add(r.source_request_id);g.last=new Date(r.completed_at).toISOString();groups.set(r.listing_id,g);
 }
 const intents=(await tx.query(`WITH latest AS (
 SELECT DISTINCT ON(action_id,listing_id) * FROM listing_viewing_intents WHERE session_id=$1 AND recorded_at<=$2 ORDER BY action_id,listing_id,recorded_at DESC,sequence DESC)
 SELECT DISTINCT ON(listing_id) * FROM latest WHERE state='active' ORDER BY listing_id,recorded_at DESC,sequence DESC`,[sessionId,cutoff])).rows;
 return behaviorContextSchema.parse({version:'1.0',as_of:new Date(cutoff).toISOString(),
  listing_view_durations:[...groups].sort(([a],[b])=>a.localeCompare(b)).map(([listing_id,g])=>({listing_id,duration_seconds:g.ms/1000,view_count:g.views.size,last_viewed_at:g.last,source_request_ids:[...g.requests].sort(),interest_eligible:interestReady&&g.verified_ms>=INTEREST_POLICY.minimum_listing_ms})),
  book_viewing_listings:intents.map(r=>({listing_id:r.listing_id,action_id:r.action_id,source_request_id:r.source_request_id,requirements_version:r.requirements_version,...r.slots,recorded_at:new Date(r.recorded_at).toISOString(),signal:'viewing_draft_saved',appointment_confirmed:false}))});
}
