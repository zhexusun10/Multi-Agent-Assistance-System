// /api/state already projects the server-approved visible range. The browser
// renders that complete list; applying another five-home limit hides appended homes.
export function publicRecommendationCandidates(rec){
  const seen=new Set();
  return [...(rec?.recommendations||[]),...(rec?.unverified_candidates||[])].filter(candidate=>{
    const id=candidate.listing.listing_id;
    if(seen.has(id))return false;
    seen.add(id);return true;
  });
}
