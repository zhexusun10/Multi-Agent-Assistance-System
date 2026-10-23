import {z} from 'zod';
const id=z.string().regex(/^\d{1,20}$/),at=z.string().datetime({offset:true});
export const behaviorContextSchema=z.object({
 version:z.literal('1.0'),as_of:at,
 listing_view_durations:z.array(z.object({listing_id:id,duration_seconds:z.number().finite().nonnegative(),view_count:z.number().int().positive(),last_viewed_at:at,source_request_ids:z.array(z.string().uuid()).min(1),interest_eligible:z.boolean()}).strict()),
 book_viewing_listings:z.array(z.object({listing_id:id,action_id:z.string().uuid(),source_request_id:z.string().uuid(),requirements_version:z.number().int().nonnegative(),starts_at:at,ends_at:at,time_zone:z.literal('Asia/Singapore'),recorded_at:at,signal:z.literal('viewing_draft_saved'),appointment_confirmed:z.literal(false)}).strict())
}).strict().superRefine((v,c)=>{
 for(const [key,items] of [['listing_view_durations',v.listing_view_durations],['book_viewing_listings',v.book_viewing_listings]] as const)if(new Set(items.map(x=>x.listing_id)).size!==items.length)c.addIssue({code:'custom',path:[key],message:'Duplicate listing summary'});
 for(const [i,item] of v.book_viewing_listings.entries())if(Date.parse(item.ends_at)<=Date.parse(item.starts_at)||Date.parse(item.ends_at)-Date.parse(item.starts_at)>14400000)c.addIssue({code:'custom',path:['book_viewing_listings',i],message:'Invalid viewing interval'});
});
export type BehaviorContext=z.infer<typeof behaviorContextSchema>;
