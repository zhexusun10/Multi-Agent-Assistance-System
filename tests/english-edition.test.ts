import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {INTAKE_EXAMPLES} from '../src/agents/requirement/few-shot.js';
import {modelIntakeSchema} from '../src/agents/requirement/contracts.js';
import {normalizeNearby} from '../src/domain/listing-presentation.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {validateExplanation} from '../src/agents/explanation/validation.js';

test('translated intake examples retain strict contracts and exact quotation provenance',()=>{
  for(const example of INTAKE_EXAMPLES){
    modelIntakeSchema.parse(example.output);
    for(const section of ['upsert','locations','removals','coverage'] as const){
      for(const item of example.output[section])if('quote' in item)assert(example.query.includes(item.quote),example.id+': '+section);
    }
    assert(/[a-z]{3}/i.test(example.query),example.id);
  }
});

test('English display prefers an available source English name and retains geometry/evidence',()=>{
  const rows:any=[{type:'NEAR_PLACE',target:{entity_id:'OSM:node:123',label:'Place',properties:{name_en:'FairPrice',name:'FairPrice',name_local:'职总平价超市',category:'Supermarket'}},evidence:{distance_m:60.29,radius_m:1500}}];
  const before=JSON.stringify(rows),out=normalizeNearby(rows);
  assert.equal(out[0].name,'FairPrice');assert.equal(out[0].distance_m,60.29);
  assert.equal(out[0].metric,'straight_line');assert.equal(out[0].route_verified,false);
  assert.equal(JSON.stringify(rows),before);
});

test('official source names may retain their own language but unrelated Chinese prose is rejected',()=>{
  const f=JSON.parse(readFileSync(new URL('./fixtures/explanation-refinement-regression.json',import.meta.url),'utf8'));
  const bundle=makeExplanationBundle(f.requirements,f.recommendation,'Help me choose between the homes.');
  bundle.facts.push({ref:'source-name',kind:'listing',listing_ids:[],text:'A supplied name',source_refs:[],numbers:[],literals:['职总平价超市']});
  const raw={version:'2.0',focus_listing_ids:[],paragraphs:[{text:'FairPrice (职总平价超市) is a supplied place name. Please confirm current rent and availability.',support_refs:[...bundle.required_refs,'source-name']}]};
  validateExplanation(raw,bundle);
  raw.paragraphs[0].text+='这些房源可以满足您的所有要求，请您马上预约。';
  assert.throws(()=>validateExplanation(raw,bundle),/prose in English/);
});
