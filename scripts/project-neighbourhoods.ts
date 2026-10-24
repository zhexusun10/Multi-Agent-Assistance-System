import {pathToFileURL} from 'node:url';
import {loadNeighbourhoods} from '../src/adapters/neighbourhoods.js';

/** Data projection only: the native geographic scorer reads these ordinary OSM
 * Place nodes without any change to its filtering or scoring algorithms. */
export async function projectNeighbourhoods(){
  const full=process.env.DATA_MODE==='full',{metadata,entities}=await loadNeighbourhoods();
  const url=`http://127.0.0.1:${full?7474:7475}/db/neo4j/tx/commit`;
  const statements=[
    {statement:'MATCH (l:Listing) RETURN count(l) AS n'},
    {statement:'UNWIND $rows AS row MERGE (p:Place:Neighbourhood {place_id:row.place_id}) SET p+=row,p.position=point({latitude:row.latitude,longitude:row.longitude,srid:4326})',parameters:{rows:entities.map(e=>e.properties)}},
    {statement:"MATCH (p:Place {dataset_id:'osm_sg_neighbourhoods'}) WHERE NOT p.place_id IN $ids SET p.active=false WITH p OPTIONAL MATCH ()-[r:NEAR_PLACE]->(p) DELETE r",parameters:{ids:entities.map(e=>e.entity_id)}},
    {statement:"MATCH (p:Place {dataset_id:'osm_sg_neighbourhoods'}) OPTIONAL MATCH ()-[old:NEAR_PLACE {source:'osm_sg_proximity'}]->(p) DELETE old"},
    {statement:`MATCH (d:Dataset {dataset_id:'osm_sg_places'}),(p:Place {dataset_id:'osm_sg_neighbourhoods',active:true}),(l:Listing {source:'propertyguru'})
      WHERE l.coordinate_status='valid' AND l.position IS NOT NULL AND point.distance(l.position,p.position)<=d.radius_m
      MERGE (l)-[r:NEAR_PLACE {source:'osm_sg_proximity'}]->(p)
      SET r.distance_m=round(point.distance(l.position,p.position),2),r.distance_type='geodesic',r.distance_unit='m',r.route_verified=false,
          r.measurement_method='neo4j.point.distance.WGS84',r.radius_m=d.radius_m,r.computed_at=$at,
          r.listing_source_updated_at=l.source_updated_at,r.place_observed_at=p.observed_at,r.distance_label='街区地图参考点直线距离（非边界或车站）'
      RETURN count(r) AS edges`,parameters:{at:new Date().toISOString()}},
  ];
  // Check the target before any writes; never project into an accidental empty graph.
  const run=async(statements:unknown[])=>{
    const response=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({statements}),signal:AbortSignal.timeout(30000)});
    if(!response.ok)throw Error('NEIGHBOURHOOD_PROJECTION_HTTP_'+response.status);
    const result=await response.json() as any;if(result.errors.length)throw Error(result.errors[0].message);return result.results;
  };
  const before=await run([statements[0]]);
  if(before[0].data[0].row[0] !== (full?21431:593))throw Error('NEIGHBOURHOOD_PROJECTION_SOURCE_COUNT');
  const result=await run(statements.slice(1));
  return {neighbourhoods:entities.length,edges:result.at(-1).data[0].row[0],version:metadata.version};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href)console.log(JSON.stringify(await projectNeighbourhoods()));
