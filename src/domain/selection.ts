import {feedbackSchema,type Candidate,type Feedback,type RecommendationResponse} from '../contracts/recommendation-v1.js';
import {BusinessError} from '../stores/domain.js';

import {displayedCandidates} from '../shared/recommendation-display.js';
export {displayedCandidates,RECOMMENDATION_DISPLAY_LIMIT} from '../shared/recommendation-display.js';
export function resolveListingSelection(rec:RecommendationResponse|null|undefined,query:string):Candidate{
 const explicit=query.match(/房源\s*(?:ID|编号)\s*[:：#]?\s*(\d+)|(?:listing|property)\s*(?:ID|#)\s*[:：]?\s*(\d+)|房源\s*(\d{5,})/i);
 const candidates=displayedCandidates(rec);
 if(explicit){const id=explicit[1]||explicit[2]||explicit[3],item=candidates.find(c=>c.listing.listing_id===id);if(!item)throw new BusinessError('SELECTION_NOT_IN_RESULT',"This home is not in the current list. Select another home.");return item;}
 const ordinal=query.match(/第\s*([一二三四五六七八九十\d]+)\s*套|(?:listing|property)\s*(\d{1,2})\b/i),map:Record<string,number>={"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,'七':7,'八':8,'九':9,'十':10};
 const pool=/待核实(?:候选|列表|房源)?/.test(query)?candidates.filter(c=>rec?.unverified_candidates.includes(c)):/已核验|已验证/.test(query)?candidates.filter(c=>rec?.recommendations.includes(c)):candidates;
 const n=ordinal?(map[ordinal[1]]??Number(ordinal[1]||ordinal[2])):pool.length===1?1:0;
 const item=Number.isInteger(n)&&n>0?pool[n-1]:null;
 if(!item)throw new BusinessError('SELECTION_REQUIRED',"Select Contact agent on a listing, say “I would like to view listing 1”, or provide a listing ID.");return item;
}

// Explicit feedback on a displayed listing: ordinals 1-10, in Chinese words or numeric digits,
// up to the native ten-result limit. Out-of-range ordinals such as the eleventh are ignored.
const ORDINALS:Record<string,number>={"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,'七':7,'八':8,'九':9,'十':10};
export function interpretFeedback(query:string,rec:any,source:string|null,op:string):Feedback[]{
  if(!rec||source!==rec.request_id)return [];
  const m=query.match(/第\s*([一二三四五六七八九十]|10|[1-9])\s*套.*?(太小|不喜欢|喜欢|面积小)/);if(!m)return [];
  const candidate=displayedCandidates(rec)[(ORDINALS[m[1]]??Number(m[1]))-1];if(!candidate)return [];
  return [feedbackSchema.parse({event_id:`explicit:${op}`,listing_id:candidate.listing.listing_id,occurred_at:new Date().toISOString(),event_type:'explicit_attribute_feedback',payload:{attribute:/小/.test(m[2])?'floor_area_sqft':'listing',sentiment:m[2]==='喜欢'?'positive':'negative',quote:m[0]}})];
}
