import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {DataHttpAdapter} from '../src/adapters/data-http.js';
import {LocationService,normalizePlace,stationMode} from '../src/adapters/locations.js';
import {isExplicitStation,isGenericMrt} from '../src/domain/geo-intake.js';
import {ToolExecutor,type ToolContext} from '../src/tools/executor.js';
import {distanceToEntity} from '../src/domain/spatial.js';

// Shared fixtures with recommendation/tests/test_geography.py: the native Python geography
// (recommendation/geography/) must give the same results as this TS implementation.
test('normalizePlace matches the shared place-normalization fixture (TS side of the parity check)',async()=>{
  const fixture=JSON.parse(await readFile('tests/fixtures/place-normalization.json','utf8')) as {cases:{input:string;expected:string}[]};
  assert(fixture.cases.length>=300);
  for(const c of fixture.cases)assert.equal(normalizePlace(c.input),c.expected,JSON.stringify(c.input));
});
test('explicit-station, rail-mode and generic-MRT rules match the shared place-mention-rules fixture (TS side)',async()=>{
  const fixture=JSON.parse(await readFile('tests/fixtures/place-mention-rules.json','utf8')) as {cases:{input:string;explicit_station:boolean;requested_mode:string|null;generic_mrt:boolean}[]};
  assert(fixture.cases.length>=70);
  for(const c of fixture.cases)assert.deepEqual([isExplicitStation(c.input),stationMode(c.input)??null,isGenericMrt(c.input)],[c.explicit_station,c.requested_mode,c.generic_mrt],JSON.stringify(c.input));
});
test('TS distances still equal the shared geography-distance-parity fixture (URA polygon / LTA nearest exit)',async()=>{
  const fixture=JSON.parse(await readFile('tests/fixtures/geography-distance-parity.json','utf8')) as {points:[number,number][];distances:Record<string,(number|null)[]>};
  const tools=new ToolExecutor(),data=new DataHttpAdapter('http://127.0.0.1:8089',tools,async()=>randomUUID());data.request=async()=>{throw Error('catalog IDs must not need the Data API');};
  const locations=new LocationService(data,tools,async()=>randomUUID());
  const ctx:ToolContext={sessionId:randomUUID(),operationId:randomUUID(),agent:'b',dataset:'unit-test',deadline:Date.now()+120000,calls:0};
  for(const [id,expected] of Object.entries(fixture.distances)){
    const entity=(await locations.get(ctx,id)).entity;
    fixture.points.forEach(([lat,lon],i)=>assert.equal(distanceToEntity(lat,lon,entity),expected[i],`${id} #${i}`));
  }
});
