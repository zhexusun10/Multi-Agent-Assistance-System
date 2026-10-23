import {feedbackSchema,type Candidate,type Feedback,type RecommendationResponse} from '../contracts/recommendation-v1.js';
import {BusinessError} from '../stores/domain.js';

/** Same order as the page: verified first, then pending verification, numbered once. */
export function displayedCandidates(rec:RecommendationResponse|null|undefined):Candidate[]{return [...(rec?.recommendations||[]),...(rec?.unverified_candidates||[])];}
export function resolveListingSelection(rec:RecommendationResponse|null|undefined,query:string):Candidate{
 const explicit=query.match(/房源\s*(?:ID|编号)\s*[:：#]?\s*(\d+)|(?:listing|property)\s*(?:ID|#)\s*[:：]?\s*(\d+)|房源\s*(\d{5,})/i);
 const candidates=displayedCandidates(rec);
 if(explicit){const id=explicit[1]||explicit[2]||explicit[3],item=candidates.find(c=>c.listing.listing_id===id);if(!item)throw new BusinessError('SELECTION_NOT_IN_RESULT','这套房源不在当前列表中，请重新选择。');return item;}
 const ordinal=query.match(/第\s*([一二三四五六七八九十\d]+)\s*套|(?:listing|property)\s*(\d{1,2})\b/i),map:Record<string,number>={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10};
 const pool=/待核实(?:候选|列表|房源)?/.test(query)?rec?.unverified_candidates||[]:/已核验|已验证/.test(query)?rec?.recommendations||[]:candidates;
 const n=ordinal?(map[ordinal[1]]??Number(ordinal[1]||ordinal[2])):pool.length===1?1:0;
 const item=Number.isInteger(n)&&n>0?pool[n-1]:null;
 if(!item)throw new BusinessError('SELECTION_REQUIRED','请点击房源卡片的“我想看这套”，或说“我要看第1套”，也可以提供房源 ID。');return item;
}

// Explicit NL feedback on a displayed listing: ordinals 1-10 (Chinese 一..十 or 1-2 digits), i.e.
// up to the 10 results of native contract 2.0; out-of-range ordinals (第11套) are ignored.
const ORDINALS:Record<string,number>={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10};
export function interpretFeedback(query:string,rec:any,source:string|null,op:string):Feedback[]{
  if(!rec||source!==rec.request_id)return [];
  const m=query.match(/第\s*([一二三四五六七八九十]|10|[1-9])\s*套.*?(太小|不喜欢|喜欢|面积小)/);if(!m)return [];
  const candidate=displayedCandidates(rec)[(ORDINALS[m[1]]??Number(m[1]))-1];if(!candidate)return [];
  return [feedbackSchema.parse({event_id:`explicit:${op}`,listing_id:candidate.listing.listing_id,occurred_at:new Date().toISOString(),event_type:'explicit_attribute_feedback',payload:{attribute:/小/.test(m[2])?'floor_area_sqft':'listing',sentiment:m[2]==='喜欢'?'positive':'negative',quote:m[0]}})];
}
