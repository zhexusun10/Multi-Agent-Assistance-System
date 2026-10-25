import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {DataHttpAdapter,type Entity} from './data-http.js';
import {ToolExecutor,type ToolContext} from '../tools/executor.js';
import type {Polygon,MultiPolygon} from 'geojson';
import {loadNeighbourhoods} from './neighbourhoods.js';

type Station={place_id:string;name:string;reference_points:{exit_code:string|null;object_id:number;latitude:number;longitude:number}[]};
type Catalog={version:string;source_url:string;raw_sha256:string;retrieved_at:string;measurement:string;stations:Station[]};
type AreaCatalog={version:string;source_url:string;raw_sha256:string;retrieved_at:string;areas:{place_id:string;name:string;region:string;geometry_index:number}[]};
/** Rail mode named in a mention: LRT first, then MRT (shared fixture tests/fixtures/place-mention-rules.json). */
export function stationMode(name:string):'LRT'|'MRT'|undefined{return /lrt|轻轨/i.test(name)?'LRT':/mrt|地铁|捷运/i.test(name)?'MRT':undefined;}
export const normalizePlace=(name:string)=>name.normalize('NFKC').toLowerCase().replace(/^(?:the)\s+/,'').replace(/(?:mrt|lrt)(?:\s*station)?$/i,'').replace(/\b(?:mrt|lrt|station)\b|地铁站|地铁|捷运站|站/gi,'').replace(/[\s_\-'’().]+/g,'');
export class LocationService{
  private loaded?:Promise<{catalog:Catalog;aliases:Record<string,string[]>;areaCatalog:AreaCatalog;areas:Entity[];areaAliases:Record<string,string[]>;neighbourhoods:Awaited<ReturnType<typeof loadNeighbourhoods>>}>;
  constructor(private data:DataHttpAdapter,private tools:ToolExecutor,private save:(ctx:ToolContext,body:unknown)=>Promise<string>){}
  private catalog(){return this.loaded??= (async()=>{
    const catalog=JSON.parse(await readFile(new URL('../../knowledge/locations/lta-stations.json',import.meta.url),'utf8')) as Catalog;
    if(new Set(catalog.stations.map(s=>s.place_id)).size!==catalog.stations.length)throw Error('DUPLICATE_STATION_ID');
    const raw=await readFile(new URL('../../data/datasets/lta_mrt_exits.geojson',import.meta.url));
    if(createHash('sha256').update(raw).digest('hex')!==catalog.raw_sha256)throw Error('LOCATION_CATALOG_HASH_MISMATCH');
    const aliases=JSON.parse(await readFile(new URL('../../knowledge/locations/aliases.json',import.meta.url),'utf8')) as Record<string,string[]>;
    const areaCatalog=JSON.parse(await readFile(new URL('../../knowledge/locations/ura-areas.json',import.meta.url),'utf8')) as AreaCatalog;
    const areaRaw=await readFile(new URL('../../data/datasets/ura_planning_areas.geojson',import.meta.url));
    if(createHash('sha256').update(areaRaw).digest('hex')!==areaCatalog.raw_sha256)throw Error('AREA_CATALOG_HASH_MISMATCH');
    const areaGeo=JSON.parse(areaRaw.toString('utf8')) as {features:{geometry:Polygon|MultiPolygon}[]};
    const areas:Entity[]=areaCatalog.areas.map(a=>({entity_id:a.place_id,label:'PlanningArea',properties:{...a,category:'PlanningArea',geometry:areaGeo.features[a.geometry_index].geometry}}));
    for(const region of new Set(areaCatalog.areas.map(a=>a.region))){const polygons=areas.filter(a=>a.properties.region===region).flatMap(a=>{const g=a.properties.geometry as Polygon|MultiPolygon;return g.type==='Polygon'?[g.coordinates]:g.coordinates;});areas.push({entity_id:'URA:REGION:'+region.replace(' REGION','').replaceAll(' ','_'),label:'PlanningRegion',properties:{place_id:'URA:REGION:'+region.replace(' REGION','').replaceAll(' ','_'),name:region,category:'PlanningRegion',geometry:{type:'MultiPolygon',coordinates:polygons}}});}
    const areaAliases=JSON.parse(await readFile(new URL('../../knowledge/locations/area-aliases.json',import.meta.url),'utf8')) as Record<string,string[]>;
    return {catalog,aliases,areaCatalog,areas,areaAliases,neighbourhoods:await loadNeighbourhoods()};
  })();}
  private entity(station:Station):Entity{
    return {entity_id:station.place_id,label:'MRTStation',properties:{...station,category:'MRTStation',latitude:station.reference_points.reduce((n,p)=>n+p.latitude,0)/station.reference_points.length,longitude:station.reference_points.reduce((n,p)=>n+p.longitude,0)/station.reference_points.length}};
  }
  async stations(ctx:ToolContext){
    const {catalog}=await this.catalog();
    return this.tools.run(ctx,'places',{category:'MRTStation',catalog:catalog.version},async()=>{
      const items=catalog.stations.filter(s=>s.name.endsWith(' MRT STATION')).map(s=>this.entity(s));
      if(!items.length)throw Error('MRT_CATALOG_EMPTY');
      const ref=await this.save(ctx,{source_url:catalog.source_url,dataset_version:catalog.version,raw_sha256:catalog.raw_sha256,measurement:catalog.measurement,category:'MRTStation',stations:items});
      return {items,ref,version:catalog.version};
    },30000);
  }
  async get(ctx:ToolContext,id:string){
    const {catalog,areaCatalog,areas,neighbourhoods}=await this.catalog();return this.tools.run(ctx,'places',{id,catalog:catalog.version,areas:areaCatalog.version},async()=>{
      const neighbourhood=neighbourhoods.entities.find(e=>e.entity_id===id);
      if(neighbourhood){const ref=await this.save(ctx,{entity:neighbourhood,...neighbourhoods.metadata});return {entity:neighbourhood,ref,version:neighbourhoods.metadata.version};}
      if(id.startsWith('URA:')){const entity=areas.find(a=>a.entity_id===id);if(!entity)throw Error('LOCATION_NOT_FOUND');const ref=await this.save(ctx,{source_url:areaCatalog.source_url,dataset_version:areaCatalog.version,raw_sha256:areaCatalog.raw_sha256,entity});return {entity,ref,version:areaCatalog.version};}
      const station=catalog.stations.find(s=>s.place_id===id);if(!station)throw Error('LOCATION_NOT_FOUND');
      const entity=this.entity(station),ref=await this.save(ctx,{source_url:catalog.source_url,dataset_version:catalog.version,raw_sha256:catalog.raw_sha256,retrieved_at:catalog.retrieved_at,measurement:catalog.measurement,entity});
      return {entity,ref,version:catalog.version};
    },30000);
  }
  async resolve(ctx:ToolContext,name:string,explicitStation=false){
    const {catalog,aliases,areas,areaCatalog,areaAliases,neighbourhoods}=await this.catalog();return this.tools.run(ctx,'resolve_place',{name,explicitStation,catalog:catalog.version,areas:areaCatalog.version,neighbourhoods:neighbourhoods.metadata.version},async()=>{
      const key=normalizePlace(name);
      if(!explicitStation){
        const entity=neighbourhoods.entities.find(e=>[e.properties.name,e.properties.name_local,...e.properties.aliases as string[]].some(v=>typeof v==='string'&&normalizePlace(v)===key));
        if(entity){const ref=await this.save(ctx,{mention:name,resolved:entity,...neighbourhoods.metadata});return {entity,ref,note:`“${name}” was matched to ${String(entity.properties.name||entity.properties.name_local)} neighbourhood. Distances use the mapped reference point in a straight line, not the neighbourhood boundary or namesake MRT station.`};}
      }
      if(!explicitStation){const exactAreas=areas.filter(a=>[String(a.properties.name),...(areaAliases[String(a.properties.name)]||[])].some(v=>normalizePlace(v)===key));if(exactAreas.length===1){const entity=exactAreas[0],ref=await this.save(ctx,{mention:name,resolved:entity,source_url:areaCatalog.source_url,dataset_version:areaCatalog.version,raw_sha256:areaCatalog.raw_sha256});return {entity,ref,note:`“${name}” was resolved as URA ${String(entity.properties.name)} area. Inside distance is zero; outside uses the nearest boundary’s straight-line distance. Inside homes tie on this factor, not at zero distance to a namesake station/centre. For station distance, specify ${String(entity.properties.name)} MRT.`};}}
      const requestedMode=stationMode(name);
      const exact=catalog.stations.filter(s=>(!requestedMode||s.name.endsWith(` ${requestedMode} STATION`))&&[s.name,...(aliases[s.name.replace(/ (?:MRT|LRT) STATION$/,'')]||[]),...(areaAliases[s.name.replace(/ (?:MRT|LRT) STATION$/,'')]||[])].some(v=>normalizePlace(v)===key));
      if(explicitStation&&exact.length===1){
        const station=exact[0],entity=this.entity(station),ref=await this.save(ctx,{mention:name,resolved:entity,source_url:catalog.source_url,dataset_version:catalog.version,raw_sha256:catalog.raw_sha256});
        return {entity,ref,note:`Location resolved as ${station.name}; distance uses the nearest officially recorded exit in a straight line.`};
      }
      if(explicitStation&&exact.length>1){const ref=await this.save(ctx,{mention:name,candidates:exact.map(s=>({id:s.place_id,name:s.name})),source_url:catalog.source_url,dataset_version:catalog.version});return {entity:null,ref,note:`“${name}” matches multiple rail stations. Please choose: ${exact.map(s=>s.name).join(", ")}.`};}
      const osmName=({NUS:'National University of Singapore',NTU:'Nanyang Technological University','NUS国立大学':'National University of Singapore','NUS新加坡国立大学':'National University of Singapore','新国立大学':'National University of Singapore',"新加坡国立大学":'National University of Singapore'} as Record<string,string>)[name.toUpperCase()]||name;
      const osm=await this.data.resolve(ctx,osmName);
      const matching=osm.items.filter(e=>['name','name_local','full_name'].some(k=>typeof e.properties[k]==='string'&&normalizePlace(String(e.properties[k]))===normalizePlace(osmName)));
      const chosen=matching.length===1?matching[0]:null;
      if(chosen)return {entity:chosen,ref:osm.ref,note:`“${name}” matched the place catalogue entry ${String(chosen.properties.name)}; straight-line distance is used by default. Actual routes are unverified.`};
      return {entity:null,ref:osm.ref,note:osm.items.length?`“${name}” matches multiple places. Please choose: ${osm.items.slice(0,4).map(e=>String(e.properties.name)).join(", ")}.`:`The place catalogue could not find “${name}”. Check the spelling or try a common alias.`};
    },30000);
  }
  async describe(id:string){const {catalog,areas,areaCatalog,neighbourhoods}=await this.catalog();const neighbourhood=neighbourhoods.entities.find(e=>e.entity_id===id);if(neighbourhood)return {entity:neighbourhood,source_url:neighbourhood.properties.source_url,version:neighbourhoods.metadata.version,raw_sha256:neighbourhoods.metadata.raw_sha256};const area=areas.find(a=>a.entity_id===id);if(area)return {entity:area,source_url:areaCatalog.source_url,version:areaCatalog.version,raw_sha256:areaCatalog.raw_sha256};const station=catalog.stations.find(s=>s.place_id===id);return station?{entity:this.entity(station),source_url:catalog.source_url,version:catalog.version,raw_sha256:catalog.raw_sha256}:null;}
  async coverage(){const {catalog,aliases,areas,areaAliases,areaCatalog,neighbourhoods}=await this.catalog();return {planning_areas:areas.filter(a=>a.label==='PlanningArea').length,planning_regions:areas.filter(a=>a.label==='PlanningRegion').length,mrt_stations:catalog.stations.length,mrt_exits:catalog.stations.reduce((n,s)=>n+s.reference_points.length,0),neighbourhoods:neighbourhoods.entities.map(e=>({id:e.entity_id,name:e.properties.name,name_local:e.properties.name_local,aliases:e.properties.aliases,measurement:neighbourhoods.metadata.measurement})),sources:[catalog.source_url,areaCatalog.source_url,neighbourhoods.metadata.source_url],areas:areas.map(a=>({id:a.entity_id,name:a.properties.name,aliases:areaAliases[String(a.properties.name)]||[]})),stations:catalog.stations.map(s=>({id:s.place_id,name:s.name,aliases:[...(aliases[s.name.replace(/ (?:MRT|LRT) STATION$/,'')]||[]),...(areaAliases[s.name.replace(/ (?:MRT|LRT) STATION$/,'')]||[])]}))};}
}
