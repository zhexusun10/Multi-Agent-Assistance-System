const SELECTION_LABELS={ranked:"Selected in score order",leader:"Clear leader, retained directly",tie_group_sample:"Scores are close; sampled within the group",price_exploration_sample:"Selected through cold-start price-stratified exploration",small_pool:"All eligible homes returned because the pool fits the display limit"};
// Native response 2.0 scores (soft requirement fit / behaviour similarity / selection reason); archived
// contract 1.0 scores (explicit/implicit) and missing unverified lists stay readable.
export function rankingDescription(rec,id){
  const native=rec.native;if(!native)return null;
  const score=native.scores?.[id],selection=native.selection_reasons?.[id];
  const reason=selection?`Selection reason: ${SELECTION_LABELS[selection.selection_reason]||selection.selection_reason} (score rank in the full pool: ${selection.score_rank}).`:'';
  if(!score)return "This home has unverified requirements and no native score. Its returned order is preserved.";
  if(score.ordering==='stable_listing_id')return "No usable soft preferences or eligible behaviour signals were available. Native listing-ID order is preserved; a zero score does not mean the home is unsuitable."+reason;
  const parts=[`Native combined match score: ${score.combined_score.toFixed(4)}`];
  if('soft_score' in score){
    if(score.soft_score!==null)parts.push(`Soft-preference match: ${score.soft_score.toFixed(4)} × ${score.soft_weight}`);
    if(score.behavior_score!==null)parts.push(`Behaviour similarity: ${score.behavior_score.toFixed(4)} × ${score.behavior_weight}`);
    parts.push(`Soft-preference evidence coverage: ${(score.soft_evidence_coverage*100).toFixed(1)}%`);
  }else{
    if(score.explicit_score!==null)parts.push(`Explicit preferences: ${score.explicit_score.toFixed(4)} × ${score.explicit_weight}`);
    if(score.implicit_score!==null)parts.push(`Estimated viewing preferences: ${score.implicit_score.toFixed(4)} × ${score.implicit_weight}`);
    parts.push(`Native scoring evidence coverage: ${(score.evidence_coverage*100).toFixed(1)}%`);
  }
  if(score.suppressed_features?.length)parts.push("Explicit requirements take priority; these behaviour-similarity features are disabled: "+score.suppressed_features.join(", "));
  return parts.join("; ")+". This is a match score, not a probability of renting."+reason;
}

export function sampleDurationEvent(listingId,requestId,seconds){
  if(!Number.isFinite(seconds)||seconds<=0||seconds>3600)throw Error("Enter a duration greater than 0 and no more than 3600 seconds.");
  return {event_id:crypto.randomUUID(),listing_id:listingId,occurred_at:new Date().toISOString(),event_type:'active_view',payload:{source:'sample',recommendation_request_id:requestId,view_session_id:crypto.randomUUID(),active_duration_ms:seconds*1000}};
}
