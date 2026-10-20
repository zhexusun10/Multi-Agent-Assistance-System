import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {parseLocationIntents,isListingReference} from '../src/domain/geo-intake.js';
import {extractRules,mergeRequirements,pendingQuestions,repairPendingLocationSubjects,type RequirementDraft} from '../src/domain/requirements.js';
import {LocationService} from '../src/adapters/locations.js';
import {DataHttpAdapter} from '../src/adapters/data-http.js';
import {ToolExecutor} from '../src/tools/executor.js';

export const USER_QUERY='我想租新加坡国立大学附近3公里内的房子，要求房子3公里内附近有MRT，预算3000新币每月，要求有独立浴室';
export const SIMPLE_QUERY='我想租新加坡国立大学附近3公里内的房子，预算3000新币每月，要求有独立浴室';
const empty={listing_type:'RENT' as const,criteria:[]};
test('exact screenshot separates NUS destination from MRT category, never resolving the listing subject',()=>{
  const d=extractRules(USER_QUERY,empty);assert.equal(d.clarifications.length,0);assert.equal(d.location_intents?.length,2);
  assert(d.location_intents?.some(l=>l.name==='新加坡国立大学'&&!l.category&&l.radius_m===3000));
  assert(d.location_intents?.some(l=>l.category==='MRTStation'&&l.radius_m===3000&&l.strength==='hard'));
  assert(!d.location_intents?.some(l=>isListingReference(l.name)));
  assert.equal(d.upsert.find(c=>c.field==='price')?.value,3000);assert(d.upsert.some(c=>c.value==='private_bathroom'));
  assert.equal(extractRules(SIMPLE_QUERY,empty).location_intents?.length,1);
});

test('20 colloquial housing-relative MRT expressions keep the right target and radius',()=>{
  const phrases:[string,number|undefined][]=[
    ['要求房子3公里内附近有MRT',3000],['房子三公里内附近有MRT',3000],['房子附近有MRT',undefined],
    ['这套房子附近有MRT',undefined],['房源周边有地铁站',undefined],['房屋附近有MRT',undefined],
    ['住宅附近要有地铁站',undefined],['房间附近有MRT',undefined],['单间附近有MRT',undefined],
    ['公寓附近有MRT',undefined],['房子3公里内有MRT',3000],['房子距离MRT800米以内',800],
    ['房子离地铁站500米内',500],['附近有MRT',undefined],['周边有地铁站',undefined],
    ['MRT附近',undefined],['离MRT800米以内',800],['near MRT',undefined],['MRT station nearby',undefined],
    ['房子步行800米内有MRT',800]
  ];
  for(const [q,radius] of phrases){const result=parseLocationIntents(q);assert.equal(result.length,1,q);assert.equal(result[0].category,'MRTStation',q);assert.equal(result[0].radius_m,radius,q);assert.equal(result[0].strength,radius?'hard':'soft',q);assert(q.includes(result[0].quote),q);}
  assert.equal(parseLocationIntents(phrases.at(-1)![0])[0].distance_type,'walking');
});

test('named MRT and unknown real names remain named targets; area and reported-route rules stay separate',()=>{
  for(const q of ['Clementi MRT附近3公里','房源附近有Clementi MRT','房子离Clementi MRT800米以内']){
    const l=parseLocationIntents(q)[0];assert.equal(l.name,'Clementi MRT',q);assert.equal(l.category,undefined,q);
  }
  assert.equal(parseLocationIntents('房源附近有不存在的测试大厦')[0].name,'不存在的测试大厦');
  for(const q of ['房子附近','房子3公里内','面积至少500平方米'])assert(!parseLocationIntents(q).some(l=>isListingReference(l.name)),q);
  const walking=extractRules('MRT步行5分钟内',empty);assert(walking.upsert.some(c=>c.field==='mrt_walking_mins'));assert.equal(walking.location_intents?.length,0);
  const reported=extractRules('平台标注MRT距离800米',empty);assert(reported.upsert.some(c=>c.field==='mrt_distance_m'));assert.equal(reported.location_intents?.length,0);
});

test('the legacy screenshot draft resumes without repeating the phantom place and retains the MRT requirement',async()=>{
  const report=JSON.parse(await readFile('evaluation/reports/location-subject-before.json','utf8'));
  const legacy=report.draft as RequirementDraft,original=structuredClone(legacy),fixed=repairPendingLocationSubjects(legacy);
  assert.deepEqual(legacy,original);assert.equal(fixed.clarifications.length,0);
  assert(fixed.upsert.some(c=>c.field==='place_proximity'&&c.target?.category==='MRTStation'&&c.value===3000&&c.strength==='hard'));
  const fresh=extractRules(SIMPLE_QUERY,empty);assert.deepEqual(pendingQuestions(legacy,fresh),[]);
  const merged=mergeRequirements(mergeRequirements(empty,fixed),fresh);assert.equal(merged.criteria.find(c=>c.field==='price')?.value,3000);assert(merged.criteria.some(c=>c.value==='private_bathroom'));
  const real={...fixed,clarifications:['地点目录暂未找到“不存在的测试大厦”，请确认名称或换一个常用名称。']};
  assert.equal(repairPendingLocationSubjects(real).clarifications.length,1);assert.equal(pendingQuestions(real,extractRules('预算改为2000',empty)).length,1);
});

test('generic MRT uses the official MRT-only exit catalog with source references, never an arbitrary station choice',async()=>{
  const tools=new ToolExecutor(),data=new DataHttpAdapter('http://127.0.0.1:8089',tools,async()=>randomUUID()),saved:any[]=[];
  data.request=async()=>{throw Error('Official MRT catalog must not depend on OSM name search');};
  const locations=new LocationService(data,tools,async(_ctx,body)=>{saved.push(body);return 'official-reference';});
  const r=await locations.stations({sessionId:'s',operationId:'o',agent:'b',dataset:'sample',deadline:Date.now()+30000,calls:0});
  assert(r.items.length>100);assert(r.items.every(s=>String(s.properties.name).endsWith(' MRT STATION')));
  assert(r.items.every(s=>s.properties.category==='MRTStation'&&(s.properties.reference_points as unknown[]).length>0));assert.equal(r.ref,'official-reference');assert(saved[0].raw_sha256);
});
