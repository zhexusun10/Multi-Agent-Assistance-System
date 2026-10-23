import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {qualifiesViewingIntent,collectBehaviorContext} from '../src/domain/behavior-context.js';
import {behaviorContextSchema} from '../src/contracts/behavior-context.js';
import {nativeRequest} from '../src/adapters/native-recommendation.js';
const slots={starts_at:'2030-01-02T06:00:00Z',ends_at:'2030-01-02T07:00:00Z',time_zone:'Asia/Singapore'};
test('only a nonempty viewing draft with valid future slots qualifies; click, empty slots, past, reversed, long or cancelled do not',()=>{
 const a={action_type:'VIEWING' as const,body:'你好，我想看房。',slots,status:'PENDING_REVIEW'};
 assert(qualifiesViewingIntent(a,Date.parse('2030-01-01T00:00:00Z')));
 for(const patch of [{slots:null},{action_type:'MESSAGE' as const},{body:' '},{status:'CANCELLED'},{slots:{...slots,ends_at:slots.starts_at}},{slots:{...slots,ends_at:'2030-01-02T12:00:00Z'}}])assert(!qualifiesViewingIntent({...a,...patch},Date.parse('2030-01-01T00:00:00Z')));
 assert(!qualifiesViewingIntent(a,Date.parse('2030-01-03T00:00:00Z')));
});
test('first request has empty arrays and behavior handoff preserves exact units and explicit false booking confirmation',()=>{
 const empty={version:'1.0' as const,as_of:new Date().toISOString(),listing_view_durations:[],book_viewing_listings:[]};
 const req={contract_version:'1.0' as const,request_id:randomUUID(),conversation_id:randomUUID(),requirements_version:0,requirements:{listing_type:'RENT' as const,criteria:[]},feedback:[],result_limit:5,behavior_context:empty};
 assert.deepEqual(nativeRequest(req).request.behavior_context,empty);
 const book={listing_id:'123',action_id:randomUUID(),source_request_id:randomUUID(),requirements_version:1,...slots,recorded_at:empty.as_of,signal:'viewing_draft_saved',appointment_confirmed:false};
 assert(behaviorContextSchema.safeParse({...empty,book_viewing_listings:[book]}).success);
 assert(!behaviorContextSchema.safeParse({...empty,book_viewing_listings:[{...book,appointment_confirmed:true}]}).success);
 assert(!behaviorContextSchema.safeParse({...empty,book_viewing_listings:[book,book]}).success);
 assert(!behaviorContextSchema.safeParse({...empty,book_viewing_listings:[{...book,ends_at:book.starts_at}]}).success);
});
test('duration aggregation uses accepted segments, distinct detail opens and source IDs, without converting unknown listings into score eligible ones',async()=>{
 const request=randomUUID(),view=randomUUID(),cutoff='2030-01-01T01:00:00Z',rows=[
  {listing_id:'123',active_duration_ms:12000,verified:true,view_session_id:view,source_request_id:request,completed_at:'2030-01-01T00:01:00Z'},
  {listing_id:'123',active_duration_ms:18000,verified:true,view_session_id:view,source_request_id:request,completed_at:'2030-01-01T00:02:00Z'},
  {listing_id:'456',active_duration_ms:35000,verified:false,view_session_id:randomUUID(),source_request_id:request,completed_at:'2030-01-01T00:03:00Z'}];
 const tx={async query(sql:string){return {rows:sql.includes('listing_view_segments')?rows:[]};}} as any;
 const context=await collectBehaviorContext(tx,'s',cutoff,true);
 assert.deepEqual(context.listing_view_durations.map(r=>[r.listing_id,r.duration_seconds,r.view_count,r.interest_eligible]),[['123',30,1,true],['456',35,1,false]]);
 assert.deepEqual(context.listing_view_durations[0].source_request_ids,[request]);
});
