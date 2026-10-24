import type {Criterion} from '../contracts/recommendation-v1.js';
import {amenityLabels} from './field-capabilities.js';
import {criterionText} from './explanation.js';

/** Turn internal filter/evidence labels into questions a renter would actually ask. */
export function customerQuestion(c:Criterion):string{
  if(c.field==='amenities')return ({private_bathroom:"Does the room have a private bathroom, or is it shared with other people?",master_room:"Is the room a master bedroom?",common_room:"Is this a common room?",pet_policy:"Are pets allowed?",cooking_allowed:"Is cooking allowed?",air_conditioning:"Does the room have air conditioning?",no_live_in_landlord:"Does the landlord live in the property?",swimming_pool:"Is use of the swimming pool included?"} as Record<string,string>)[String(c.value)]||`Could you confirm whether there is ${amenityLabels[String(c.value)]||String(c.value)}?`;
  if(c.field==='rental_scope')return c.value==='whole_unit'?"Is this the whole unit, or only one room?":"Is this a room rental?";
  if(c.field==='furnishing')return c.value==='fully_furnished'?"Is it fully furnished? What furniture is included?":c.value==='unfurnished'?"Is the property unfurnished?":"What furniture is included, and what would I need to provide?";
  if(c.field==='lease_months')return c.operator==='eq'?`Could I rent it for ${c.value} months?`:`I would prefer a lease ${c.operator==='gte'?"At least":c.operator==='lte'?"at most":"between "}${Array.isArray(c.value)?c.value.join(' to '):c.value} months. Would that be possible?`;
  if(c.field==='move_in_date')return `I would like to move in ${c.operator==='lte'?'on or before ':c.operator==='gte'?'on or after ':''}${c.value}. Would that timing work?`;
  if(c.field==='current_availability')return "Is this property still available?";
  if(c.field==='commute_time')return `Travelling from here to ${c.target?.name||c.target?.category||"my workplace or school"}, ${c.transport_mode==='transit'?"by public transport":c.transport_mode==='walking'?"walking":"Commute"} usually takes how long? I would prefer ${c.operator==='between'&&Array.isArray(c.value)?c.value.join(' to '):c.value} minutes${c.operator==='lte'?" or less":''} for the journey.`;
  if(c.field==='price')return `My monthly rent budget is ${Array.isArray(c.value)?c.value.join(' to '):c.value} SGD${c.operator==='lte'?" or less":''}. Would the rent be negotiable?`;
  const friendly=criterionText(c).replace(/(?:，必须满足|，软偏好|，偏好权重 [\d.]+|, must have|, soft preference|, preference weight [\d.]+)/g,'').replace(/等于\s*/g,"is ").replace(/ SGD\/month/g,"SGD/month").replace(/ sqft/g," sq ft").replace(/ count/g," rooms").replace(/\s+m\b/g," metres");
  return `Could you tell me more about ${friendly} and confirm the details?`;
}
