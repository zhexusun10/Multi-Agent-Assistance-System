import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {writeFile} from 'node:fs/promises';
import pg from 'pg';
import {nativeRequest,projectNative} from '../src/adapters/native-recommendation.js';
import {recommendationRequestSchema} from '../src/contracts/recommendation-v1.js';

test('native geographic ranking matches independently computed haversine/NEAR_PLACE values and guarded unsupported requirements',{skip:process.env.NATIVE_GEO_LIVE!=='1',timeout:240000},async()=>{
 const sql=new pg.Pool({connectionString:process.env.PROPERTYGURU_DATABASE_URL!.replace('postgresql+psycopg2:','postgresql:')});
 try{
   const destination=(await (await fetch('http://127.0.0.1:8089/api/v1/places/search',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({filters:{q:'National University of Singapore'},page_size:50})})).json()).items.find((p:any)=>p.properties.place_id==='OSM:way:54519165').properties;
   const req=recommendationRequestSchema.parse({contract_version:'1.0',request_id:randomUUID(),conversation_id:randomUUID(),requirements_version:1,result_limit:5,requirements:{listing_type:'RENT',criteria:[{criterion_id:'budget',field:'price',operator:'lte',value:3000,unit:'SGD/month',strength:'hard'},{criterion_id:'district',field:'district',operator:'eq',value:'D05',strength:'hard'},{criterion_id:'radius',field:'place_proximity',operator:'lte',value:3000,unit:'m',strength:'hard',target:{place_id:destination.place_id,name:destination.name},distance_type:'straight_line'},{criterion_id:'near',field:'place_proximity',operator:'lte',value:1000,unit:'m',strength:'soft',weight:1,target:{place_id:destination.place_id,name:destination.name},distance_type:'straight_line'}]}});
   const nativeInput=nativeRequest(req).request,r=await fetch(process.env.RECOMMENDATION_API_URL+'/app/recommend',{method:'POST',headers:{'content-type':'application/json','x-recommendation-token':process.env.RECOMMENDATION_SERVICE_TOKEN!},body:JSON.stringify(nativeInput),signal:AbortSignal.timeout(160000)});assert(r.ok);const envelope=await r.json();assert.equal(envelope.response.status,'success',JSON.stringify(envelope.response.error));
   const rad=(v:number)=>v*Math.PI/180;
   const distance=(p:any)=>{if(p.latitude===null||p.longitude===null)return null;const h=Math.sin(rad(destination.latitude-p.latitude)/2)**2+Math.cos(rad(p.latitude))*Math.cos(rad(destination.latitude))*Math.sin(rad(destination.longitude-p.longitude)/2)**2;return 6371008.8*2*Math.asin(Math.sqrt(Math.max(0,Math.min(1,h))));};
   const properties=(await sql.query("SELECT listing_id,price,district_code,latitude,longitude FROM properties WHERE listing_type='RENT' ORDER BY listing_id::text")).rows;
   const source=properties.filter(p=>Number(p.price)<=3000&&p.price!==null&&p.district_code==='D05');
   // Native reuses existing graph distances if they agree within one meter.
   // Read those recorded values independently from the data API's nearby endpoint
   // is unnecessary for ordering: this fixture's selected points use coordinate fallback.
   const eligible=source.map(p=>({...p,d:distance(p)})).filter(p=>p.d!==null&&p.d<=3000).sort((a,b)=>b.d===a.d?(String(a.listing_id)<String(b.listing_id)?-1:1):a.d!-b.d!);
   const native=envelope.response;assert.equal(envelope.presentation.counts.assessed,21431);assert.equal(envelope.presentation.counts.eligible,eligible.length);
   assert.deepEqual(native.recommendations.map((c:any)=>c.listing.listing_id),eligible.slice(0,4).map(p=>String(p.listing_id)));
   for(const c of native.recommendations){const p=eligible.find(p=>String(p.listing_id)===c.listing.listing_id)!;assert(Math.abs(c.scores.combined_score-1/(1+p.d!/1000))<.001);assert(c.criterion_results.find((x:any)=>x.criterion_id==='radius').outcome==='pass');}
   let saved:any;const ctx={sessionId:req.conversation_id,operationId:randomUUID(),agent:'b' as const,dataset:'2026-10-09',deadline:Date.now()+120000,calls:0};const save=async(_ctx:unknown,body:unknown)=>{saved=body;return randomUUID();};
   const rec=await projectNative(req,ctx,envelope,save);assert.deepEqual(rec.native!.ranking,native.recommendations.map((x:any)=>x.listing.listing_id));assert.deepEqual(saved.response,native);
   req.requirements.criteria.push({criterion_id:'private',field:'amenities',operator:'eq',value:'private_bathroom',strength:'hard'});
   const guarded=await projectNative(req,ctx,envelope,save);assert.equal(guarded.recommendations.length,0);assert.equal(guarded.native!.status,'success');assert(guarded.unverified_candidates.every(c=>c.criterion_results.find(v=>v.criterion_id==='private')?.status==='unknown'));assert.deepEqual(saved.response,native);
   await writeFile('evaluation/reports/native-geographic-oracle.json',JSON.stringify({status:'passed',dataset:'2026-10-09',assessed:21431,eligible:eligible.length,native_ids:native.recommendations.map((c:any)=>c.listing.listing_id),selected:native.recommendations.map((c:any)=>({listing_id:c.listing.listing_id,score:c.scores.combined_score,oracle_distance_m:eligible.find(p=>String(p.listing_id)===c.listing.listing_id)!.d})),preserved_native_response:true,unsupported_hard_guard:true,execution:native.execution},null,2));
 }finally{await sql.end();}
});
