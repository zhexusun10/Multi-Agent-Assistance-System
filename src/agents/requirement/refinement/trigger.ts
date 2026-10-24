import type {RecommendationResponse} from '../../../contracts/recommendation-v1.js';
import {nativeSelectionSchema} from '../../../contracts/recommendation-native.js';

/** Only the adopted native selection decides whether to ask. No application
 * threshold, score approximation or legacy mock fallback is allowed here. */
export function refinementTrigger(rec:RecommendationResponse|null|undefined){
  if(!rec||rec.status==='error'||rec.native?.contract!=='2.0')return null;
  const selection=nativeSelectionSchema.safeParse(rec.native.selection);
  if(!selection.success)return null;
  const signal=selection.data.cold_start;
  if(!signal.evaluated||!signal.active)return null;
  if(!signal.pool_key||signal.pool_key!==rec.native.pool?.pool_key)return null;
  return {trigger:'native_cold_start' as const,cold_start:signal};
}
