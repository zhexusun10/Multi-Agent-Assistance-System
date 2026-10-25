import type {Candidate,RecommendationResponse} from '../contracts/recommendation-v1.js';

export const RECOMMENDATION_DISPLAY_LIMIT=5;
export const RECOMMENDATION_RESERVOIR_LIMIT=10;
export const PRESENTATION_VERSION='20261024-append-ten';
type DisplayResponse=Partial<Pick<RecommendationResponse,'recommendations'|'unverified_candidates'|'presentation'>>;

/** Missing metadata intentionally preserves the five-home view of old sessions. */
export function displayLimit(rec:DisplayResponse|null|undefined):number{
  return rec?.presentation?.limit===10?RECOMMENDATION_RESERVOIR_LIMIT:RECOMMENDATION_DISPLAY_LIMIT;
}
/** Shared by the server, cards, navigation, selection and explanation. */
export function displayedCandidates(rec:DisplayResponse|null|undefined):Candidate[]{
  const seen=new Set<string>();
  return [...(rec?.recommendations||[]),...(rec?.unverified_candidates||[])].filter(c=>{
    if(seen.has(c.listing.listing_id))return false;
    seen.add(c.listing.listing_id);return true;
  }).slice(0,displayLimit(rec));
}
export function withPresentation(rec:RecommendationResponse,limit:5|10=5):RecommendationResponse{
  return {...rec,presentation:{limit,batch_size:5,loaded_limit:10}};
}
/** Returns a staged view. Adoption occurs atomically with the new explanation. */
export function nextRecommendationBatch(rec:RecommendationResponse){
  const previous=displayedCandidates(rec),expanded=withPresentation(rec,10);
  const seen=new Set(previous.map(c=>c.listing.listing_id));
  return {recommendation:expanded,previous_count:previous.length,listing_ids:displayedCandidates(expanded).filter(c=>!seen.has(c.listing.listing_id)).map(c=>c.listing.listing_id)};
}
/** Public projection; stored candidates, scores and native exchange stay intact. */
export function recommendationForDisplay(rec:RecommendationResponse|null):RecommendationResponse|null{
  if(!rec)return null;
  const visible=new Set(displayedCandidates(rec));
  return {...rec,recommendations:rec.recommendations.filter(c=>visible.has(c)),unverified_candidates:rec.unverified_candidates.filter(c=>visible.has(c))};
}
