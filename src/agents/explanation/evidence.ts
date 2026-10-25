import type {Criterion,RecommendationResponse,Requirements} from '../../contracts/recommendation-v1.js';
import {FIELD_RULES} from '../../contracts/recommendation-v1.js';
import {nativeResponseSchema,statedWeight,interestWeight,interestScore} from '../../contracts/recommendation-native.js';
import {legacyNativeResponseSchema} from '../../contracts/recommendation-native-legacy.js';
import {criterionText} from '../../domain/explanation.js';
import {displayedCandidates} from '../../domain/selection.js';
import type {ExplanationBundle,ExplanationFact} from './contracts.js';

const placeNames:Record<string,string>={'Nanyang Technological University':"Nanyang Technological University",'National University of Singapore':"National University of Singapore",CLEMENTI:"Clementi",'MRT或公交站':"MRT or bus stop",'超市':"Supermarket"};
const featureNames:Record<string,string>={price:"Monthly rent",property_type:"Property type",bedrooms:"Number of bedrooms",floor_area_sqft:"Floor area",geography:"Location"};
const numeric=(v:unknown):number[]=>typeof v==='number'&&Number.isFinite(v)?[v]:Array.isArray(v)?v.flatMap(numeric):[];
// Source-format aliases remain multilingual for exact provenance checks.
function dateLiterals(value:string):string[]{
  const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(value);if(!match)return [value];
  const [,year,month,day]=match,m=Number(month),d=Number(day),date=new Date(value+'T00:00:00Z');
  if(!Number.isFinite(date.getTime())||date.toISOString().slice(0,10)!==value)return [value];
  return [value,`${year}年${m}月${d}日`,`${year}年${month}月${day}日`,`${m}月${d}日`,
    ...[month,String(m)].flatMap(mm=>[day,String(d)].map(dd=>`${year}/${mm}/${dd}`)),
    ...['en-GB','en-US'].flatMap(locale=>(['long','short'] as const).map(format=>new Intl.DateTimeFormat(locale,{year:'numeric',month:format,day:'numeric',timeZone:'UTC'}).format(date)))];
}
export function makeExplanationBundle(req:Requirements,rec:RecommendationResponse,question:string,archive?:unknown):ExplanationBundle{
  const facts:ExplanationFact[]=[],required_refs:string[]=[];
  const add=(ref:string,kind:ExplanationFact['kind'],text:string,listing_ids:string[]=[],source_refs:string[]=[],numbers:number[]=[])=>{facts.push({ref,kind,text,listing_ids,source_refs,numbers});return ref;};
  const clean=(s:string)=>Object.entries(placeNames).reduce((t,[a,b])=>t.replaceAll(a,b),s);
  const desc=(c:Criterion)=>clean(criterionText(c)).replace(/(?:，偏好权重|, preference weight) [\d.]+/,", preferred where possible").replace('SGD/month',"SGD/month").replace('count'," rooms").replace('sqft'," sq ft");
  const nearer=(c:Criterion)=>c.field==='place_proximity'&&c.strength==='soft'&&c.distance_preference!=='bounded';
  const criterionNumbers=(c:Criterion)=>nearer(c)?[]:numeric(c.value);
  // A district/postcode/date is an exact string, never a measurable quantity.
  // Keep its full value for reference-scoped grounding instead of turning D05
  // into the numeric allowance 5 (which could incorrectly authorize a rent).
  const literalValues=(c:Criterion,value:unknown):string[]=>['string','date'].includes(FIELD_RULES[c.field]?.kind)
    ?(Array.isArray(value)?value:[value]).filter((v):v is string=>typeof v==='string'&&/\d/.test(v)).flatMap(v=>FIELD_RULES[c.field].kind==='date'?dateLiterals(v):[v]):[];
  const rows=displayedCandidates(rec),byId=new Map(req.criteria.map(c=>[c.criterion_id,c]));
  const ranked=rows.filter(r=>rec.recommendations.includes(r)),pending=rows.filter(r=>rec.unverified_candidates.includes(r));
  const rankedIds=ranked.map(r=>r.listing.listing_id);
  if(req.criteria.some(c=>c.field==='bathrooms')&&!req.criteria.some(c=>c.field==='amenities'&&c.value==='private_bathroom'))add('bathroom-count-not-privacy','limitation',"This bathroom requirement concerns count, not exclusive use. If unverified, say bathroom count needs confirmation, not private bathroom needs confirmation. 1B1B does not prove a private bathroom or mean the user requested one.");
  const lists=rows.map((item,index)=>{
    const id=item.listing.listing_id,refs:string[]=[],unverified=rec.unverified_candidates.includes(item);
    refs.push(add('listing:'+id,'listing',`No. ${index+1}: ${item.listing.title}.${unverified?"Some hard requirements lack evidence, so full compliance is unconfirmed. This home is supplementary, outside the selected ranking, even if some distances are known.":"Available information satisfies the current hard requirements and this home belongs to the selected ranking."}`,[id],[],[index+1]));
    if(item.listing.price!==null)refs.push(add('price:'+id,'listing',`Recorded monthly rent: ${item.listing.price} SGD; confirm the current price with the agent.`,[id],[],[item.listing.price]));
    // Zero is a source placeholder in this dataset, never assert a zero-bedroom home.
    if(item.listing.bedrooms!==null&&item.listing.bedrooms>0)refs.push(add('beds:'+id,'listing',`Bedroom count: ${item.listing.bedrooms}.`,[id],[],[item.listing.bedrooms]));
    if(item.listing.floor_area_sqft&&item.listing.floor_area_sqft>0)refs.push(add('area:'+id,'listing',`Floor area${item.listing.floor_area_sqft} sq ft.`,[id],[],[item.listing.floor_area_sqft]));
    for(const r of item.criterion_results){
      const c=byId.get(r.criterion_id);if(!c)throw Error('EXPLANATION_UNKNOWN_CRITERION');
      const observed=numeric(r.observed_value),distance=c.field==='place_proximity';
      let text=desc(c)+"; "+(r.status==='unknown'?"Not yet confirmed.":nearer(c)?"Used to compare proximity to the destination, without another qualifying radius.":r.status==='pass'?"Matches.":c.strength==='hard'?"Does not satisfy this hard requirement.":"Does not meet this preference.");
      if(r.status!=='unknown'&&r.observed_value!==null){
        const area=distance&&/^URA:/.test(c.target?.place_id||'');
        const value=distance&&typeof r.observed_value==='number'?area?(r.observed_value===0?"Inside this area: area distance is zero, not zero distance to a namesake station or the area centre":`Distance to the area boundary: ${Math.round(r.observed_value)} m (nearest straight-line distance; no walking time can be inferred)`):`${Math.round(r.observed_value)} m (straight-line distance; no walking time can be inferred)`:String(r.observed_value)+(r.unit==='SGD/month'?"SGD/month":r.unit||'');
        text+="Known record: "+value+".";
      }
      if(r.status==='unknown')text+="Missing data does not mean the amenity is absent and does not establish convenient transport or a satisfied condition.";
      const nums=[...criterionNumbers(c),...observed];
      if(typeof c.value==='number'&&typeof r.observed_value==='number'){
        nums.push(Math.abs(c.value-r.observed_value));
        if(c.field==='price'&&c.operator==='lte')text+=`Compared with the cap, recorded rent is ${r.observed_value>c.value?"higher by ":"lower by "}${Math.abs(c.value-r.observed_value)} SGD.`;
      }
      if(distance)nums.push(...nums.map(n=>n/1000));
      refs.push(add('condition:'+id+':'+c.criterion_id,r.status==='unknown'?'limitation':'condition',text,[id],r.evidence_ids,nums));
      facts.at(-1)!.literals=[...new Set([...literalValues(c,c.value),...(r.status==='unknown'?[]:literalValues(c,r.observed_value))])];
    }
    return {listing_id:id,position:index+1,title:item.listing.title,group:unverified?'hard_unknown' as const:'hard_pass' as const,ranking_status:unverified?'not_ranked' as const:'ranked' as const,fact_refs:refs};
  });
  // Provide the user's complete requirements once, separately from per-home
  // observations. Merge only the same destination's radius + nearer preference;
  // the native distance scale is not another user-requested radius.
  const destination=(c:Criterion)=>c.target?.place_id||c.target?.category||c.target?.name;
  const sameDestination=(a:Criterion,b:Criterion)=>a.field==='place_proximity'&&b.field==='place_proximity'&&a.distance_type===b.distance_type&&a.transport_mode===b.transport_mode&&destination(a)===destination(b);
  const grouped=new Map<string,Criterion[]>();
  for(const c of req.criteria){
    const bounded=nearer(c)?req.criteria.find(x=>!nearer(x)&&sameDestination(c,x)):undefined;
    const key=bounded?.criterion_id||c.criterion_id;
    grouped.set(key,[...(grouped.get(key)||[]),c]);
  }
  const requirements=[...grouped.entries()].map(([id,criteria])=>{
    const primary=criteria.find(c=>!nearer(c))||criteria[0];
    const label=desc(primary)+(criteria.some(nearer)&&!nearer(primary)?"; closer to the destination is preferred within this range":'');
    const numbers=criteria.flatMap(criterionNumbers);if(primary.field==='place_proximity')numbers.push(...numbers.map(n=>n/1000));
    const ref=add('requirement:'+id,'requirement',label,[],[],numbers);
    facts.at(-1)!.literals=[...new Set(criteria.flatMap(c=>literalValues(c,c.value)))];
    return {ref,label,strength:primary.strength,comparison_only:nearer(primary),criterion_ids:criteria.map(c=>c.criterion_id),results:rows.map((row,index)=>({
      listing_id:row.listing.listing_id,
      position:index+1,title:row.listing.title,
      status:row.criterion_results.find(r=>r.criterion_id===primary.criterion_id)?.status||'unknown' as const,
      observed_value:row.criterion_results.find(r=>r.criterion_id===primary.criterion_id)?.observed_value??null,
      unit:primary.unit??null,
      fact_refs:criteria.map(c=>'condition:'+row.listing.listing_id+':'+c.criterion_id).filter(ref=>facts.some(f=>f.ref===ref)),
    }))};
  });
  const missing=requirements.flatMap(r=>{
    const unknown=r.results.filter(x=>x.status==='unknown');
    return unknown.length?[{requirement:r,unknown}]:[];
  });
  if(missing.length){
    const refs=missing.flatMap(x=>x.unknown.flatMap(r=>r.fact_refs));
    add('coverage:missing','limitation',"Missing evidence by condition (each condition may involve a different set of homes; do not merge the groups): "+missing.map(x=>x.requirement.label+": only "+x.unknown.map(r=>r.title).join(", ")+"not yet confirmed").join("; ")+". A home outside a condition’s missing-data list must not be described as lacking that record.",[...new Set(missing.flatMap(x=>x.unknown.map(r=>r.listing_id)))]);
    facts.at(-1)!.derived_from=refs;
  }
  let native;
  if(archive){
    const response=(archive as any).response;
    native=response?.contract_version==='1.0'?legacyNativeResponseSchema.parse(response):nativeResponseSchema.parse(response);
    if(native.request_id!==rec.request_id||native.conversation_id!==rec.conversation_id||native.requirements_version!==rec.requirements_version+1)throw Error('EXPLANATION_ARCHIVE_IDENTITY');
    // Native rationale is a list of criterion IDs. Translate it against accepted facts, never render raw IDs.
    for(const item of native.recommendations){
      if(!rows.some(r=>r.listing.listing_id===item.listing.listing_id))continue;
      const source=[...item.strengths,...item.trade_offs,...item.unknowns].filter(id=>byId.has(id));
      const matching=facts.filter(f=>source.some(id=>f.ref==='condition:'+item.listing.listing_id+':'+id));
      if(matching.length)add('native-rationale:'+item.listing.listing_id,'listing',"The recommender’s matches, compromises and unknowns are linked to this home’s individual conditions.",[item.listing.listing_id],matching.flatMap(f=>f.source_refs));
    }
  }
  const scores=rec.native?ranked.flatMap(r=>rec.native!.scores[r.listing.listing_id]?[rec.native!.scores[r.listing.listing_id]]:[]):[],active=new Set<string>(),behavior=new Set<string>();
  for(const score of scores){
    for(const c of score.contributions){
      if(c.unknown||c.similarity===null||c.weight<=0)continue;
      if(c.reference===null&&statedWeight(score)>0&&byId.get(c.feature)?.strength==='soft')active.add(c.feature);
      if(c.reference!==null&&interestWeight(score)>0&&featureNames[c.feature]&&!score.suppressed_features.includes(c.feature))behavior.add(c.feature);
    }
  }
  if(!rec.native)for(const row of rows)for(const b of row.score_breakdown)if(b.origin==='stated_preference'&&row.criterion_results.find(r=>r.criterion_id===b.criterion_id)?.status!=='unknown')active.add(b.criterion_id);
  const modes=[...new Set(scores.map(s=>s.ordering))],mode=rec.native?(modes.length===1?modes[0]:modes.length?'mixed':'unscored'):'baseline';
  const activeText=[...active].map(id=>desc(byId.get(id)!));
  const source=(archive as any)?.request?.feedback as {source?:string}[]|undefined;
  const sampleOnly=source?.length&&source.every(x=>x.source==='sample');
  const v2=rec.native?.contract==='2.0',selection=v2?rec.native?.selection:undefined;
  // Describe the DISPLAYED listings: native returns up to ten and the first five are shown, so a sampled
  // list whose visible prefix holds only leaders is plain score order.
  const shownReason=(reason:string)=>rows.some(r=>rec.native?.selection_reasons?.[r.listing.listing_id]?.selection_reason===reason);
  const explored=selection?.mode==='cold_start_exploration'&&shownReason('price_exploration_sample');
  const tieSampled=selection?.mode==='near_tie_sampling'&&shownReason('tie_group_sample');
  const exploratory=explored||tieSampled;
  const appointment=scores.some(s=>'appointment_score' in s&&s.appointment_score!==null&&s.appointment_weight>0);
  const browsing=scores.some(s=>'browsing_score' in s&&s.browsing_score!==null&&s.browsing_weight>0);
  const behaviorSource=v2?[...(browsing?["previous viewing activity"]:[]),...(appointment?["saved viewing intentions"]:[])].join(" and ")||"existing behaviour signals":sampleOnly?"viewing samples supplied for this round":"previous viewing activity";
  let rankText=rows.length?(ranked.length?(pending.length?"Some homes have evidence for the hard requirements; others have incomplete data. Do not claim every home satisfies all requirements.":"Available information for these homes satisfies the current hard requirements."):"Some hard requirements lack evidence for these homes, so full compliance remains unconfirmed."):"No homes can be displayed for this request; this does not mean none exist in the market.";
  // Selection is already described by the requirement/result pairs. Keep this
  // fact about the actual order, so it does not prime repetitive disclaimers.
  if(activeText.length)rankText+="The ranking preferences with comparable data are: "+activeText.join("; ")+".";
  if(active.size===1&&!behavior.size&&!exploratory)rankText+="Among scored homes with comparable data, this is the only effective ranking preference. Equal matches retain a stable order without proving one is better.";
  if(active.size>1)rankText+=exploratory?"These preferences jointly determine the match score; the final list also follows the selection policy described below.":"These preferences jointly determine the order; do not reduce it to only one factor.";
  const filterOnly=req.criteria.filter(c=>c.strength==='hard');
  if(filterOnly.length)rankText+="Conditions used only as filters, without ranking rewards: "+filterOnly.map(desc).join("; ")+". Do not add lower rent as a ranking cause if it was not used.";
  if(behavior.size)rankText+=`${activeText.length?"Also considers ":"Ranking considers "}${behaviorSource} similarities in ${[...behavior].map(x=>featureNames[x]).join(", ")}; these are interest signals, not explicit user requirements or confirmed bookings.`;
  if(active.size&&behavior.size&&scores.every(s=>statedWeight(s)>interestWeight(s)))rankText+="Explicit preferences have the main role; viewing interests provide secondary evidence.";
  if(!active.size&&!behavior.size&&rows.length)rankText+="Current data cannot reliably distinguish suitability from the user’s preferences. The order is for browsing; say this once without separately repeating that rent is not a ranking cause.";
  const rankedRows=rows.filter(r=>rec.native?.scores[r.listing.listing_id]);
  if(rankedRows.length>1&&rankedRows.every(r=>Math.abs(r.score-rankedRows[0].score)<1e-12))rankText+=exploratory?"Current match scores do not distinguish the homes; earlier display does not mean clearly better.":"The displayed scored homes are not distinguished by their ranking evidence. A stable order is retained, not proof the first is better.";
  if(tieSampled)rankText+="Homes with very close matches are sampled. Display position does not establish superiority over later homes.";
  if(explored)rankText+="To help compare price levels, some strong matches are retained and supplementary homes are drawn from price strata. Display order is not pure score rank or a guarantee the first is best. A saved viewing draft is an interest signal, not a booking.";
  const unscored=(rec.native?.unscored_listing_ids||[]).filter(id=>rows.some(r=>r.listing.listing_id===id));
  if(rankedIds.length)rankText+="The selected-ranking comparison includes only: "+ranked.map(r=>r.listing.title).join(", ")+".";
  if(pending.length)rankText+="Supplementary displayed homes are outside this ranking. They may still have known school/supermarket distances for comparison, without satisfying every hard requirement or participating in ranking. A shorter supplementary distance does not invalidate the selected ranking.";
  add('ranking:method','ranking',rankText,rows.map(r=>r.listing.listing_id),rec.native?[rec.native.exchange_ref]:[],[...active].flatMap(id=>{const c=byId.get(id)!;return c.field==='place_proximity'?[...criterionNumbers(c),...criterionNumbers(c).map(n=>n/1000)]:criterionNumbers(c);}));
  if(v2)for(const row of rows){
    const id=row.listing.listing_id,picked=rec.native?.selection_reasons?.[id];if(!picked)continue;
    const reason=({ranked:"Selected in native match-score order.",leader:"Retained as a leading match.",tie_group_sample:"Sampled from near-equal matches, without implying greater suitability than its peers.",price_exploration_sample:"Included as a supplementary price-level choice to understand trade-offs, not simply because its score is higher.",small_pool:"All eligible homes fit within the display limit, so all were returned."})[picked.selection_reason];
    add(`selection:${id}`,'ranking',`${row.listing.title}: ${reason}Display numbers may differ from score positions in the full candidate pool.`,[id],[rec.native!.exchange_ref]);
  }
  required_refs.push('ranking:method');
  if(behavior.size){add('ranking:behavior','ranking',`This round also used ${behaviorSource} from ${[...behavior].map(x=>featureNames[x]).join(", ")}as interest signals. Mention this briefly when explaining ranking rather than discussing only explicit preferences.`);required_refs.push('ranking:behavior');}
  if(rows.length){add('availability:current','availability',"These records do not establish current availability. Confirm present availability and price with the agent.");required_refs.push('availability:current');}
  if(!rows.length)add('results:empty','limitation',rec.status==='needs_clarification'?"First provide: "+rec.clarifications.map(x=>x.question).join("; "):"No homes can be displayed for these conditions. Discuss which condition the user would change; do not automatically relax requirements or claim the entire market has none.");
  for(let a=0;a<rows.length;a++)for(let b=a+1;b<rows.length;b++){
    const x=rows[a],y=rows[b];
    if(x.listing.price!==null&&y.listing.price!==null){
      const ref=add(`compare:${a+1}:${b+1}`,'comparison',`No. ${a+1} recorded monthly rent is ${x.listing.price} SGD; No. ${b+1} is ${y.listing.price} SGD; compared with the first, the latter is ${y.listing.price>=x.listing.price?"higher by ":"lower by "}${Math.abs(y.listing.price-x.listing.price)} SGD; a price difference alone is not a ranking cause.`,[x.listing.listing_id,y.listing.listing_id],[],[a+1,b+1,x.listing.price,y.listing.price,Math.abs(y.listing.price-x.listing.price)]);
      facts.find(f=>f.ref===ref)!.derived_from=[`price:${x.listing.listing_id}`,`price:${y.listing.listing_id}`,...req.criteria.filter(c=>c.field==='price').flatMap(c=>[x,y].filter(row=>row.criterion_results.find(r=>r.criterion_id===c.criterion_id)?.observed_value===row.listing.price).map(row=>`condition:${row.listing.listing_id}:${c.criterion_id}`))];
    }
    const sx=rec.native?.scores[x.listing.listing_id],sy=rec.native?.scores[y.listing.listing_id];
    if(sx&&sy&&rankedIds.includes(x.listing.listing_id)&&rankedIds.includes(y.listing.listing_id)){
      const factors=[...active].flatMap(id=>{
        const cx=sx.contributions.find(v=>v.feature===id&&v.reference===null&&!v.unknown&&v.similarity!==null),cy=sy.contributions.find(v=>v.feature===id&&v.reference===null&&!v.unknown&&v.similarity!==null);
        if(!cx||!cy)return [];
        const direction=cx.contribution===cy.contribution?"Both homes have the same ranking contribution from this factor":cx.contribution>cy.contribution?`This factor favours home No. ${a+1} `:`This factor favours home No. ${b+1} `;
        return [desc(byId.get(id)!)+':'+direction];
      });
      if(factors.length)add(`compare-ranking:${a+1}:${b+1}`,'comparison',`Within the selected ranking only: home No. ${a+1} ${x.listing.title} precedes No. ${b+1} ${y.listing.title}. The recommender’s actual per-factor contributions show: ${factors.join("; ")}. Trade-offs are allowed; an earlier home need not lead every factor. Do not extend these comparisons to supplementary homes outside the ranking.`,[x.listing.listing_id,y.listing.listing_id],rec.native?[rec.native.exchange_ref]:[],[a+1,b+1]);
    }
    const usedBehavior=[sx,sy].map(score=>score?.contributions.filter(c=>c.reference!==null&&!c.unknown&&c.similarity!==null&&c.weight>0&&featureNames[c.feature]&&!score.suppressed_features.includes(c.feature)).map(c=>c.feature)||[]);
    if(behavior.size&&sx&&sy&&usedBehavior.every(features=>features.length>0)&&interestWeight(sx)>0&&interestWeight(sy)>0&&interestScore(sx)!==null&&interestScore(sy)!==null){
      const ix=interestScore(sx)!,iy=interestScore(sy)!;const matching=ix===iy?"The two homes have equal overall viewing-interest similarity":ix>iy?`No. ${a+1} ${x.listing.title} has greater overall similarity than No. ${b+1} ${y.listing.title} to the viewing-interest signals used`:`No. ${b+1} ${y.listing.title} has greater overall similarity than No. ${a+1} ${x.listing.title} to the viewing-interest signals used`;
      const features=[...new Set(usedBehavior.flat())].map(key=>featureNames[key]).join(", ");
      add(`compare-interest:${a+1}:${b+1}`,'comparison',`The recommender’s actual viewing-interest comparison: ${matching}. This is the overall match from ${features} and other participating features. It does not establish similarity on every feature or a user preference for a specific size/type. Do not recalculate or change the original order.`,[x.listing.listing_id,y.listing.listing_id],rec.native?[rec.native.exchange_ref]:[],[a+1,b+1]);
    }
    for(const c of req.criteria.filter(c=>c.field==='place_proximity')){
      const rx=x.criterion_results.find(r=>r.criterion_id===c.criterion_id),ry=y.criterion_results.find(r=>r.criterion_id===c.criterion_id);
      if(!rx||!ry||rx.status==='unknown'||ry.status==='unknown'||typeof rx.observed_value!=='number'||typeof ry.observed_value!=='number')continue;
      const difference=Math.abs(rx.observed_value-ry.observed_value),roundedDifference=Math.abs(Math.round(rx.observed_value)-Math.round(ry.observed_value));
      const direction=rx.observed_value===ry.observed_value?"Both homes have the same straight-line distance":rx.observed_value<ry.observed_value?`No. ${a+1} is closer; No. ${b+1} is farther`:`No. ${b+1} is closer; No. ${a+1} is farther`;
      const ref=add(`compare-distance:${a+1}:${b+1}:${c.criterion_id}`,'comparison',`Distance to ${clean(c.target?.name||c.target?.category||"this place")} straight-line distance: No. ${a+1} ${x.listing.title} is ${rx.observed_value} m; No. ${b+1} ${y.listing.title} is ${ry.observed_value} m.${direction}; the distance differs by ${Number(difference.toFixed(2))} m. This comparison does not change recommendation order or establish actual walking/commute differences.`,[x.listing.listing_id,y.listing.listing_id],[...rx.evidence_ids,...ry.evidence_ids],[a+1,b+1,rx.observed_value,ry.observed_value,difference,roundedDifference,difference/1000,roundedDifference/1000]);
      facts.find(f=>f.ref===ref)!.derived_from=[`condition:${x.listing.listing_id}:${c.criterion_id}`,`condition:${y.listing.listing_id}:${c.criterion_id}`];
    }
  }
  return {question,requirements,listings:lists,facts,required_refs,ranking:{mode,active_criteria:[...active],behavior_features:[...behavior],ranked_listing_ids:rankedIds,filter_only_criteria:filterOnly.map(c=>c.criterion_id),unscored_listing_ids:unscored}};
}
