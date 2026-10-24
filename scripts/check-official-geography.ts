/** Real SQL/KG + native algorithms, isolated scripted runs only. No paid models. */
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {resolve} from 'node:path';
import {randomUUID} from 'node:crypto';
import pg from 'pg';
import {nativeRequest,projectNative} from '../src/adapters/native-recommendation.js';
import {recommendationRequestSchema} from '../src/contracts/recommendation-v1.js';
import {LocationService} from '../src/adapters/locations.js';
import {distanceToEntity} from '../src/domain/spatial.js';
import {DataHttpAdapter} from '../src/adapters/data-http.js';
import {ExplanationEnricher} from '../src/agents/explanation/enrichment.js';
import {makeExplanationBundle} from '../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../src/agents/explanation/decision-support.js';
import {recoverExplanation} from '../src/agents/explanation/recovery.js';
import {ToolExecutor,type ToolContext} from '../src/tools/executor.js';
import {matchSummary} from '../frontend/property-presentation.js';

const f=JSON.parse(await readFile('tests/fixtures/explanation-refinement-regression.json','utf8'));
const pool=new pg.Pool({connectionString:process.env.PROPERTYGURU_DATABASE_URL!.replace('postgresql+psycopg2:','postgresql:')});
const report:any={status:'running',model_calls:0,checks:[]};
await mkdir('data/.runtime',{recursive:true});
try{
 const data=new DataHttpAdapter(process.env.DATA_API_URL||'http://127.0.0.1:8089',new ToolExecutor(),async()=>randomUUID());
 const target=await new LocationService(data,new ToolExecutor(),async()=>randomUUID()).describe('URA:AREA:CLEMENTI');assert(target);
 for(const refined of [false,true]){
  const requirements={...f.requirements,criteria:f.requirements.criteria.filter((c:any)=>refined||c.field!=='floor_area_sqft')};
  const req=recommendationRequestSchema.parse({contract_version:'1.0',request_id:randomUUID(),conversation_id:randomUUID(),requirements_version:refined?2:1,requirements,feedback:[],result_limit:5});
  const input=resolve('data/.runtime/official-geo-input.json'),output=resolve('data/.runtime/official-geo-output.json');
  await writeFile(input,JSON.stringify(nativeRequest(req).request));
  await promisify(execFile)(resolve('.venv/Scripts/python.exe'),['-m','scripts.check_top5_data',input,output],{timeout:120000,maxBuffer:100000});
  const envelope=JSON.parse(await readFile(output,'utf8'));assert.equal(envelope.response.execution.model_calls,0);
  const sql=`SELECT listing_id,title,price,bedrooms,floor_area_sqft,latitude,longitude FROM properties WHERE listing_type='RENT' AND price<=3000 AND bedrooms=1 AND lower(property_type)='condominium'${refined?' AND floor_area_sqft BETWEEN 500 AND 1500':''}`;
  const candidates=(await pool.query(sql)).rows;
  const oracle=candidates.flatMap(l=>{if(l.latitude===null||l.longitude===null)return [];const distance=distanceToEntity(Number(l.latitude),Number(l.longitude),target.entity);return distance===null?[]:[{...l,distance,id:String(l.listing_id)}];}).sort((a,b)=>a.distance-b.distance||(a.id<b.id?-1:1));
  assert(oracle.length>=5);assert.deepEqual(envelope.response.recommendations.map((r:any)=>r.listing.listing_id),oracle.slice(0,5).map(l=>l.id));
  const archived=new Map<string,unknown>(),save=async(_ctx:unknown,body:unknown)=>{const id=randomUUID();archived.set(id,body);return id;};
  const ctx:ToolContext={agent:'b',sessionId:req.conversation_id,operationId:randomUUID(),dataset:'2026-10-09',deadline:Date.now()+120000,calls:0};
  const rec=await projectNative(req,ctx,envelope,save),criterion=req.requirements.criteria.find(c=>c.field==='place_proximity')!;
  for(const [index,candidate] of rec.recommendations.entries()){
   const result=candidate.criterion_results.find(r=>r.criterion_id===criterion.criterion_id)!;
   assert.notEqual(result.status,'unknown');assert.equal(result.observed_value,oracle[index].distance);
   assert(Math.abs(candidate.score-1/(1+oracle[index].distance/1000))<1e-12);
   const card=matchSummary(candidate,req.requirements.criteria,(c:any)=>c.field);
   assert(card.rows.some((r:any)=>r.text.includes('金文泰区域边界')||r.text.includes('金文泰区域内')));
   assert(!card.pending.some((p:string)=>p.includes('金文泰')||p.includes('CLEMENTI')));
  }
  report.checks.push({case:refined?'after_card_refinement':'before_card_refinement',passed:true,hard_match_count:candidates.length,known_coordinate_count:oracle.length,missing_coordinate_count:candidates.length-oracle.length,ranking_matches_full_pool_oracle:true,top5:oracle.slice(0,5).map(l=>({id:l.id,title:l.title,boundary_distance_m:l.distance}))});
  if(refined){
   const extra=await new ExplanationEnricher(data,new ToolExecutor(),save).load(rec,{...ctx,agent:'c'});
   const bundle=addDecisionSupport(makeExplanationBundle(req.requirements,rec,'介绍这些房源',archived.get(rec.native!.exchange_ref)),req.requirements,rec,extra);
   const narrative=recoverExplanation(bundle,req.requirements,rec);
   assert(narrative.paragraphs.filter(p=>p.listing_id).every(p=>p.text.includes('金文泰区域边界')||p.text.includes('金文泰区域内')));
   await writeFile('data/.runtime/official-geo-ui-result.json',JSON.stringify({requirements:req.requirements,recommendation:rec,narrative,summary:narrative.paragraphs.map(p=>p.text).join('\n\n')}));
  }
 }
 const oldIds=f.recommendation.recommendations.map((r:any)=>r.listing.listing_id);
 const originals=(await pool.query('SELECT listing_id,title,latitude,longitude FROM properties WHERE listing_id=ANY($1::bigint[])',[oldIds])).rows;
 assert(originals.every(r=>r.latitude===null&&r.longitude===null));
 report.original_screenshot_homes={all_five_missing_coordinates:true,homes:originals.map(r=>({listing_id:String(r.listing_id),title:r.title}))};report.status='passed';
}finally{await pool.end();await mkdir('evaluation/reports',{recursive:true});await writeFile('evaluation/reports/official-geography.json',JSON.stringify(report,null,2)+'\n');}
console.log(JSON.stringify(report));
