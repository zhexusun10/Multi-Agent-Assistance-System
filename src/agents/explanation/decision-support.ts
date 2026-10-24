import type {RecommendationResponse,Requirements} from '../../contracts/recommendation-v1.js';
import {displayedCandidates,RECOMMENDATION_DISPLAY_LIMIT} from '../../domain/selection.js';
import type {ExplanationBundle,ExplanationFact} from './contracts.js';
import type {ExplanationEnrichment} from './enrichment.js';
import {describeCohort,unitRentAssessment,type CohortObservation} from './cohort.js';

const categories:Record<string,string>={MRT:"MRT station",BusStop:"Bus stop",BusStation:"Bus stop",PublicTransport:"Public transport stop",Supermarket:"Supermarket",Market:"Market",FoodCourt:"Food court",Mall:"Shopping mall",Park:"Park",Clinic:"Clinic",Hospital:"Hospital",Library:"Library",School:"School"};
const priority=Object.keys(categories);
const typeNames:Record<string,string>={condominium:"Condominium",apartment:"Apartment",'hdb flat':"HDB flat",'executive condominium':"Executive condominium",'landed house':"Landed house"};
const positive=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v)&&v>0;
const safeText=(v:unknown)=>typeof v==='string'&&v.trim()&&v.length<=180&&!/[\r\n\x00-\x1f]/.test(v)?v.trim():null;
const round=(v:number)=>Number(v.toFixed(2));

export function explanationTargets(bundle:ExplanationBundle){
  const selected=new Set<string>(),q=bundle.question;
  for(const l of bundle.listings)if(q.includes(l.title)||q.includes(l.listing_id))selected.add(l.listing_id);
  const ordinal:Record<string,number>={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10};
  for(const m of q.matchAll(/第\s*([一二三四五六七八九十\d]+)\s*套|(?:property|listing)\s*#?\s*(\d{1,2})\b/gi)){
    const n=ordinal[m[1]]??Number(m[1]||m[2]);if(bundle.listings[n-1])selected.add(bundle.listings[n-1].listing_id);
  }
  const first=q.match(/前\s*([两二三四五六七八\d]+)\s*套|first\s+(two|three|\d+)\s+(?:homes|properties|listings)/i);
  if(first){const words:Record<string,number>={two:2,three:3,...ordinal},n=words[first[1]||first[2]]??Number(first[1]||first[2]);for(const l of bundle.listings.slice(0,n))selected.add(l.listing_id);}
  return (selected.size?bundle.listings.filter(l=>selected.has(l.listing_id)):bundle.listings.slice(0,RECOMMENDATION_DISPLAY_LIMIT)).map(l=>l.listing_id);
}

/** Adds decision facts to a COPY. Criterion outcomes and native ordering stay
 * authoritative even when the display-only detail API knows another field. */
export function addDecisionSupport(source:ExplanationBundle,req:Requirements,rec:RecommendationResponse,enrichment:ExplanationEnrichment[]=[]):ExplanationBundle{
  const bundle=structuredClone(source),rows=displayedCandidates(rec),ids=new Set(rows.map(r=>r.listing.listing_id));
  const supplements=new Map<string,ExplanationEnrichment>();
  for(const e of enrichment){
    if(!ids.has(e.listing_id)||supplements.has(e.listing_id)||e.dataset!==rec.state.coverage.dataset_version)throw Error('EXPLANATION_ENRICHMENT_IDENTITY');
    supplements.set(e.listing_id,e);
  }
  const add=(ref:string,kind:ExplanationFact['kind'],text:string,listing_ids:string[],source_refs:string[]=[],numbers:number[]=[],derived_from?:string[])=>{
    bundle.facts.push({ref,kind,text,listing_ids,source_refs,numbers,...(derived_from?{derived_from}:{})});return ref;
  };
  const profiles=rows.map((row,index)=>{
    const id=row.listing.listing_id,e=supplements.get(id),p=e?.sections.details==='available'?e.details:null,sourceRefs=e?[e.source_ref]:[];
    const known_refs:string[]=[],extra_refs:string[]=[],highlight_refs:string[]=[],caution_refs:string[]=[];
    const known=(ref:string)=>{if(bundle.facts.some(f=>f.ref===ref))known_refs.push(ref);};
    known(`price:${id}`);known(`beds:${id}`);known(`area:${id}`);
    const addExtra=(field:string,label:string,value:unknown,unit='')=>{
      const canonical=(row.listing as Record<string,unknown>)[field];
      // A conflicting reread is never substituted for the accepted result.
      if(canonical!==undefined&&canonical!==null&&canonical!==0&&value!==null&&value!==undefined&&canonical!==value){
        caution_refs.push(add(`profile:${id}:conflict:${field}`,'limitation',`${label} has supplementary data differing from this recommendation. Retain the recommendation value and ask for confirmation.`,[id],sourceRefs));return;
      }
      if((field==='price'||field==='bedrooms'||field==='floor_area_sqft')&&positive(canonical))return;
      if((field==='price'||field==='bedrooms'||field==='bathrooms'||field==='floor_area_sqft')&&!positive(value))return;
      if(typeof value!=='number'&&!safeText(value))return;
      const text=field==='bathrooms'?`The listing records ${value} bathrooms. Counts do not establish exclusive use or upgrade an unverified recommendation condition.`:
        field==='floor_area_sqft'?`Listed floor area${value} sq ft, approximately ${round(Number(value)/10.763910416709722)} sq m. This is the listed property area, not verified bedroom area.`:
        `${label}: ${value}${unit}.`;
      const numbers=typeof value==='number'?[value,...(field==='floor_area_sqft'?[value/10.763910416709722]:[])]:[];
      extra_refs.push(add(`profile:${id}:${field}`,'listing',text,[id],sourceRefs,numbers));
    };
    if(p){addExtra('price',"Recorded monthly rent: ",p.price,"SGD/month");addExtra('bedrooms',"Number of bedrooms",p.bedrooms," rooms");addExtra('bathrooms',"Bathroom count",p.bathrooms," items");addExtra('floor_area_sqft',"Property area",p.floor_area_sqft);addExtra('floor_level',"Floor level",p.floor_level);addExtra('build_year',"Year built",p.build_year," year");}
    const property=row.listing.property_type||safeText(p?.property_type);
    if(property){const label=typeNames[property.toLowerCase()]||property;extra_refs.push(add(`profile:${id}:type`,'listing',`Recorded property type: ${label}; type does not establish interior amenities.`,[id],sourceRefs));}
    if(positive(row.listing.floor_area_sqft)){
      const sqft=row.listing.floor_area_sqft;
      extra_refs.push(add(`profile:${id}:area-unit`,'listing',`Listed floor area${sqft} sq ft, approximately ${round(sqft/10.763910416709722)} sq m. This is listed area, not verified bedroom area.`,[id],[],[sqft,sqft/10.763910416709722],[`area:${id}`]));
    }
    // Select one known place per category; the graph is a sampled catalogue,
    // so this is the nearest RECORDED place, never a claim of exhaustive search.
    const nearby=e?.sections.nearby==='available'?e.nearby:[];
    const chosen=priority.flatMap(category=>{
      const points=nearby.filter(n=>n.category===category&&Number.isFinite(n.distance_m)&&n.distance_m>=0&&n.distance_m<=3000).sort((a,b)=>a.distance_m-b.distance_m);
      return points.length?[points[0]]:[];
    }).slice(0,6);
    // A platform MRT annotation may also be available from SQL without an edge.
    if(!chosen.some(n=>n.category==='MRT')&&p&&safeText(p.nearest_mrt)&&positive(p.mrt_distance_m))chosen.unshift({id:'platform:mrt',name:safeText(p.nearest_mrt)!,category:'MRT',distance_m:p.mrt_distance_m,metric:'platform_reported',walking_mins:null,source_url:null,source:"PropertyGuru listing information",computed_at:null,radius_m:null,route_verified:false});
    for(const n of chosen.slice(0,6)){
      if(!safeText(n.name))continue;
      const metric=n.metric==='straight_line'?"approx. straight-line distance: ":"The information gives a distance of approximately ",distance=round(n.distance_m),label=categories[n.category];
      const meaning=['MRT','BusStop','BusStation','PublicTransport'].includes(n.category)?"A public-transport option; actual routes and services are unverified.":
        ['Supermarket','Market','FoodCourt','Mall'].includes(n.category)?"Another shopping or dining option; routes and current opening are unverified.":n.category==='Park'?"An outdoor option; actual entrances and routes are unverified.":"A neighbourhood amenity; actual routes and services are unverified.";
      extra_refs.push(add(`profile:${id}:nearby:${n.category}`,'listing',`Nearby ${label} has ${n.name}, ${metric}${distance} m.${meaning}`,[id],sourceRefs,[distance,distance/1000]));
      bundle.facts.at(-1)!.literals=[n.name];
      bundle.facts.at(-1)!.distance_metric=n.metric;
    }
    for(const r of row.criterion_results){
      const c=req.criteria.find(c=>c.criterion_id===r.criterion_id);
      if(!c)continue;const ref=`condition:${id}:${r.criterion_id}`;
      if((r.status==='unknown'||r.status==='fail')&&c.strength==='hard')caution_refs.push(ref);
      else if(r.status!=='unknown'&&c.field==='place_proximity')known(ref);
    }
    if(!known_refs.length&&!extra_refs.length)caution_refs.push(add(`profile:${id}:sparse`,'limitation',"Available data is limited. Do not invent advantages; state that there is insufficient information for a detailed comparison.",[id]));
    // Additional information is a semantic field, not a particular duplicate
    // reference. The original area observation is as valid as its unit-conversion
    // supplement when the user never requested area.
    const knownFields:Record<string,string>={price:'price',beds:'bedrooms',area:'floor_area_sqft'};
    const additional_refs=known_refs.filter(ref=>knownFields[ref.split(':')[0]]&&!req.criteria.some(c=>c.field===knownFields[ref.split(':')[0]])).concat(extra_refs.filter(ref=>{
      const field=ref.split(':')[2];
      if(field==='nearby'){
        const category=ref.split(':')[3],requestedCategory=category==='MRT'?'MRTStation':category;return !req.criteria.some(c=>c.field==='place_proximity'&&(c.target?.category===requestedCategory||c.target?.category==='PublicTransport'&&['MRT','BusStop','BusStation','PublicTransport'].includes(category)));
      }
      const actual=field==='area-unit'?'floor_area_sqft':field==='type'?'property_type':field;
      return !req.criteria.some(c=>c.field===actual);
    }));
    const core_refs=['price','area'].flatMap(field=>{const ref=`${field}:${id}`,fallback=`profile:${id}:${field==='price'?'price':'floor_area_sqft'}`;return bundle.facts.some(f=>f.ref===ref)?[ref]:bundle.facts.some(f=>f.ref===fallback)?[fallback]:[];});
    return {listing_id:id,position:index+1,title:row.listing.title,known_refs,extra_refs,additional_refs,highlight_refs,caution_refs,core_refs};
  });
  // Ratios remain private arithmetic until a useful, same-scope comparison
  // exists. Never feed the writer an isolated unit-rent number.
  const unitObservations:CohortObservation[]=[],unitSources=new Map<string,string[]>();
  for(const profile of profiles){
    const priceRef=profile.core_refs.find(r=>r===`price:${profile.listing_id}`||r.endsWith(':price'));
    const areaRef=profile.core_refs.find(r=>r===`area:${profile.listing_id}`||r.endsWith(':floor_area_sqft'));
    const price=bundle.facts.find(f=>f.ref===priceRef)?.numbers[0],area=bundle.facts.find(f=>f.ref===areaRef)?.numbers[0];
    if(!positive(price)||!positive(area)||profile.caution_refs.some(r=>/conflict:(price|floor_area_sqft)$/.test(r)))continue;
    unitSources.set(profile.listing_id,[priceRef!,areaRef!]);
    unitObservations.push({listing_id:profile.listing_id,value:round(price/area),ref:priceRef!});
  }
  const observations=(field:'price'|'floor_area_sqft')=>profiles.flatMap(p=>{
    const ref=p.core_refs.find(r=>r===`${field==='price'?'price':'area'}:${p.listing_id}`||r.endsWith(':'+field));
    const value=bundle.facts.find(f=>f.ref===ref)?.numbers[0];
    return ref&&positive(value)?[{listing_id:p.listing_id,value,ref}]:[];
  });
  const groups=[{name:'recommended' as const,ids:rows.filter(r=>rec.recommendations.includes(r)).map(r=>r.listing.listing_id)},
    {name:'unverified' as const,ids:rows.filter(r=>rec.unverified_candidates.includes(r)).map(r=>r.listing.listing_id)}];
  const positions={lowest:"lowest",highest:"highest",tied_lowest:"joint lowest",tied_highest:"joint highest",lower:"on the lower side",middle:"in the middle",higher:"on the higher side",similar:"very similar"};
  // Compare distances only to the SAME destination and using the SAME metric.
  // The hard radius and softer nearer preference share one observation, not
  // two editorial topics. This is context for the writer, never another ranker.
  const destinations=new Map<string,typeof req.criteria>();
  for(const c of req.criteria.filter(c=>c.field==='place_proximity')){
    const target=c.target?.place_id?['place',c.target.place_id]:c.target?.category?['category',c.target.category]:['name',c.target?.name];
    const key=JSON.stringify([target,c.distance_type,c.transport_mode,c.unit]);
    destinations.set(key,[...(destinations.get(key)||[]),c]);
  }
  const distancePositions={lowest:"closest",highest:"farthest",tied_lowest:"joint closest",tied_highest:"joint farthest",lower:"relatively closer",middle:"in the middle",higher:"relatively farther",similar:"equal distances"};
  for(const [key,criteria] of destinations)for(const group of groups){
    const sources=new Map<string,string[]>();
    const values=rows.filter(r=>group.ids.includes(r.listing.listing_id)).flatMap(row=>{
      const known=row.criterion_results.filter(r=>criteria.some(c=>c.criterion_id===r.criterion_id)&&r.status!=='unknown'&&typeof r.observed_value==='number'&&Number.isFinite(r.observed_value)&&r.observed_value>=0);
      if(!known.length||new Set(known.map(r=>r.observed_value)).size!==1)return [];
      const refs=known.map(r=>`condition:${row.listing.listing_id}:${r.criterion_id}`);sources.set(row.listing.listing_id,refs);
      return [{listing_id:row.listing.listing_id,value:known[0].observed_value as number,ref:refs[0]}];
    });
    const c=criteria[0],name=c.target?.name||c.target?.category||c.target?.place_id||"target destination",region=/^URA:/.test(c.target?.place_id||'');
    for(const value of values){
      const summary=describeCohort(value.listing_id,group.ids,values,{allowZero:true});if(!summary)continue;
      const ref=add(`cohort:${value.listing_id}:proximity:${c.criterion_id}`,'comparison',
        `To the same destination ${name}${region?"area (zero inside; nearest boundary outside)":''} of ${c.distance_type==='straight_line'?"straight line":c.distance_type} distance is ${Math.round(value.value)} metres; among ${summary.complete?"all homes in this group":"homes in this group with this distance available"}: ${distancePositions[summary.position]}.${summary.clustered?"Distances are similar overall; do not exaggerate the differences.":''}This compares the same measurement metric, not the nearest MRT station. Soft-preference pass/fail flags do not change the observed numbers. It establishes neither actual commute convenience nor a new order.`,
        values.map(v=>v.listing_id),[],[value.value,summary.minimum,summary.maximum,summary.median,summary.scope_count,summary.known_count],values.flatMap(v=>sources.get(v.listing_id)!));
      const fact=bundle.facts.at(-1)!;fact.subject_listing_id=value.listing_id;fact.cohort={...summary,field:'place_proximity',group:group.name};
      fact.proximity={target_name:name,target_key:key,distance_type:c.distance_type!,...(c.transport_mode?{transport_mode:c.transport_mode}:{})};
      profiles.find(p=>p.listing_id===value.listing_id)!.highlight_refs.push(ref);
    }
  }
  for(const group of groups)for(const field of ['price','floor_area_sqft','rent_per_sqft'] as const){
    const values=(field==='rent_per_sqft'?unitObservations:observations(field)).filter(x=>group.ids.includes(x.listing_id));
    // Relative unit-rent judgements require matching, explicitly recorded rental
    // scopes; an unexplained mixture of rooms and whole units is not comparable.
    if(field==='rent_per_sqft'){
      const scopes=group.ids.map(id=>{const e=supplements.get(id);return e?.sections.details==='available'?e.details?.rental_scope:null;});
      if(!scopes.length||!scopes.every(s=>['room','whole_unit'].includes(String(s))&&s===scopes[0]))continue;
    }
    for(const value of values){
      const summary=describeCohort(value.listing_id,group.ids,values);if(!summary)continue;
      const unitAssessment=field==='rent_per_sqft'?unitRentAssessment(summary):null;
      if(field==='rent_per_sqft'&&!unitAssessment)continue;
      const label=field==='price'?"Monthly rent":field==='floor_area_sqft'?"Property area":"monthly rent per sq ft";
      const scope=summary.complete?`the ${summary.scope_count} ${group.name==='recommended'?"recommended homes":"homes with information still to confirm"} in this round`:`the ${summary.known_count} homes with ${label.toLowerCase()} data, out of ${summary.scope_count} in this round`;
      const position=field==='floor_area_sqft'?positions[summary.position].replace(/lowest/g,"smallest").replace(/highest/g,"largest").replace(/lower/g,"smaller").replace(/higher/g,"larger"):positions[summary.position];
      const cluster=summary.clustered?"These values are close overall; do not exaggerate small differences.":'';
      const standout=summary.stands_out?"This difference stands out and may be a leading feature of the introduction.":'';
      const profile=profiles.find(p=>p.listing_id===value.listing_id)!;
      const unitMeaning=field==='rent_per_sqft'?(unitAssessment==='higher'?"The same area costs relatively more in this home; area and rent alone offer less value. Consider whether its other features justify the cost.":"The same area costs relatively less in this home; considering space and rent alone, it offers better value."):'';
      const ref=add(`cohort:${value.listing_id}:${field}`,'comparison',`${profile.title}: ${label} is ${value.value}${field==='rent_per_sqft'?"SGD/sq ft/month":''}; among ${scope}: ${position}. ${cluster} ${standout} ${summary.complete?'':"Other homes lack this measurement; do not claim a whole-group minimum or maximum."}${unitMeaning}`,values.map(x=>x.listing_id),[],[value.value,summary.minimum,summary.maximum,summary.median,summary.scope_count,summary.known_count],field==='rent_per_sqft'?values.flatMap(x=>unitSources.get(x.listing_id)!):values.map(x=>x.ref));
      const fact=bundle.facts.at(-1)!;fact.subject_listing_id=value.listing_id;fact.cohort={...summary,field,group:group.name};
      profile.highlight_refs.push(ref);
      if(field==='rent_per_sqft')profile.additional_refs.push(ref);
    }
  }
  // Auditable relative advantages within THIS display set. They describe a
  // buyer's trade-offs, not a new score, ranking or inferred user preference.
  for(const field of ['price','floor_area_sqft'] as const){
    const values=rows.flatMap(row=>{
      const value=row.listing[field],id=row.listing.listing_id;
      if(positive(value))return [{id,value,ref:field==='price'?`price:${id}`:`area:${id}`}];
      if(field==='floor_area_sqft'){
        const e=supplements.get(id),v=e?.sections.details==='available'?e.details?.floor_area_sqft:null,ref=`profile:${id}:floor_area_sqft`;
        if(positive(v)&&bundle.facts.some(f=>f.ref===ref))return [{id,value:v,ref}];
      }return [];
    });
    if(values.length<2)continue;
    for(const x of values){
      const others=values.filter(y=>field==='price'?y.value>x.value:y.value<x.value).sort((a,b)=>Math.abs(a.value-x.value)-Math.abs(b.value-x.value));
      if(!others.length)continue;const y=others[0],peer=rows.find(r=>r.listing.listing_id===y.id)!.listing.title,difference=Math.abs(x.value-y.value);
      const title=rows.find(r=>r.listing.listing_id===x.id)!.listing.title;
      const text=field==='price'?`${title} has recorded monthly rent of ${x.value} SGD; ${peer} is ${y.value} SGD; ${title} compared with ${peer} less per month by ${difference} SGD. Lower rent/budget headroom is valid, not guaranteed better value, lower total costs or the actual ranking cause.`:
        `${title} has listed area of ${x.value} sq ft; ${peer} is ${y.value} sq ft; ${title} compared with ${peer} more by ${difference} sq ft. A larger listed area is valid; rental scope and whole-unit/room basis need confirmation. Do not guarantee a larger bedroom, greater comfort or occupancy.`;
      const ref=add(`advantage:${x.id}:${field}`,'comparison',text,[x.id,y.id],[],[x.value,y.value,difference,...(field==='floor_area_sqft'?[x.value/10.763910416709722,y.value/10.763910416709722,difference/10.763910416709722]:[])],[x.ref,y.ref]);
      bundle.facts.at(-1)!.subject_listing_id=x.id;
      // Pairwise comparisons remain available for a focused comparison, but
      // are no longer the default highlight fed to a general listing guide.
    }
  }
  const target_listing_ids=explanationTargets(bundle),explain_order=/排序|排名|排(?:在|到|前|后)|顺序|为什么第|\b(?:rank\w*|order\w*)\b/i.test(bundle.question);
  const focused=target_listing_ids.length!==Math.min(RECOMMENDATION_DISPLAY_LIMIT,rows.length)||explain_order||/只(?:问|看|说|谈|比较)|仅(?:问|看|比较)|比较前[两二2]套|对比前[两二2]套|\b(?:only|versus)\b/i.test(bundle.question);
  bundle.decision_support={mode:focused?'focused':'guide',target_listing_ids,explain_order,profiles};
  add('guide:purpose','limitation',"Introduce each current home’s real features and supported relative advantages, adding useful known facts beyond the user’s requests to help them choose. Do not reorder or guarantee all requirements are satisfied.",[]);
  bundle.required_refs=rows.length?['availability:current',...(explain_order?['ranking:method',...(bundle.ranking.behavior_features.length?['ranking:behavior']:[])]:[])]:['results:empty'];
  if(explain_order)for(const p of profiles.filter(p=>target_listing_ids.includes(p.listing_id))){
    const ref=`selection:${p.listing_id}`;
    if(bundle.facts.some(f=>f.ref===ref)){p.known_refs.push(ref);bundle.required_refs.push(ref);}
  }
  return bundle;
}
