import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import type { AgentId } from '../types.js';
export const AGENT_PROFILES:Record<AgentId,{name:string;tools:string[];skill:string|null}>={
  a:{name:'Requirement',tools:['resolve_place','capabilities'],skill:'rental-requirement-intake'},
  b:{name:'Recommendation adapter',tools:['listings','listing_detail','places','capabilities'],skill:null},
  c:{name:'Explanation',tools:['listing_detail','listing_context','knowledge'],skill:'evidence-based-explanation'},
  d:{name:'Follow-up',tools:['listing_detail','knowledge','prepare_message','prepare_viewing','prepare_summary'],skill:'salesperson-followup'},
};
const cache=new Map<AgentId,{instructions:string;sha256:string}>();
export async function loadProfile(id:AgentId){
  const found=cache.get(id);if(found)return found;
  const name=AGENT_PROFILES[id].skill;const instructions=name?await readFile(new URL(`../../skills/${name}/SKILL.md`,import.meta.url),'utf8'):'Recommendation agent implementation belongs to the external teammate. Local baseline is explicitly labelled; full mode assesses the verified complete canonical snapshot.';
  if(instructions.length>12000)throw Error('Skill exceeds prompt budget');
  const result={instructions,sha256:createHash('sha256').update(instructions).digest('hex')};cache.set(id,result);return result;
}
