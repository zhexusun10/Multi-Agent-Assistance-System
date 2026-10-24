import {readFileSync} from 'node:fs';
const response=JSON.parse(readFileSync(new URL('../fixtures/recommendation-v2/teammate-round-3.response.json',import.meta.url),'utf8').replace(/^\uFEFF/,''));
export function refinementNative(active:boolean){
  const selection=structuredClone(response.selection);
  Object.assign(selection.cold_start,{active,evaluated:true,exit_conditions_met:!active,reason:active?'below_exit_thresholds':'exit_conditions_met'});
  return {contract:'2.0' as const,selection,pool:structuredClone(response.pool)};
}
