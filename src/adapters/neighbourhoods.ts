import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import type {Entity} from './data-http.js';

/** Reviewed map identities, not aliases for similarly named rail stations. */
export async function loadNeighbourhoods(){
  const metadata=JSON.parse(await readFile(new URL('../../knowledge/locations/neighbourhoods.json',import.meta.url),'utf8'));
  const raw=await readFile(new URL('../../data/datasets/osm_neighbourhoods.json',import.meta.url));
  if(createHash('sha256').update(raw).digest('hex')!==metadata.raw_sha256)throw Error('NEIGHBOURHOOD_HASH_MISMATCH');
  const rows=JSON.parse(raw.toString('utf8')) as Record<string,any>[];
  if(rows.length!==metadata.count||new Set(rows.map(r=>r.place_id)).size!==rows.length)throw Error('NEIGHBOURHOOD_IDENTITY');
  const entities:Entity[]=rows.map(row=>{
    if(!/^OSM:node:\d+$/.test(row.place_id)||row.category!=='Neighbourhood'||!['neighbourhood','suburb'].includes(row.osm_place_type)||!(row.latitude>1.1&&row.latitude<1.5&&row.longitude>103.5&&row.longitude<104.2))throw Error('NEIGHBOURHOOD_IDENTITY');
    return {entity_id:row.place_id,label:'Neighbourhood',properties:{...row,source:'openstreetmap',dataset_id:'osm_sg_neighbourhoods',active:true,coordinate_status:'valid',observed_at:metadata.retrieved_at,dataset_version:metadata.version}};
  });
  return {metadata,entities};
}
