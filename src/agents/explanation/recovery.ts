import type {Requirements,RecommendationResponse} from '../../contracts/recommendation-v1.js';
import type {ExplanationBundle,ExplanationOutput,ExplanationFact} from './contracts.js';
import {displayedCandidates} from '../../domain/selection.js';
import {validateExplanation} from './validation.js';

/** A failed writer never becomes a factual source. Compose a short, independently
 * validated answer from accepted facts only; keep the original failure in storage. */
export function recoverExplanation(bundle:ExplanationBundle,req:Requirements,rec:RecommendationResponse):ExplanationOutput{
  const english=true,g=bundle.decision_support!; // English presentation; source facts remain unchanged.
  const facts=new Map(bundle.facts.map(f=>[f.ref,f])),rows=displayedCandidates(rec);
  const placeName=(name:string|undefined)=>({'MRT或公交站':'MRT or bus stop','最近的MRT或公交站':'Nearest MRT or bus stop','超市':'Supermarket','CLEMENTI':'Clementi'} as Record<string,string>)[name||'']||name||'target destination';
  const paragraphs:ExplanationOutput['paragraphs']=[];
  if(!g.target_listing_ids.length)return validateExplanation({version:'2.0',focus_listing_ids:[],paragraphs:[{listing_id:null,text:english?'There are no homes to display for these conditions. Which condition would you like to adjust?':'There are no homes to display for these conditions. Which condition would you like to adjust?',support_refs:['results:empty']}]},bundle);
  paragraphs.push({listing_id:null,text:english?'These details may help you choose between the homes:':'These details may help you choose between the homes:',support_refs:['guide:purpose']});
  for(const id of g.target_listing_ids){
    const profile=g.profiles.find(p=>p.listing_id===id)!,row=rows.find(r=>r.listing.listing_id===id)!;
    const refs=new Set<string>(profile.core_refs),parts:string[]=[];
    const price=profile.core_refs?.map(r=>facts.get(r)!).find(f=>f.ref===`price:${id}`||f.ref.endsWith(':price'));
    const area=profile.core_refs?.map(r=>facts.get(r)!).find(f=>f.ref===`area:${id}`||f.ref.endsWith(':floor_area_sqft'));
    const highlights=profile.highlight_refs.map(r=>facts.get(r)!);
    const relative=(field:string)=>{
      const f=highlights.find(f=>f.cohort?.field===field);if(!f)return '';
      refs.add(f.ref);const c=f.cohort!,scope=c.complete?(english?'in this group':'in this group'):(english?'among homes with available data':'among homes with available data');
      const position=c.clustered?(english?'similar':'similar'):({lowest:english?'the lowest':'the lowest',tied_lowest:english?'joint lowest':'joint lowest',highest:english?'the highest':'the highest',tied_highest:english?'joint highest':'joint highest',lower:english?'on the lower side':'on the lower side',higher:english?'on the higher side':'on the higher side',middle:english?'in the middle':'in the middle',similar:english?'similar':'similar'})[c.position];
      const words=field==='floor_area_sqft'?position.replace(/lowest/g,'smallest').replace(/highest/g,'largest').replace(/lower/g,'smaller').replace(/higher/g,'larger'):position;
      return english?`, ${words} ${scope}`:`, ${scope}${words}`;
    };
    const pricePosition=highlights.find(f=>f.cohort?.field==='price')?.cohort;
    const areaPosition=highlights.find(f=>f.cohort?.field==='floor_area_sqft')?.cohort;
    const rent=price?(english?`At S$${price.numbers[0].toLocaleString('en-SG')} a month${relative('price')}`:`At S$${price.numbers[0].toLocaleString('en-SG')} a month${relative('price')}`)+
      (pricePosition&&!pricePosition.clustered&&['lowest','tied_lowest','lower'].includes(pricePosition.position)?(english?', leaving more of your budget for other needs.':', leaving more of your budget for other needs.'):(english?'.':".")):'';
    const space=area?(english?`The listed area is ${area.numbers[0]} sq ft${relative('floor_area_sqft')}`:`The listed area is ${area.numbers[0]} sq ft${relative('floor_area_sqft')}`)+
      (areaPosition&&!areaPosition.clustered?(english?', worth comparing with the layout and the space you need.':', worth comparing with the layout and the space you need.'):(english?'.':".")):'';
    const areaFirst=areaPosition&&!areaPosition.clustered&&['highest','tied_highest'].includes(areaPosition.position);
    parts.push(...(areaFirst?[space,rent]:[rent,space]).filter(Boolean));
    const describedDistances=new Set<string>();
    for(const r of row.criterion_results){
      const c=req.criteria.find(c=>c.criterion_id===r.criterion_id);
      if(c?.field!=='place_proximity'||r.status==='unknown'||typeof r.observed_value!=='number')continue;
      const name=c.target?.name==='CLEMENTI'?"Clementi":c.target?.name||c.target?.category||"target destination",region=/^URA:/.test(c.target?.place_id||'');
      // Radius filtering and nearer ranking may cite the same measurement.
      // Retain both condition references, but describe that distance only once.
      // Different destinations, metrics, modes or values remain separate facts.
      refs.add(`condition:${id}:${r.criterion_id}`);
      const target=c.target?.place_id?['place',c.target.place_id]:c.target?.category?['category',c.target.category]:['name',c.target?.name];
      const measurement=JSON.stringify([target,c.distance_type,c.transport_mode,r.unit??c.unit,r.observed_value]);
      if(describedDistances.has(measurement))continue;
      describedDistances.add(measurement);
      parts.push(english?(region&&r.observed_value===0?`It is within ${name}.`:`It is about ${Math.round(r.observed_value).toLocaleString('en-SG')} metres in a straight line from ${region?'the boundary of ':''}${name}.`):(region&&r.observed_value===0?`It is within ${name}.`:`It is about ${Math.round(r.observed_value).toLocaleString('en-SG')} metres in a straight line from ${region?'the boundary of ':''}${name}.`));
    }
    const describe=(f:ExplanationFact)=>{
      refs.add(f.ref);
      if(f.cohort){
        const c=f.cohort,low=['lowest','tied_lowest','lower'].includes(c.position),high=['highest','tied_highest','higher'].includes(c.position);
        const scope=c.complete?(english?'in this group':'in this group'):(english?'among homes with available data':'among homes with available data');
        if(c.field==='rent_per_sqft')return english?`At about S$${c.value} per sq ft per month, the unit rent is ${low?'lower':'higher'} ${scope}; the same space ${low?'costs less':'costs more'} in rent.`:`At about S$${c.value} per sq ft per month, the unit rent is ${low?'lower':'higher'} ${scope}; the same space ${low?'costs less':'costs more'} in rent.`;
        const label=c.field==='price'?(english?'rent':'rent'):(english?'listed area':'listed area');
        const position=c.clustered?(english?'similar':'similar'):c.position==='tied_lowest'?(english?'joint lowest':'joint lowest'):c.position==='tied_highest'?(english?'joint highest':'joint highest'):c.position==='lowest'?(english?'lowest':'lowest'):c.position==='highest'?(english?'highest':'highest'):low?(english?'lower':'lower'):high?(english?'higher':'higher'):(english?'in the middle':'in the middle');
        return english?`Its ${label} is ${position} ${scope}.`:`Its ${label} is ${position} ${scope}.`;
      }
      if(f.ref.includes(':nearby:'))return english?`${f.literals?.[0]} is about ${f.numbers[0]} metres away${f.distance_metric==='straight_line'?' in a straight line':' according to the property information'}; check the actual route.`:`${f.literals?.[0]} is about ${f.numbers[0]} metres away${f.distance_metric==='straight_line'?' in a straight line':' according to the property information'}; check the actual route.`;
      if(f.ref.endsWith(':bathrooms'))return english?`The information lists ${f.numbers[0]} bathrooms; this does not establish private use.`:`The information lists ${f.numbers[0]} bathrooms; this does not establish private use.`;
      if(f.ref.startsWith('beds:')||f.ref.endsWith(':bedrooms'))return english?`The information lists ${f.numbers[0]} bedrooms.`:`The information lists ${f.numbers[0]} bedrooms.`;
      if(f.ref.endsWith(':type'))return english?`The recorded property type is ${row.listing.property_type}.`:f.text.split('；')[0]+".";
      if(f.ref.endsWith(':build_year'))return english?`The recorded construction year is ${f.numbers[0]}.`:`The recorded construction year is ${f.numbers[0]}.`;
      if(f.ref.endsWith(':floor_level'))return f.text;
      return '';
    };
    // A useful introduction should retain several concrete neighbourhood
    // choices, instead of falling back to the first (often build-year) field.
    const normalize=(s:string)=>s.toLowerCase().replace(/\b(?:mrt|station)\b/g,'').replace(/[^\p{L}\p{N}]/gu,'');
    const stationTargets=req.criteria.filter(c=>c.field==='place_proximity'&&/MRT|STATION/i.test(c.target?.name||'')).map(c=>normalize(c.target!.name!));
    const nearby=profile.extra_refs.filter(r=>r.includes(':nearby:')).map(r=>facts.get(r)!).filter(f=>!f.ref.endsWith(':MRT')||!stationTargets.includes(normalize(f.literals?.[0]||''))).slice(0,3);
    for(const f of nearby)refs.add(f.ref);
    if(nearby.length){
      const places=nearby.map(f=>`${f.literals?.[0]}${english?` about ${f.numbers[0]} metres${f.distance_metric==='straight_line'?' in a straight line':' in the property information'}`:` about ${f.numbers[0]} metres${f.distance_metric==='straight_line'?' in a straight line':' in the property information'}`}`);
      const hasTransport=nearby.some(f=>/:nearby:(MRT|BusStop|BusStation|PublicTransport)$/.test(f.ref));
      const hasShopping=nearby.some(f=>/:nearby:(Supermarket|Market|Mall|FoodCourt)$/.test(f.ref));
      parts.push(english?`Nearby options include ${places.join('; ')}. ${hasShopping?'These give you more options for groceries or meals. ':''}${hasTransport?'They can help you consider public transport; check the actual route.':''}`:`Nearby options include ${places.join('; ')}. ${hasShopping?'These give you more options for groceries or meals. ':''}${hasTransport?'They can help you consider public transport; check the actual route.':''}`);
    }
    if(profile.additional_refs.length&&!profile.additional_refs.some(r=>refs.has(r))){
      const extra=profile.additional_refs.find(r=>!r.endsWith(':area-unit')&&!r.includes(':nearby:'));
      if(extra){const sentence=describe(facts.get(extra)!);if(sentence)parts.push(sentence);}
    }else if(!nearby.length){
      const extra=profile.extra_refs.find(r=>r.endsWith(':bathrooms'))||profile.extra_refs.find(r=>r.endsWith(':type'));
      if(extra&&!refs.has(extra)){const sentence=describe(facts.get(extra)!);if(sentence)parts.push(sentence);}
    }
    const unknown=row.criterion_results.filter(r=>r.status==='unknown').map(r=>{
      const c=req.criteria.find(c=>c.criterion_id===r.criterion_id);if(!c)return null;
      const label=c.field==='place_proximity'?`Distance to ${placeName(c.target?.name)}`:({bedrooms:"Number of bedrooms",bathrooms:"Bathroom count",floor_area_sqft:"Floor area",amenities:"Amenity information",price:"Monthly rent"} as Record<string,string>)[c.field]||"Some requirements";
      refs.add(`condition:${id}:${r.criterion_id}`);return label;
    }).filter(Boolean);
    for(const ref of profile.caution_refs)refs.add(ref);
    if(unknown.length)parts.push(`${[...new Set(unknown)].join(', ')} still lack sufficient data; please confirm these requirements before deciding.`);
    else if(profile.caution_refs.length)parts.push(english?'Some information is incomplete or conflicting; please check it with the agent.':'Some information is incomplete or conflicting; please check it with the agent.');
    const selectionRef=`selection:${id}`;
    if(g.explain_order&&facts.has(selectionRef)){
      refs.add(selectionRef);const reason=rec.native?.selection_reasons?.[id]?.selection_reason;
      if(reason==='price_exploration_sample')parts.push(english?'It was included to offer a different price option, rather than because it had the highest match score.':'It was included to offer a different price option, rather than because it had the highest match score.');
      else if(reason==='tie_group_sample')parts.push(english?'It was sampled from homes with similar matches; its position does not establish it as the better choice.':'It was sampled from homes with similar matches; its position does not establish it as the better choice.');
      else if(reason==='small_pool')parts.push(english?'All eligible homes fit within the display limit, so all were returned.':'All eligible homes fit within the display limit, so all were returned.');
      else parts.push(english?'It was retained based on the recommendation match results.':'It was retained based on the recommendation match results.');
    }
    if(!refs.size){refs.add(`listing:${id}`);parts.push(english?'There is limited information for this home; ask the agent for details.':'There is limited information for this home; ask the agent for details.');}
    paragraphs.push({listing_id:id,text:(english?`${profile.position}. ${profile.title}: `:`${profile.position}. ${profile.title}: `)+parts.join(english?' ':''),support_refs:[...refs]});
  }
  paragraphs.push({listing_id:null,text:english?'Please confirm current rent and availability with the agent.':'Please confirm current rent and availability with the agent.',support_refs:['availability:current']});
  if(g.explain_order){paragraphs.at(-1)!.support_refs.push(...bundle.required_refs.filter(r=>r.startsWith('ranking:')));paragraphs.at(-1)!.text+=(english?' The displayed recommendation order is preserved.':' The displayed recommendation order is preserved.')+(bundle.ranking.behavior_features.length?(english?' That order also uses the available behavioral-interest signals.':' That order also uses the available behavioral-interest signals.'):'');}
  return validateExplanation({version:'2.0',focus_listing_ids:g.target_listing_ids,paragraphs},bundle);
}
