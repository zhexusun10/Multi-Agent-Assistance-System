import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import pg from 'pg';
import {pointToPolygonDistance} from '@turf/point-to-polygon-distance';

// Explicitly choose the configured isolated sample or verified full snapshot.
const full=process.env.DATA_MODE==='full',expected=full?21431:593;
const graph=`http://127.0.0.1:${full?7474:7475}/db/neo4j/tx/commit`;
async function cypher(statements:{statement:string;parameters?:unknown}[]){const r=await fetch(graph,{method:'POST',headers:{'content-type':'application/json','connection':'close'},body:JSON.stringify({statements}),signal:AbortSignal.timeout(60000)});if(!r.ok)throw Error('Graph projection HTTP '+r.status);const d=await r.json() as any;if(d.errors.length)throw Error(d.errors[0].message);return d.results;}
const pool=new pg.Pool({connectionString:full?process.env.PROPERTYGURU_DATABASE_URL?.replace('postgresql+psycopg2:','postgresql:'):'postgresql://propertyguru_scraper@127.0.0.1:5432/propertyguru_sample'});
try{
  const areas=JSON.parse(await readFile('knowledge/locations/ura-areas.json','utf8')),stations=JSON.parse(await readFile('knowledge/locations/lta-stations.json','utf8'));
  const areaBytes=await readFile('data/datasets/ura_planning_areas.geojson'),stationBytes=await readFile('data/datasets/lta_mrt_exits.geojson');
  for(const [bytes,sha] of [[areaBytes,areas.raw_sha256],[stationBytes,stations.raw_sha256]] as const)if(createHash('sha256').update(bytes).digest('hex')!==sha)throw Error('Geo source hash mismatch');
  const features=JSON.parse(areaBytes.toString('utf8')).features;
  const props=(await pool.query('SELECT listing_id,latitude,longitude FROM properties')).rows;
  const current=await cypher([{statement:'MATCH (l:Listing) RETURN count(l) AS n'}]);
  if(props.length!==expected||current[0].data[0].row[0]!==expected)throw Error('Projection source/graph count mismatch; no writes performed');
  const aliases=JSON.parse(await readFile('knowledge/locations/area-aliases.json','utf8'));
  const memberships:{listing_id:string;area_id:string}[]=[];
  for(const a of areas.areas){const geometry=features[a.geometry_index].geometry;const coords:number[][]=[];const collect=(v:any)=>{if(typeof v[0]==='number')coords.push(v);else v.forEach(collect);};collect(geometry.coordinates);const xs=coords.map(p=>p[0]),ys=coords.map(p=>p[1]);const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
    for(const p of props){if(p.latitude===null||p.longitude===null||p.longitude<minX||p.longitude>maxX||p.latitude<minY||p.latitude>maxY)continue;if(pointToPolygonDistance([p.longitude,p.latitude],geometry,{units:'meters',method:'geodesic'})<=0)memberships.push({listing_id:String(p.listing_id),area_id:a.place_id});}
  }
  const areaRows=areas.areas.map((a:any)=>({...a,aliases:aliases[a.name]||[],source:'ura_mp2019',dataset_version:areas.version,source_url:areas.source_url,geometry_ref:'/api/locations/'+a.place_id}));
  const regionRows=[...new Set(areas.areas.map((a:any)=>a.region))].map((name:any)=>({place_id:'URA:REGION:'+name.replace(' REGION','').replaceAll(' ','_'),name,aliases:aliases[name]||[],source:'ura_mp2019'}));
  const stationRows=stations.stations.map((s:any)=>({...s,reference_points:undefined,exits_json:JSON.stringify(s.reference_points),latitude:s.reference_points.reduce((n:number,p:any)=>n+p.latitude,0)/s.reference_points.length,longitude:s.reference_points.reduce((n:number,p:any)=>n+p.longitude,0)/s.reference_points.length,source:'lta_mrt_exits',dataset_version:stations.version,source_url:stations.source_url}));
  const exitRows=stations.stations.flatMap((s:any)=>s.reference_points.map((p:any)=>({...p,exit_id:'LTA:EXIT:'+p.object_id,station_id:s.place_id,source:'lta_mrt_exits',dataset_version:stations.version})));
  if(new Set(stationRows.map((s:any)=>s.place_id)).size!==stationRows.length||new Set(exitRows.map((e:any)=>e.exit_id)).size!==exitRows.length)throw Error('Duplicate station or exit IDs; no writes performed');
  await cypher([{statement:'CREATE CONSTRAINT geo_location_id IF NOT EXISTS FOR (n:GeoLocation) REQUIRE n.location_id IS UNIQUE'},{statement:'CREATE CONSTRAINT geo_exit_id IF NOT EXISTS FOR (n:GeoExit) REQUIRE n.exit_id IS UNIQUE'}]);
  await cypher([
    {statement:'UNWIND $rows AS row MERGE (n:GeoLocation:GeoArea {location_id:row.place_id}) SET n+=row',parameters:{rows:areaRows}},
    {statement:'UNWIND $rows AS row MERGE (n:GeoLocation:GeoRegion {location_id:row.place_id}) SET n+=row',parameters:{rows:regionRows}},
    {statement:'UNWIND $rows AS row MERGE (n:GeoLocation:GeoStation {location_id:row.place_id}) SET n+=row, n.position=point({latitude:row.latitude,longitude:row.longitude})',parameters:{rows:stationRows}},
    {statement:"MATCH (e:GeoExit {source:'lta_mrt_exits'}) WHERE e.dataset_version<>$version DETACH DELETE e",parameters:{version:stations.version}},
    {statement:"MATCH (e:GeoExit {source:'lta_mrt_exits'})-[r:EXIT_OF]->(:GeoStation) DELETE r"},
    {statement:'UNWIND $rows AS row MATCH (s:GeoStation {location_id:row.station_id}) MERGE (e:GeoExit {exit_id:row.exit_id}) SET e+=row,e.position=point({latitude:row.latitude,longitude:row.longitude}) MERGE (e)-[:EXIT_OF]->(s)',parameters:{rows:exitRows}},
    {statement:"MATCH (s:GeoStation {source:'lta_mrt_exits'}) WHERE NOT s.location_id IN $ids DETACH DELETE s",parameters:{ids:stationRows.map((s:any)=>s.place_id)}},
    {statement:"MATCH (l:Listing)-[r:IN_PLANNING_AREA {source:'ura_mp2019'}]->(:GeoArea) DELETE r"},
    {statement:"UNWIND $rows AS row MATCH (l:Listing {listing_id:toInteger(row.listing_id)}),(a:GeoArea {location_id:row.area_id}) MERGE (l)-[r:IN_PLANNING_AREA {source:'ura_mp2019'}]->(a) SET r.method='point_in_official_polygon',r.dataset_version=$version",parameters:{rows:memberships,version:areas.version}},
    {statement:'MATCH (a:GeoArea),(r:GeoRegion) WHERE a.region=r.name MERGE (a)-[:PART_OF_PLANNING_REGION]->(r)'}
  ]);
  const counts=await cypher([{statement:'MATCH (n:GeoLocation) RETURN labels(n) AS labels,count(*) AS n'},{statement:'MATCH (:Listing)-[r:IN_PLANNING_AREA]->(:GeoArea) RETURN count(r) AS n'},{statement:'MATCH (:GeoExit)-[r:EXIT_OF]->(:GeoStation) RETURN count(r) AS n'}]);
  const nodes=counts[0].data.map((d:any)=>d.row) as [string[],number][];
  for(const [label,expected] of [['GeoArea',areaRows.length],['GeoRegion',regionRows.length],['GeoStation',stationRows.length]] as const)if(nodes.filter(([labels])=>labels.includes(label)).reduce((n,[,c])=>n+c,0)!==expected)throw Error(`Graph count mismatch for ${label}`);
  if(counts[1].data[0].row[0]!==memberships.length||counts[2].data[0].row[0]!==exitRows.length)throw Error('Graph relationship count mismatch');
  const report={checked_at:new Date().toISOString(),status:'passed',target:full?'verified full snapshot 7474':'isolated sample 7475',planning_areas:areaRows.length,regions:regionRows.length,stations:stationRows.length,exits:exitRows.length,memberships:memberships.length,source_hashes:[areas.raw_sha256,stations.raw_sha256],graph_counts:counts};
  await mkdir('evaluation/reports',{recursive:true});await writeFile(full?'evaluation/reports/geo-projection-full.json':'evaluation/reports/geo-projection.json',JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,areas:report.planning_areas,regions:report.regions,stations:report.stations,memberships:report.memberships}));
}finally{await pool.end();}
