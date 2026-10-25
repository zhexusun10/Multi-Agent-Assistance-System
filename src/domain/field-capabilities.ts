import type {Criterion} from '../contracts/recommendation-v1.js';

export const amenityLabels:Record<string,string>={private_bathroom:"Private bathroom (exclusive use, not shared)",master_room:"Master room",common_room:"Common room",swimming_pool:"Swimming pool",gym:"Gym",balcony:"Balcony",parking:"Parking",elevator:"Lift",wifi:"Wi-Fi",washer:"Washing machine",refrigerator:"Refrigerator",security:"Security",pet_policy:"Pets allowed",cooking_allowed:"Cooking allowed",air_conditioning:"Air conditioning",no_live_in_landlord:"No live-in landlord"};
export const valueLabels:Record<string,string>={fully_furnished:"Fully furnished",partially_furnished:"Partially furnished",unfurnished:"Unfurnished",whole_unit:"Whole unit",room:"Room"};
export function unknownReason(c:Criterion):string{
 switch(c.field){
  case 'amenities':return c.value==='private_bathroom'?"Data unavailable: the snapshot does not establish private or exclusive bathroom use. Bathroom count does not prove whether it is shared.":`Data unavailable: the snapshot has no verifiable “${amenityLabels[String(c.value)]||c.value}” record.`;
  case 'furnishing':return "Data unavailable: furnishing is not recorded and cannot be inferred from photos or property type.";
  case 'rental_scope':return "Data unavailable: whole-unit/room scope is not recorded. Bedroom count does not establish rental scope.";
  case 'lease_months':return "Data unavailable: rental duration is not recorded. Property tenure is not the tenant’s lease term.";
  case 'move_in_date':return "Data unavailable: confirm the move-in date with the agent.";
  case 'commute_time':return "Route tools are not connected: actual journey time for a destination, mode and departure time is unavailable. Straight-line distance or station walking minutes cannot replace it.";
  case 'current_availability':return "Current status is unverified: a historical listing, platform badge or successful crawl does not prove availability today.";
  case 'place_proximity':return c.distance_type!=='straight_line'?"Route tools are not connected: only straight-line distance can be checked, not walking/driving route distance.":"Calculation evidence is missing: listing coordinates or destination geometry are unavailable.";
  default:return "The recorded value is missing or unusable for this listing.";
 }
}
