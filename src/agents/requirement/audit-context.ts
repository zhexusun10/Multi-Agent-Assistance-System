import type {Requirements} from '../../contracts/recommendation-v1.js';

/** Present semantics, not a ranking kernel's numeric scale, to the semantic reviewer.
 * The unchanged raw criteria remain in the attempt trace and persistence path.
 */
export function intakeAuditRequirements(requirements:Requirements){
  return {listing_type:requirements.listing_type,criteria:requirements.criteria.map(c=>{
    if(c.field!=='place_proximity'||c.strength!=='soft'||c.distance_preference!=='nearer')return c;
    return {criterion_id:c.criterion_id,field:c.field,meaning:'prefer_closer',strength:c.strength,
      target:c.target,distance_type:c.distance_type,weight:c.weight,
      distance_cutoff:null,scope:'ranking_only_no_distance_cutoff'};
  })};
}
