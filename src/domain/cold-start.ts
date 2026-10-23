import type {RecommendationResponse} from '../contracts/recommendation-v1.js';

/** Native recommendation cold-start exit state, kept by the orchestrator (migration 007) and sent as
 * the native request's `cold_start` (recommendation-owned extension of the main request contract). */
export type ColdStartState={exited:boolean;exited_pool_key:string|null};
export const NOT_EXITED:ColdStartState={exited:false,exited_pool_key:null};

/** Stored only when the ADOPTED native response evaluated cold start and met every exit condition;
 * legacy/baseline rounds and rounds that did not evaluate it never change the state. */
export function coldStartAfterAdoption(current:ColdStartState|null|undefined,rec:RecommendationResponse):ColdStartState|null{
  const selection=rec.native?.contract==='2.0'?rec.native.selection as {cold_start?:{evaluated?:unknown;exit_conditions_met?:unknown;pool_key?:unknown}}|null|undefined:null;
  const cs=selection?.cold_start,poolKey=typeof cs?.pool_key==='string'?cs.pool_key:typeof rec.native?.pool?.pool_key==='string'?rec.native.pool.pool_key as string:null;
  if(cs?.evaluated===true&&cs.exit_conditions_met===true&&poolKey)return {exited:true,exited_pool_key:poolKey};
  return current??null;
}
