import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {DataHttpAdapter} from '../src/adapters/data-http.js';
import {LocationService} from '../src/adapters/locations.js';
import {ToolExecutor,type ToolContext} from '../src/tools/executor.js';
import {parseLocationIntents} from '../src/domain/geo-intake.js';
import {extractRules,mergeRequirements,criterion} from '../src/domain/requirements.js';
import {distanceToEntity,geodesic,proximityUtility} from '../src/domain/spatial.js';

const ctx=():ToolContext=>({sessionId:randomUUID(),operationId:randomUUID(),agent:'a',dataset:'unit-test',deadline:Date.now()+120000,calls:0});
function service(){const tools=new ToolExecutor(),data=new DataHttpAdapter('http://127.0.0.1:8089',tools,async()=>randomUUID());data.request=async()=>{throw Error('Known catalog names must resolve without a network request');};return new LocationService(data,tools,async()=>randomUUID());}
test('real catalog covers 55 areas, 5 regions and official stations; every area has English and Chinese resolution',async()=>{
  const locations=service(),coverage=await locations.coverage();assert.equal(coverage.planning_areas,55);assert.equal(coverage.planning_regions,5);assert.equal(coverage.mrt_stations,188);assert.equal(coverage.mrt_exits,613);
  for(const area of coverage.areas){assert.equal((await locations.resolve(ctx(),String(area.name))).entity?.entity_id,area.id);assert(area.aliases.length>0);assert.equal((await locations.resolve(ctx(),area.aliases[0])).entity?.entity_id,area.id);}
  assert.equal(new Set(coverage.stations.map(s=>s.id)).size,coverage.mrt_stations);
  for(const station of coverage.stations)assert.equal((await locations.resolve(ctx(),station.name,true)).entity?.entity_id,station.id);
  for(const name of ['BUKIT PANJANG','CHOA CHU KANG','PUNGGOL']){assert.equal((await locations.resolve(ctx(),name+' MRT',true)).entity?.entity_id,'LTA:MRT:'+name.replaceAll(' ','_'));assert.equal((await locations.resolve(ctx(),name+' LRT',true)).entity?.entity_id,'LTA:LRT:'+name.replaceAll(' ','_'));}
});
test('Clementi area and MRT are distinct grounded entities; aliases and joined MRT spelling work',async()=>{
  const locations=service();for(const name of ['金文泰','Clementi','克莱门特','金文台'])assert.equal((await locations.resolve(ctx(),name)).entity?.entity_id,'URA:AREA:CLEMENTI');
  for(const name of ['Clementi MRT','ClementiMRT','金文泰地铁站','克莱门特MRT'])assert.equal((await locations.resolve(ctx(),name,true)).entity?.entity_id,'LTA:MRT:CLEMENTI');
});

test('Chinatown and Holland Village neighbourhoods resolve bilingually without becoming rail stations',async()=>{
  const locations=service();
  for(const [names,id,station] of [
    [['牛车水','Chinatown','China Town','Chinatown Singapore'],'OSM:node:2511049756','LTA:MRT:CHINATOWN'],
    [['荷兰村','Holland Village','Holland V'],'OSM:node:8777049478','LTA:MRT:HOLLAND_VILLAGE'],
  ] as const){
    for(const name of names){const r=await locations.resolve(ctx(),name,false);assert.equal(r.entity?.entity_id,id);assert.equal(r.entity?.label,'Neighbourhood');assert.match(r.note,/reference point/i);}
    assert.equal((await locations.resolve(ctx(),names[0]+'地铁站',true)).entity?.entity_id,station);
    const readContext=ctx();readContext.agent='b';const found=await locations.get(readContext,id);assert.equal(found.entity.entity_id,id);
    assert.equal((await locations.describe(id))?.entity.entity_id,id);
  }
});

test('a single substring POI match and a same-name station are not substitutes for an unknown neighbourhood',async()=>{
  const tools=new ToolExecutor(),data=new DataHttpAdapter('http://127.0.0.1:8089',tools,async()=>randomUUID());
  data.request=async()=>({items:[{entity_id:'OSM:node:1',label:'FoodCourt',properties:{name:'Example Food Centre'}}],next_cursor:null,has_more:false,page_size:50,source:{store:'test',role:'places'},warnings:[]});
  const locations=new LocationService(data,tools,async()=>randomUUID());
  assert.equal((await locations.resolve(ctx(),'Example')).entity,null);
  assert.equal((await locations.resolve(ctx(),'Dover')).entity,null);
});
test('natural nearby stays soft and an explicit radius converts Chinese or Arabic kilometres to 3000 metres',()=>{
  assert.equal(parseLocationIntents('我要找金文泰附近')[0].radius_m,undefined);
  for(const q of ['就是Clementi MRT附近3公里','克莱门特MRT附近三公里','金文泰地铁站周边3km']){const l=parseLocationIntents(q)[0];assert.equal(l.radius_m,3000);assert.equal(l.strength,'hard');assert.equal(l.distance_type,'straight_line');assert(l.explicit_station);}
});
test('a transport-mode-only follow-up retains the original 30 minute target',()=>{
  const base=mergeRequirements({listing_type:'RENT',criteria:[]},extractRules('离新国立大学通勤30分钟以内',{listing_type:'RENT',criteria:[]}));
  const next=mergeRequirements(base,extractRules('通勤指定方式为公交\n就是Clementi MRT附近3公里',base));const c=next.criteria.find(c=>c.field==='commute_time')!;assert.equal(c.value,30);assert.equal(c.transport_mode,'transit');assert.equal(c.target?.name,'新国立大学');
});
test('continuous distance utility is strictly decreasing beyond any pivot and never creates a distance cutoff',()=>{
  const distances=[0,100,1000,3000,10000,50000],scores=distances.map(d=>proximityUtility(d,1000));assert(scores.every((s,i)=>s>0&&(!i||s<scores[i-1])));assert.equal(scores[2],.5);
});
test('area distance is zero inside, positive outside; station distance uses actual nearest exit',async()=>{
  const locations=service(),area=(await locations.resolve(ctx(),'金文泰')).entity!,station=(await locations.resolve(ctx(),'Clementi MRT',true)).entity!;
  const point=(station.properties.reference_points as any[])[0];assert.equal(distanceToEntity(point.latitude,point.longitude,area),0);assert(distanceToEntity(1.4,103.95,area)!>1000);
  const c=ctx();c.agent='b';const resolved=await locations.get(c,station.entity_id);const d=distanceToEntity(1.31,103.76,resolved.entity)!;assert(Math.abs(d-Math.min(...(station.properties.reference_points as any[]).map(p=>geodesic(1.31,103.76,p.latitude,p.longitude))))<1e-9);
});
