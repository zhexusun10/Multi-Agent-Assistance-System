import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {rentalCases} from '../evaluation/rental-cases.js';
import {extractRules,criterion,mergeRequirements} from '../src/domain/requirements.js';
import {LocationService} from '../src/adapters/locations.js';
import {ToolExecutor} from '../src/tools/executor.js';
import {DataHttpAdapter} from '../src/adapters/data-http.js';
test('120 authored rental utterances retain exact semantic slots, negatives and clarification decisions',async()=>{
 const tools=new ToolExecutor(),locations=new LocationService(new DataHttpAdapter('http://127.0.0.1:8089',tools,async()=>randomUUID()),tools,async()=>randomUUID());
 const key=(s:any)=>JSON.stringify([s.field,s.operator,Array.isArray(s.value)?s.value.map((v:any)=>typeof v==='number'?Number(v.toFixed(2)):v).sort():typeof s.value==='number'?Number(s.value.toFixed(2)):s.value,s.strength,s.unit??'',s.target??'',s.distance_type??'',s.transport_mode??'']);
 for(const row of rentalCases){const base=mergeRequirements({listing_type:'RENT',criteria:[]},{upsert:(row.base||[]).map(s=>criterion(s.field,s.operator as any,s.value,s.unit,s.strength==='soft',{...(s.target?{target:{name:s.target}}:{}),...(s.transport_mode?{transport_mode:s.transport_mode}:{})})),sources:[],clarifications:[],remove_fields:[],supported:true});const d=extractRules(row.query,base),actual:any[]=d.upsert.map(c=>({...c,target:c.target?.place_id||c.target?.category||c.target?.name}));
  for(const l of d.location_intents||[]){const resolved=await locations.resolve({sessionId:randomUUID(),operationId:randomUUID(),agent:'a',dataset:'corpus',deadline:Date.now()+30000,calls:0},l.name,l.explicit_station);actual.push({field:'@location',operator:l.radius_m?'lte':'near',value:l.radius_m??null,strength:l.strength,unit:'m',target:resolved.entity?.entity_id,distance_type:l.distance_type});}
  assert.deepEqual(actual.map(key).sort(),row.expected.map(key).sort(),row.id+' '+row.query);assert.equal(!!d.clarifications.length,!!row.clarify,row.id+' clarify');assert.equal(d.supported,row.supported??true,row.id+' supported');assert.deepEqual(d.remove_fields.sort(),[...(row.remove||[])].sort(),row.id+' removal');
 }
});
