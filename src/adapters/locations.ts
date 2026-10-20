import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {DataHttpAdapter,type Entity} from './data-http.js';
import {ToolExecutor,type ToolContext} from '../tools/executor.js';
import type {Polygon,MultiPolygon} from 'geojson';

type Station={place_id:string;name:string;reference_points:{exit_code:string|null;object_id:number;latitude:number;longitude:number}[]};
type Catalog={version:string;source_url:string;raw_sha256:string;retrieved_at:string;measurement:string;stations:Station[]};
type AreaCatalog={version:string;source_url:string;raw_sha256:string;retrieved_at:string;areas:{place_id:string;name:string;region:string;geometry_index:number}[]};
export const normalizePlace=(name:string)=>name.normalize('NFKC').toLowerCase().replace(/^(?:the)\s+/,'').replace(/(?:mrt|lrt)(?:\s*station)?$/i,'').replace(/\b(?:mrt|lrt|station)\b|地铁站|地铁|捷运站|站/gi,'').replace(/[\s_\-'’().]+/g,'');
export class LocationService{
  private loaded?:Promise<{catalog:Catalog;aliases:Record<string,string[]>;areaCatalog:AreaCatalog;areas:Entity[];areaAliases:Record<string,string[]>}>;
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
    return {catalog,aliases,areaCatalog,areas,areaAliases};
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
    const {catalog,areaCatalog,areas}=await this.catalog();return this.tools.run(ctx,'places',{id,catalog:catalog.version,areas:areaCatalog.version},async()=>{
      if(id.startsWith('URA:')){const entity=areas.find(a=>a.entity_id===id);if(!entity)throw Error('LOCATION_NOT_FOUND');const ref=await this.save(ctx,{source_url:areaCatalog.source_url,dataset_version:areaCatalog.version,raw_sha256:areaCatalog.raw_sha256,entity});return {entity,ref,version:areaCatalog.version};}
      const station=catalog.stations.find(s=>s.place_id===id);if(!station)throw Error('LOCATION_NOT_FOUND');
      const entity=this.entity(station),ref=await this.save(ctx,{source_url:catalog.source_url,dataset_version:catalog.version,raw_sha256:catalog.raw_sha256,retrieved_at:catalog.retrieved_at,measurement:catalog.measurement,entity});
      return {entity,ref,version:catalog.version};
    },30000);
  }
  async resolve(ctx:ToolContext,name:string,explicitStation=false){
    const {catalog,aliases,areas,areaCatalog,areaAliases}=await this.catalog();return this.tools.run(ctx,'resolve_place',{name,explicitStation,catalog:catalog.version,areas:areaCatalog.version},async()=>{
      const key=normalizePlace(name);
      if(!explicitStation){const exactAreas=areas.filter(a=>[String(a.properties.name),...(areaAliases[String(a.properties.name)]||[])].some(v=>normalizePlace(v)===key));if(exactAreas.length===1){const entity=exactAreas[0],ref=await this.save(ctx,{mention:name,resolved:entity,source_url:areaCatalog.source_url,dataset_version:areaCatalog.version,raw_sha256:areaCatalog.raw_sha256});return {entity,ref,note:`“${name}”已解析为 URA ${String(entity.properties.name)} 区域；区内距离记为 0，区外按到区域边界的最近直线距离排序。区内房源在此距离项上并列，不表示到同名地铁站或中心点为0米；需要站点距离时可指定 ${String(entity.properties.name)} MRT。`};}}
      const requestedMode=/lrt|轻轨/i.test(name)?'LRT':/mrt|地铁|捷运/i.test(name)?'MRT':undefined;
      const exact=catalog.stations.filter(s=>(!requestedMode||s.name.endsWith(` ${requestedMode} STATION`))&&[s.name,...(aliases[s.name.replace(/ (?:MRT|LRT) STATION$/,'')]||[]),...(areaAliases[s.name.replace(/ (?:MRT|LRT) STATION$/,'')]||[])].some(v=>normalizePlace(v)===key));
      if(exact.length===1){
        const station=exact[0],entity=this.entity(station),ref=await this.save(ctx,{mention:name,resolved:entity,source_url:catalog.source_url,dataset_version:catalog.version,raw_sha256:catalog.raw_sha256,default_neighbourhood_anchor:!explicitStation});
        return {entity,ref,note:explicitStation?`地点已解析为 ${station.name}；距离按官方已收录出口的最近直线距离计算。`:`“${name}附近”按 ${station.name} 出口作为地区就近参考，优先更近的房源；这不是行政区域边界，也不增加硬半径限制。`};
      }
      if(exact.length>1){const ref=await this.save(ctx,{mention:name,candidates:exact.map(s=>({id:s.place_id,name:s.name})),source_url:catalog.source_url,dataset_version:catalog.version});return {entity:null,ref,note:`“${name}”对应多个轨道站点，请选择：${exact.map(s=>s.name).join('、')}。`};}
      const osmName=({NUS:'National University of Singapore',NTU:'Nanyang Technological University','新国立大学':'National University of Singapore','新加坡国立大学':'National University of Singapore'} as Record<string,string>)[name.toUpperCase()]||name;
      const osm=await this.data.resolve(ctx,osmName);
      const matching=osm.items.filter(e=>['name','name_local','full_name'].some(k=>typeof e.properties[k]==='string'&&normalizePlace(String(e.properties[k]))===normalizePlace(osmName)));
      const chosen=matching.length===1?matching[0]:osm.items.length===1?osm.items[0]:null;
      if(chosen)return {entity:chosen,ref:osm.ref,note:`“${name}”已匹配到地点目录 ${String(chosen.properties.name)}；默认采用直线距离，未验证实际路线。`};
      return {entity:null,ref:osm.ref,note:osm.items.length?`“${name}”匹配到多个地点，请选择：${osm.items.slice(0,4).map(e=>String(e.properties.name)).join('、')}。`:`地点目录暂未找到“${name}”，请确认名称或换一个常用名称。`};
    },30000);
  }
  async describe(id:string){const {catalog,areas,areaCatalog}=await this.catalog();const area=areas.find(a=>a.entity_id===id);if(area)return {entity:area,source_url:areaCatalog.source_url,version:areaCatalog.version,raw_sha256:areaCatalog.raw_sha256};const station=catalog.stations.find(s=>s.place_id===id);return station?{entity:this.entity(station),source_url:catalog.source_url,version:catalog.version,raw_sha256:catalog.raw_sha256}:null;}
  async coverage(){const {catalog,aliases,areas,areaAliases,areaCatalog}=await this.catalog();return {planning_areas:areas.filter(a=>a.label==='PlanningArea').length,planning_regions:areas.filter(a=>a.label==='PlanningRegion').length,mrt_stations:catalog.stations.length,mrt_exits:catalog.stations.reduce((n,s)=>n+s.reference_points.length,0),sources:[catalog.source_url,areaCatalog.source_url],areas:areas.map(a=>({id:a.entity_id,name:a.properties.name,aliases:areaAliases[String(a.properties.name)]||[]})),stations:catalog.stations.map(s=>({id:s.place_id,name:s.name,aliases:[...(aliases[s.name.replace(/ (?:MRT|LRT) STATION$/,'')]||[]),...(areaAliases[s.name.replace(/ (?:MRT|LRT) STATION$/,'')]||[])]}))};}
}
