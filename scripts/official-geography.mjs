/** Bounded JSON-in/JSON-out data bridge. Reuses the application's authoritative
 * location catalogue and geodesic implementation; never invokes an agent/model. */
import {LocationService} from '../dist/adapters/locations.js';
import {distanceToEntity} from '../dist/domain/spatial.js';

let text='';
for await(const chunk of process.stdin){text+=chunk;if(text.length>16*1024*1024)throw Error('OFFICIAL_GEO_INPUT_LIMIT');}
const input=JSON.parse(text);
if(!Array.isArray(input.criteria)||!Array.isArray(input.listings)||input.criteria.length>32||input.listings.length>100000)throw Error('OFFICIAL_GEO_INVALID_INPUT');
// describe() reads only bundled, hash-validated catalogues; it does not use the
// data/tools/save dependencies used by other LocationService methods.
const locations=new LocationService(null,null,null),measurements=[],catalogues=[];
for(const c of input.criteria){
  const target=await locations.describe(c.destination_id);
  const official=target&&/^(URA|LTA):/.test(target.entity.entity_id);
  const metadata=official?{place_id:target.entity.entity_id,name:target.entity.properties.name,version:target.version,raw_sha256:target.raw_sha256,source_url:target.source_url}:null;
  if(metadata)catalogues.push(metadata);
  const method=official?(target.entity.properties.geometry?'turf.geodesic.distance_to_area_zero_inside':'haversine.nearest_recorded_exit'):'unavailable';
  for(const l of input.listings){
    const valid=Number.isFinite(l.latitude)&&Number.isFinite(l.longitude)&&Math.abs(l.latitude)<=90&&Math.abs(l.longitude)<=180;
    const usable=official&&valid&&c.field==='distance'&&c.distance_type==='straight_line';
    const value=usable?distanceToEntity(l.latitude,l.longitude,target.entity):null;
    measurements.push({listing_id:l.listing_id,criterion_id:c.criterion_id,value,method:value===null?'unavailable':method,destination_id:official?target.entity.entity_id:null,catalogue:metadata,unknown_reason:!official?'official destination not found':c.field!=='distance'||c.distance_type!=='straight_line'?'unsupported travel mode: route evidence unavailable':!valid?'missing valid listing coordinates':value===null?'official geometry unavailable':null});
  }
}
process.stdout.write(JSON.stringify({measurements,catalogues}));
