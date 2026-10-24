import type { Candidate,Criterion,RecommendationResponse,Requirements } from '../contracts/recommendation-v1.js';
import {amenityLabels,valueLabels} from './field-capabilities.js';
import {displayedCandidates} from './selection.js';
import type {NativeScores,LegacyNativeScores} from '../contracts/recommendation-native.js';
type SelectionReason={selection_reason:string;selection_note:string;score_rank:number};
const selectionLabels:Record<string,string>={ranked:"Selected in score order",leader:"Clear leader, retained directly",tie_group_sample:"Scores are near-equal; sampled within the group",price_exploration_sample:"Selected through cold-start price-stratified exploration",small_pool:"All eligible homes returned because the pool fits the display limit"};
export function selectionText(reason:SelectionReason|undefined){return reason?`Selection reason: ${selectionLabels[reason.selection_reason]||reason.selection_reason} (score position in the full pool: ${reason.score_rank}).`:'';}
// Accepts contract 2.0 scores (soft/behaviour) and archived contract 1.0 scores (explicit/implicit).
export function nativeScoreText(score:NativeScores|LegacyNativeScores|undefined,unscored=false,selection?:SelectionReason){
  if(!score)return unscored?"This unverified home has no native score; the returned order is preserved.":"No native scoring information is available.";
  if(score.ordering==='stable_listing_id')return "No usable soft preferences or behaviour signals were available. Native listing-ID order is preserved; zero does not mean unsuitable."+selectionText(selection);
  const names:Record<string,string>={price:"Monthly rent",property_type:"Property type",bedrooms:"Bedroom count: ",floor_area_sqft:"Floor area",geography:"Geographic location"};
  const parts=[`Native combined match score: ${score.combined_score.toFixed(4)} (not a probability of renting)`];
  if('soft_score' in score){
    if(score.soft_score!==null)parts.push(`Soft-preference match: ${score.soft_score.toFixed(4)} × ${score.soft_weight}`);
    if(score.behavior_score!==null)parts.push(`Behaviour similarity: ${score.behavior_score.toFixed(4)} × ${score.behavior_weight}`+[score.appointment_score===null?'':`Viewing-intention similarity: ${score.appointment_score.toFixed(4)}`,score.browsing_score===null?'':`Cumulative viewing similarity: ${score.browsing_score.toFixed(4)}`].filter(Boolean).map(x=>` (${x})`).join(''));
    parts.push(`Soft-preference evidence coverage: ${score.soft_evidence_coverage.toFixed(4)}`);
  }else{
    if(score.explicit_score!==null)parts.push(`Explicit preferences: ${score.explicit_score.toFixed(4)} × ${score.explicit_weight}`);
    if(score.implicit_score!==null)parts.push(`Sample viewing interests: ${score.implicit_score.toFixed(4)} × ${score.implicit_weight}`);
    parts.push(`Native scoring evidence coverage: ${score.evidence_coverage.toFixed(4)}`);
  }
  if(score.suppressed_features.length)parts.push(`${'soft_score' in score?"No usable or distinguishing behaviour-comparison features":"Explicitly requested features are excluded from behaviour-similarity inference"}: ${score.suppressed_features.map(x=>names[x]||x).join(", ")}`);
  return parts.join("; ")+"."+selectionText(selection);
}
export const labels:Record<string,string>={price:"Monthly rent",bedrooms:"Bedrooms",bathrooms:"Bathrooms",floor_area_sqft:"Floor area",land_area_sqft:"Land area",district:"District",property_type:"Property type",place_proximity:"Distance to location",mrt_distance_m:"Property-reported MRT distance",mrt_walking_mins:"Property-reported walking minutes",furnishing:"Furnishing",rental_scope:"Whole unit / room",lease_months:"Lease duration",commute_time:"Commute time",current_availability:"Current availability",move_in_date:"Move-in date",amenities:"Indoor amenities"};
export function criterionText(c:Criterion){if(c.field==='amenities')return `${amenityLabels[String(c.value)]||c.value}${c.strength==='hard'?", must have":", soft preference"}`;if(c.field==='place_proximity'&&c.strength==='soft'&&c.distance_preference!=='bounded')return `Closer to ${c.target?.name||c.target?.category||c.target?.place_id} (closer is preferred; no hard radius)`;const op={eq:"equals",in:"is one of",gte:"At least",lte:"at most",between:"between"}[c.operator];const value=Array.isArray(c.value)?c.value.join(' — '):valueLabels[String(c.value)]||c.value;const target=c.target?[c.target.name||c.target.category||c.target.place_id,c.distance_type==='straight_line'?"straight-line distance":c.distance_type,c.transport_mode==='unspecified'?"transport mode unspecified":c.transport_mode==='transit'?"public transport":c.transport_mode].filter(Boolean).join("; "):null;return `${labels[c.field]||c.field} ${op} ${value}${c.unit?' '+c.unit:''}${target?" ("+target+")":''}${c.strength==='soft'?`, preference weight ${c.weight}`:", must have"}`;}
export function explainCandidate(item:Candidate,requirements:Requirements){
  return {listing_id:item.listing.listing_id,rank:item.rank,title:item.listing.title,
    facts:[item.listing.price===null?"Monthly rent unknown":`Recorded monthly rent: SGD ${item.listing.price}/month`,item.listing.bedrooms===null||item.listing.bedrooms<=0?"Bedroom count unknown":`${item.listing.bedrooms} bedrooms`,item.listing.district||"District unknown"],
    claims:item.criterion_results.map(r=>{const c=requirements.criteria.find(c=>c.criterion_id===r.criterion_id)!;const observed=r.observed_value===null?"Unknown":typeof r.observed_value==='number'?(Number.isInteger(r.observed_value)?r.observed_value:r.observed_value.toFixed(2)):String(r.observed_value);return {criterion_id:r.criterion_id,evidence_ids:r.evidence_ids,status:r.status,text:`${criterionText(c)}: ${r.status==='unknown'?"To confirm":c.field==='place_proximity'&&c.strength==='soft'?"Included in proximity ranking":r.status==='pass'?"Pass":"Not met"}; recorded value ${observed}${r.unit?' '+r.unit:''}${r.reason?"; "+r.reason:''}${c.field==='place_proximity'?", straight-line distance to the recorded place/area; actual walking route unverified":''}`,budget_margin:c.field==='price'&&c.operator==='lte'&&typeof r.observed_value==='number'?Number(c.value)-r.observed_value:null};}),
    score:item.score,score_meaning:"Explicit soft-preference match, not a probability of renting",unknowns:["Current availability unverified",...item.criterion_results.filter(r=>r.status==='unknown').map(r=>labels[requirements.criteria.find(c=>c.criterion_id===r.criterion_id)!.field]||r.criterion_id)],
  };
}
export function buildExplanation(req:Requirements,rec:RecommendationResponse,dataset:string,question=''){
  const describe=(c:Candidate)=>({...explainCandidate(c,req),...(rec.native?{native_scores:rec.native.scores[c.listing.listing_id]??null,score_meaning:nativeScoreText(rec.native.scores[c.listing.listing_id],rec.native.unscored_listing_ids.includes(c.listing.listing_id),rec.native.selection_reasons?.[c.listing.listing_id])}:{})});
  const visible=displayedCandidates(rec),verified=visible.filter(c=>rec.recommendations.includes(c)).map(describe),unverified=visible.filter(c=>rec.unverified_candidates.includes(c)).map(describe);
  const counts=rec.state.counts;
  // Structured evidence/document record only. The Explanation Agent writes chat prose.
  const summary=rec.status==='needs_clarification'?rec.clarifications.map(x=>x.question).join("; "):verified.length?"Available information satisfies the current hard requirements; confirm present availability.":unverified.length?"Some hard requirements remain unverified for these homes. Ask the agent for further details.":"No matching homes can be displayed for these conditions. Discuss which requirement to change.";
  return {summary,question,dataset,source_request_id:rec.request_id,requirements_version:rec.requirements_version,verified,unverified,
    warnings:rec.warnings.map(w=>w.message),coverage:rec.state.coverage,counts,relaxation_suggestions:rec.relaxation_suggestions,
    snapshot_notice:`Data source: ${dataset}. Historical records do not prove current availability. Straight-line, property-reported and actual route measurements are distinct.`,
    comparison:verified.length>=2?verified.slice(0,3).map(x=>({listing_id:x.listing_id,rank:x.rank,facts:x.facts,score:x.score,unknowns:x.unknowns})):[],
  };
}
export const escapeHtml=(s:unknown)=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
export function renderSummary(requirements:Requirements,rec:RecommendationResponse,dataset:string){
  const model=buildExplanation(requirements,rec,dataset);const lines=["# Singapore rental requirements and recommendations",'',`Data scope: ${dataset}`,`Requirements version: ${rec.requirements_version}; recommendation request: ${rec.request_id}`,'',model.snapshot_notice,'',"## Current requirements",...requirements.criteria.map(c=>`- ${criterionText(c)}`),'',"## Recommendations",model.summary];
  const visible=displayedCandidates(rec);
  for(const group of [{title:"Passes all hard requirements",items:visible.filter(c=>rec.recommendations.includes(c))},{title:"Homes needing confirmation (not verified matches)",items:visible.filter(c=>rec.unverified_candidates.includes(c))}]){if(!group.items.length)continue;lines.push('',`## ${group.title}`);for(const c of group.items){const e=explainCandidate(c,requirements);lines.push('',`### ${visible.indexOf(c)+1}. ${c.listing.title} (${c.listing.listing_id})`,...e.facts.map(x=>`- ${x}`),...e.claims.map(x=>`- ${x.text} [Evidence: ${x.evidence_ids.join(', ')}]`),"- Current availability: confirm with the agent",`- Source: ${c.listing.source_url||"Not provided"}`);}}
  lines.push('',"## Coverage and limitations",...model.warnings.map(x=>`- ${x}`),'',"## Next steps","- Confirm current availability, lease, furnishing and actual routes.","- Choose a home, verify the contact and review the enquiry/viewing draft. Local execution is simulated.",'',"## Evidence index",...rec.evidence.map(e=>`- ${e.evidence_id}: ${e.field_path}; source reference ${e.source_ref}; version ${e.dataset_version}`));
  const markdown=lines.join('\n');const html="<!doctype html><html lang=\"en-SG\"><meta charset=\"utf-8\"><title>Rental recommendations</title><style>body{font:16px/1.8 system-ui;max-width:900px;margin:48px auto;padding:24px;color:#182a32}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}h1{color:#096b64}</style><h1>Rental requirements and recommendations</h1><pre>"+escapeHtml(markdown)+'</pre></html>';
  return {markdown,html,model,template_version:'summary-v1.1'};
}
