import type {Requirements,RecommendationResponse,Criterion} from '../../../contracts/recommendation-v1.js';
import type {RefinementQuestion} from '../../../contracts/requirement-refinement.js';
import {INTAKE_ENUMS} from '../contracts.js';
import {displayedCandidates} from '../../../domain/selection.js';

export const REFINEMENT_POLICY={max_questions:3,min_coverage:.75,min_price_spread:100,min_area_spread_sqft:50} as const;
const valid=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v)&&v>0;
const sameTarget=(c:Criterion,q:Criterion)=>JSON.stringify([c.target?.place_id||c.target?.category||c.target?.name,c.distance_type])===JSON.stringify([q.target?.place_id||q.target?.category||q.target?.name,q.distance_type]);
const typeLabels:Record<string,string>={'HDB Flat':"HDB flat",Condominium:"Condominium",Apartment:"Apartment",'Walk-up Apartment':"Walk-up apartment",'Terraced House':"Terraced house",'Semi-Detached House':"Semi-detached house",'Detached House':"Detached house",'Bungalow House':"Bungalow",'Corner Terrace':"Corner terrace",Shophouse:"Shophouse",'Good Class Bungalow':"Good class bungalow",'Executive Condominium':"Executive condominium"};
export function planRefinement(requirements:Requirements,rec:RecommendationResponse,answered:ReadonlySet<string>=new Set()):RefinementQuestion[]{
  if(rec.status==='error')return [];
  const candidates=displayedCandidates(rec);if(!candidates.length)return [];
  const known=(field:string)=>requirements.criteria.some(c=>c.field===field);
  const questions:RefinementQuestion[]=[];
  const enough=(n:number)=>n>=Math.ceil(candidates.length*REFINEMENT_POLICY.min_coverage);
  const prices=candidates.map(c=>c.listing.price).filter(valid),areas=candidates.map(c=>c.listing.floor_area_sqft).filter(valid);
  const budget=requirements.criteria.find(c=>c.field==='price'&&c.strength==='hard'&&['lte','between'].includes(c.operator));
  const ceiling=budget?(Array.isArray(budget.value)?Number(budget.value[1]):Number(budget.value)):undefined;
  if(!budget)questions.push({id:'budget',kind:'budget',title:"What is your maximum monthly rent in SGD?",help:"A maximum is enough; a minimum is optional."});
  if(!known('bedrooms'))questions.push({id:'bedrooms',kind:'bedrooms',title:"How many bedrooms would you like?"});
  const types=candidates.flatMap(c=>{const t=INTAKE_ENUMS.property_type.find(t=>t.toLowerCase()===c.listing.property_type?.trim().toLowerCase());return t?[t]:[];});
  const options=[...new Set(types)].sort().map(value=>({value,label:typeLabels[value]||value}));
  if(!known('property_type')&&enough(types.length)&&options.length>=2)questions.push({id:'property_type',kind:'property_type',title:"Which property types do you prefer?",options});
  const explicitRoom=requirements.criteria.some(c=>c.field==='rental_scope'&&c.value==='room'||c.field==='amenities'&&['master_room','common_room'].includes(String(c.value)));
  if(!explicitRoom&&!known('floor_area_sqft')&&enough(areas.length)&&Math.max(...areas)-Math.min(...areas)>=REFINEMENT_POLICY.min_area_spread_sqft)questions.push({id:'area',kind:'area',title:"Do you have a preferred listed floor area?",help:"This is the listed property area, not an individual bedroom’s area."});
  if(valid(ceiling)&&!requirements.criteria.some(c=>c.field==='price'&&c.strength==='soft')&&enough(prices.length)&&Math.max(...prices)-Math.min(...prices)>=REFINEMENT_POLICY.min_price_spread)questions.push({id:'ideal_rent',kind:'ideal_rent',title:"Within your budget, what monthly rent would you prefer?",upper_bound:ceiling,help:`Your existing budget cap of ${ceiling.toLocaleString('en-SG')} SGD stays unchanged.`});
  for(const c of requirements.criteria.filter(c=>c.field==='place_proximity'&&c.strength==='hard'&&c.operator==='lte'&&c.distance_type==='straight_line')){
    if(requirements.criteria.some(s=>s.strength==='soft'&&s.field==='place_proximity'&&sameTarget(c,s)))continue;
    const values=candidates.map(x=>x.criterion_results.find(r=>r.criterion_id===c.criterion_id)).filter(r=>r&&r.status!=='unknown'&&typeof r.observed_value==='number'&&Number.isFinite(r.observed_value)).map(r=>Number(r!.observed_value));
    if(enough(values.length)&&Math.max(...values)-Math.min(...values)>=50)questions.push({id:'closer:'+c.criterion_id,kind:'closer',title:`Within the range, would you prefer to be closer to ${c.target?.name||"this location"}?`,help:"Compare straight-line distances without adding a new limit.",target:c.target,distance_type:c.distance_type});
  }
  return questions.filter(q=>!answered.has(q.id)).slice(0,REFINEMENT_POLICY.max_questions);
}
