import {readFileSync} from 'node:fs';
const response=JSON.parse(readFileSync(new URL('../fixtures/recommendation-v2/teammate-round-3.response.json',import.meta.url),'utf8').replace(/^\uFEFF/,''));
// The teammate file keeps its stored legacy C/B/S cold_start; the default signal replaces it with a fresh mpd-v1
// object (pool 88 > window 30). The strict union rejects a mixed object, so never merge one shape into the other.
export function refinementNative(active:boolean,shape:'mpd'|'legacy'='mpd'){
  const selection=structuredClone(response.selection),pool_key=selection.cold_start.pool_key;
  selection.cold_start=shape==='legacy'
    ?{...selection.cold_start,active,evaluated:true,exit_conditions_met:!active,reason:active?'b_above_max':'exit_conditions_met'}
    :{evaluated:true,active,reason:active?'mean_difference_below_threshold':'mean_difference_at_or_above_threshold',detector:'mean_pairwise_difference',detector_version:'mpd-v1',candidate_window:30,evaluated_count:30,mean_pairwise_difference:active?0.004:0.05,threshold:0.01,pool_key};
  return {contract:'2.0' as const,selection,pool:structuredClone(response.pool)};
}
