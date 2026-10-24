export const RECOMMENDATION_DISPLAY_LIMIT=5;
export const PRESENTATION_VERSION='20261024-refinement-recovery';

/** The same selector is compiled for Node and served to the browser. Native
 * responses remain intact. Pending homes fill unused places, in original order. */
export function displayedCandidates(rec:Partial<Pick<RecommendationResponse,'recommendations'|'unverified_candidates'>>|null|undefined):Candidate[]{
  return [...(rec?.recommendations||[]),...(rec?.unverified_candidates||[])].slice(0,RECOMMENDATION_DISPLAY_LIMIT);
}

/** Public view only. The stored response and native exchange remain complete. */
export function recommendationForDisplay(rec:RecommendationResponse|null):RecommendationResponse|null{
  if(!rec)return null;
  const visible=new Set(displayedCandidates(rec));
  return {...rec,recommendations:rec.recommendations.filter(c=>visible.has(c)),unverified_candidates:rec.unverified_candidates.filter(c=>visible.has(c))};
}
import type {Candidate,RecommendationResponse} from '../contracts/recommendation-v1.js';

