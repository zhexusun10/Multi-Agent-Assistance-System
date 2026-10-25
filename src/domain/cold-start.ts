import type {RecommendationResponse} from '../contracts/recommendation-v1.js';

/** Former native cold-start exit state (migration 007), still sent as the native request's `cold_start`
 * (recommendation-owned extension of the main request contract). Since the mpd-v1 detector (2026-10-24)
 * native decides every round from the current score spread D and ignores this field; the stored
 * column and its historical values are kept as they are. */
export type ColdStartState={exited:boolean;exited_pool_key:string|null};
export const NOT_EXITED:ColdStartState={exited:false,exited_pool_key:null};

/** No new exit is recorded (mpd-v1 has no remembered exit; a stored legacy `exit_conditions_met`
 * response does not create one either): the stored value is carried forward unchanged. */
export function coldStartAfterAdoption(current:ColdStartState|null|undefined,_rec:RecommendationResponse):ColdStartState|null{
  return current??null;
}
