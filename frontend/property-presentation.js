const names={price:"Monthly rent",bedrooms:"Number of bedrooms",bathrooms:"Number of bathrooms",floor_area_sqft:"Floor area",land_area_sqft:"Land area",build_year:"Year built",property_type:"Property type",district:"District",region_code:"Region",postal_code:"Postal code",lease_months:"Lease duration",furnishing:"Furnishing",rental_scope:"Rental scope",commute_time:"Commute time",move_in_date:"Move-in date",current_availability:"Current availability"};
const amenities={private_bathroom:"Private bathroom",master_room:"Master room",common_room:"Common room",air_conditioning:"Air conditioning",cooking_allowed:"Cooking policy",pet_policy:"Pet policy",no_live_in_landlord:"Live-in landlord",swimming_pool:"Swimming pool",gym:"Gym",balcony:"Balcony",parking:"Parking",elevator:"Lift",wifi:"Wi-Fi",washer:"Washing machine",refrigerator:"Refrigerator",security:"Security"};
const places={'National University of Singapore':"National University of Singapore",'Nanyang Technological University':"Nanyang Technological University",CLEMENTI:"Clementi",'金文泰':"Clementi",'南洋理工大学':"Nanyang Technological University",'新加坡国立大学':"National University of Singapore",'超市':"Supermarket",'MRT或公交站':"MRT or bus stop",'最近的MRT或公交站':"Nearest MRT or bus stop"};
const finite=v=>typeof v==='number'&&Number.isFinite(v);
export const money=value=>finite(value)?'$'+value.toLocaleString('en-SG'):"Rent to confirm";
export const distance=value=>Math.round(value).toLocaleString('en-SG')+" m";
const targetName=c=>places[c.target?.name]||c.target?.name||c.target?.category||"destination";
const placeKey=c=>JSON.stringify([c.target?.place_id||c.target?.name||c.target?.category,c.distance_type]);
const label=c=>c.field==='amenities'?(amenities[c.value]||String(c.value)):c.field==='place_proximity'?`Distance to ${targetName(c)}${c.distance_preference==='bounded'?` (${{lte:'≤',gte:'≥',between:"Range"}[c.operator]||''}${Array.isArray(c.value)?c.value.map(distance).join('–'):distance(c.value)})`:''}`:names[c.field]||c.field;

// Presentation only: preserve the recommendation's statuses and measured values.
export function matchSummary(candidate,criteria,formatCriterion){
  const rows=[],pending=new Set(),seenPlaces=new Set();
  const results=candidate.criterion_results.map(r=>({r,c:criteria.find(c=>c.criterion_id===r.criterion_id)})).filter(x=>x.c);
  results.sort((a,b)=>(a.c.strength==='soft')-(b.c.strength==='soft'));
  const boundedPlaces=new Set(results.filter(({c})=>c.field==='place_proximity'&&c.distance_preference==='bounded').map(({c})=>placeKey(c)));
  for(const {r,c} of results){
    if(c.field==='place_proximity'&&c.distance_preference==='nearer'&&boundedPlaces.has(placeKey(c)))continue;
    if(r.status==='unknown'){pending.add(label(c)+(c.field==='place_proximity'&&/missing valid (?:listing )?coordinates/i.test(r.reason||'')?" (listing coordinates unavailable)":''));continue;}
    let text;const relative=c.field==='place_proximity'&&c.strength==='soft'&&c.distance_preference==='nearer';
    if(c.field==='place_proximity'){
      const key=placeKey(c);
      if(seenPlaces.has(key))continue;
      if(!finite(r.observed_value)){pending.add(label(c));continue;}
      seenPlaces.add(key);
      const region=/^URA:/.test(c.target?.place_id||'');
      const metric=c.distance_type==='walking'?"walking":c.distance_type==='driving'?"driving":"straight line";
      text=region&&r.observed_value===0?`Within ${targetName(c)} area`:`Distance to ${targetName(c)}${region?" area boundary":''} ${distance(r.observed_value)} (${metric}`;
      if(!(region&&r.observed_value===0))text+=((c.strength==='hard'||c.distance_preference==='bounded')&&finite(c.value)?`, ${c.strength==='soft'?"Prefer ":"required"} ${{lte:'≤',gte:'≥'}[c.operator]||''} ${distance(c.value)}`:relative?"; closer is preferred":'')+')';
    }else if(c.field==='price'&&c.operator==='lte')text=r.status==='fail'?`Monthly rent exceeds the ${money(c.value)} budget`:`Monthly rent is within the ${money(c.value)} budget`;
    else text=formatCriterion(c).replace(/^(?:Required|Preferred|必须|偏好) · /,'').replace('SGD/month',"SGD/month").replace(/ count\b/,"");
    // A soft preference affects ranking; it is not a pass/fail requirement.
    rows.push({status:c.strength==='soft'?'preference':r.status,text:(c.strength==='soft'?"Preferred · ":'')+text});
  }
  const l=candidate.listing;
  if(!finite(l.bedrooms)||l.bedrooms<=0)pending.add("Number of bedrooms");
  if(!finite(l.floor_area_sqft)||l.floor_area_sqft<=0)pending.add("Floor area");
  if(!l.property_type)pending.add("Property type");
  if(!l.availability_status||l.availability_status==='unknown')pending.add("Current availability");
  return {rows,pending:[...pending]};
}

export function renderMatches({el,candidate,criteria,formatCriterion,detail=false}){
  const section=el('section',detail?'detail-match':'listing-match-summary');
  section.append(el(detail?'h3':'h4','',"How it matches your requirements"));
  const summary=matchSummary(candidate,criteria,formatCriterion),chips=el('div','match-chips');
  for(const row of summary.rows)chips.append(el('span','match-chip '+row.status,(row.status==='preference'?'':row.status==='fail'?'× ':'✓ ')+row.text));
  section.append(chips);
  if(summary.pending.length)section.append(el('p','pending-summary',"To confirm: "+summary.pending.join(', ')));
  return section;
}

export function rentElement(el,value,className){const p=el('div',className);p.append(el('span','rent-amount',money(value)));if(finite(value))p.append(el('span','rent-period'," / month"));return p;}
