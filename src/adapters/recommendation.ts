import { randomUUID } from 'node:crypto';
import { DataHttpAdapter, type Entity } from './data-http.js';
import type { ToolContext } from '../tools/executor.js';
import { FIELD_RULES,compare,validateResponse,type Criterion,type Evidence,type Candidate,type RecommendationRequest,type RecommendationResponse } from '../contracts/recommendation-v1.js';
import type {LocationService} from './locations.js';
import {geodesic,distanceToEntity,proximityUtility} from '../domain/spatial.js';
import {unknownReason} from '../domain/field-capabilities.js';
import {callNative} from './native-recommendation.js';
export {geodesic} from '../domain/spatial.js';

function numeric(v:unknown){return typeof v==='number'&&Number.isFinite(v)&&v>=0?v:null;}
export function observed(properties:Record<string,unknown>,c:Criterion):unknown {
  if(FIELD_RULES[c.field].unsupported)return null;
  if(c.field==='price'&&(properties.currency!=='SGD'||properties.listing_type!=='RENT'))return null;
  const v=properties[c.field==='district'?'district_code':c.field];
  if(FIELD_RULES[c.field].kind==='number'||FIELD_RULES[c.field].kind==='integer')return numeric(v);
  return typeof v==='string'&&v.trim()?v:null;
}
export function classify(results:Candidate['criterion_results'],criteria:Criterion[]):'eligible'|'unverified'|'excluded'{
  const hard=criteria.filter(c=>c.strength==='hard').map(c=>results.find(r=>r.criterion_id===c.criterion_id)?.status??'unknown');
  return hard.includes('fail')?'excluded':hard.includes('unknown')?'unverified':'eligible';
}
export class RecommendationGateway {
  constructor(private data:DataHttpAdapter,private save:(ctx:ToolContext,body:unknown)=>Promise<string>,private mode:string,private remoteUrl:string|undefined,private locations?:LocationService){}
  async recommend(req:RecommendationRequest,ctx:ToolContext):Promise<RecommendationResponse>{
    if(this.mode==='native')return callNative(req,ctx,this.remoteUrl||'http://127.0.0.1:8092',this.save);
    if(this.mode==='remote'){
      if(!this.remoteUrl)throw Error('RECOMMENDATION_API_URL is required');
      // Logical retries use the exact persisted request body and ID.
      let last:unknown;
      for(let i=0;i<2;i++)try{
        const response=await fetch(this.remoteUrl.replace(/\/$/,'')+'/recommend',{method:'POST',headers:{'content-type':'application/json','idempotency-key':req.request_id},body:JSON.stringify(req),redirect:'error',signal:AbortSignal.timeout(30000)});
        if(!response.ok){if(response.status>=500){last=Error(`RECOMMENDATION_HTTP_${response.status}`);continue;}throw Error(`RECOMMENDATION_HTTP_${response.status}`);}
        const text=await response.text();if(Buffer.byteLength(text)>8*1024*1024)throw Error('Recommendation response too large');
        return validateResponse(JSON.parse(text),req);
      }catch(e){last=e;if(i)throw e;}
      throw last;
    }
    if(this.mode!=='baseline')throw Error('Recommendation integration is disabled');
    return this.baseline(req,ctx);
  }
  private async baseline(req:RecommendationRequest,ctx:ToolContext):Promise<RecommendationResponse>{
    const start=Date.now(),full=process.env.DATA_MODE==='full';
    const fullPool=full?await this.data.assessment(ctx):null;
    const pool=fullPool||await this.data.all(ctx,'listings',2000),criteria=req.requirements.criteria;
    if(full&&pool.truncated)throw Error('FULL_DATASET_INCOMPLETE');
    const geoProofs=new Map<string,unknown>();
    const needsPlaces=criteria.some(c=>c.field==='place_proximity'&&c.distance_type==='straight_line');
    const needsOsm=criteria.some(c=>c.field==='place_proximity'&&c.distance_type==='straight_line'&&c.target?.category!=='MRTStation'&&(!c.target?.place_id||! /^(LTA|URA):/.test(c.target.place_id)));
    const places=needsOsm?await this.data.all(ctx,'places',10000):{items:[] as Entity[],truncated:false,refs:[]};
    if(criteria.some(c=>c.field==='place_proximity'&&c.distance_type==='straight_line'&&c.target?.category==='MRTStation')){
      if(!this.locations)throw Error('LOCATION_SERVICE_REQUIRED');
      const stations=await this.locations.stations(ctx);places.items.push(...stations.items);places.refs.push(stations.ref);
    }
    for(const id of new Set(criteria.flatMap(c=>c.field==='place_proximity'&&c.target?.place_id&&/^(LTA|URA):/.test(c.target.place_id)?[c.target.place_id]:[]))){if(!this.locations)throw Error('LOCATION_SERVICE_REQUIRED');const found=await this.locations.get(ctx,id);places.items.push(found.entity);places.refs.push(found.ref);}
    if(places.truncated)throw Error('Place catalog is truncated');
    const evidence:Evidence[]=[],eligible:Candidate[]=[],unverified:Candidate[]=[],excluded:Candidate[]=[];
    const sourceRows:Record<string,unknown>[]=[];let cursor=0;
    const worker=async()=>{while(cursor<pool.items.length){const row=pool.items[cursor++],id=String(row.properties.listing_id??row.entity_id.replace(/^Listing:/,''));
      const detail=fullPool?{value:row.properties,ref:fullPool.rowRefs.get(row.entity_id)!}:await this.data.listing(ctx,id),p=detail.value;sourceRows.push(p);
      const results:Candidate['criterion_results']=[],breakdown:Candidate['score_breakdown']=[];
      for(const c of criteria){
        let value=observed(p,c),ref=detail.ref,method:string|undefined;
        if(c.field==='place_proximity'){
          value=null;const lat=numeric(p.latitude),lon=numeric(p.longitude);
          if(c.distance_type==='straight_line'&&lat!==null&&lon!==null&&lat>=1.15&&lat<=1.5&&lon>=103.55&&lon<=104.2){
            const candidates=places.items.filter(e=>(!c.target?.place_id||e.properties.place_id===c.target.place_id)&&(!c.target?.category||e.properties.category===c.target.category));
            let nearest:{distance:number;place:Entity}|null=null;
            for(const place of candidates){const distance=distanceToEntity(lat,lon,place);if(distance===null)continue;if(!nearest||distance<nearest.distance)nearest={distance,place};}
            if(nearest){value=nearest.distance;method=nearest.place.properties.geometry?'turf.geodesic.distance_to_area_zero_inside':nearest.place.properties.reference_points?'haversine.nearest_recorded_exit':'haversine.WGS84_spherical';geoProofs.set(`e:${req.request_id}:${id}:${c.criterion_id}`,{listing_ref:detail.ref,place_catalog_refs:places.refs,target_id:nearest.place.entity_id,latitude:lat,longitude:lon,distance_m:value,measurement_method:method,route_verified:false});}
          }
        }
        const eid=`e:${req.request_id}:${id}:${c.criterion_id}`,status=value===null?'unknown':compare(value,c)?'pass':'fail';
        evidence.push({evidence_id:eid,listing_id:id,field_path:c.field,value,...(c.unit?{unit:c.unit}:{}),...(c.target?{target:c.target}:{}),...(c.field==='place_proximity'?{distance_type:'straight_line',raw_distance_type:'geodesic',route_verified:false,measurement_method:method||'unavailable'}:{}),source_kind:'data_api',source_ref:ref,dataset_version:ctx.dataset,retrieved_at:new Date().toISOString(),verified_at:null});
        results.push({criterion_id:c.criterion_id,status,observed_value:value,...(c.unit?{unit:c.unit}:{}),evidence_ids:[eid],...(status==='unknown'?{reason:unknownReason(c)}:method==='turf.geodesic.distance_to_area_zero_inside'?{reason:value===0?'位于所选规划区内，按区域集合距离记为0；不表示距地铁站、中心点或任意具体地标0米。':'区域外到官方区域边界的最近直线距离；不是到地铁站或区域中心的距离。'}:{})});
        if(c.strength==='soft'){
          // Missing soft data contributes zero, with the full declared weight denominator.
          let utility=status==='pass'?1:0;
          if(value!==null&&c.operator==='lte'&&typeof value==='number'&&typeof c.value==='number'&&c.value>0)utility=c.field==='place_proximity'?proximityUtility(value,c.value):Math.max(0,Math.min(1,1-value/(2*c.value)));
          const denom=criteria.filter(x=>x.strength==='soft').reduce((n,x)=>n+x.weight!,0);
          breakdown.push({criterion_id:c.criterion_id,origin:'stated_preference',utility,weight:c.weight!,normalized_weight:c.weight!/denom,contribution:utility*c.weight!/denom,evidence_ids:[eid]});
        }
      }
      const candidate:Candidate={listing:{listing_id:id,title:String(p.title||'未命名房源'),price:observed(p,{field:'price'} as Criterion) as number|null,price_unit:'SGD/month',bedrooms:numeric(p.bedrooms),district:typeof p.district_code==='string'?p.district_code:null,source_url:typeof p.url==='string'?p.url:null,availability_status:'unknown',floor_area_sqft:numeric(p.floor_area_sqft),property_type:typeof p.property_type==='string'?p.property_type:null,thumbnail_url:typeof p.thumbnail_url==='string'?p.thumbnail_url:null},rank:1,score:breakdown.length?breakdown.reduce((n,x)=>n+x.contribution,0):0,criterion_results:results,score_breakdown:breakdown,explanation_evidence:{strengths:results.filter(x=>x.status==='pass').map(x=>({criterion_ids:[x.criterion_id],evidence_ids:x.evidence_ids})),compromises:results.filter(x=>x.status==='fail').map(x=>({criterion_ids:[x.criterion_id],evidence_ids:x.evidence_ids})),unknowns:results.filter(x=>x.status==='unknown').map(x=>({field:criteria.find(c=>c.criterion_id===x.criterion_id)!.field,evidence_ids:x.evidence_ids}))}};
      const group=classify(results,criteria);(group==='eligible'?eligible:group==='unverified'?unverified:excluded).push(candidate);
    }};
    await Promise.all(Array.from({length:8},worker));
    const order=(a:Candidate,b:Candidate)=>b.score-a.score||(a.listing.price??Infinity)-(b.listing.price??Infinity)||(BigInt(a.listing.listing_id)>BigInt(b.listing.listing_id)?1:-1);
    eligible.sort(order);unverified.sort(order);
    const poolRef=await this.save(ctx,{request:req,assessed:sourceRows.map(x=>String(x.listing_id)),eligible:eligible.map(x=>x.listing.listing_id),unverified:unverified.map(x=>x.listing.listing_id),excluded:excluded.map(x=>x.listing.listing_id),feedback_consumed:req.feedback.map(f=>f.event_id),feedback_ranking_enabled:false});
    const selected=eligible.slice(0,req.result_limit).map((c,i)=>({...c,rank:i+1})),uv=unverified.slice(0,5).map((c,i)=>({...c,rank:i+1}));
    const used=new Set([...selected,...uv].flatMap(c=>c.criterion_results.flatMap(x=>x.evidence_ids)));
    const suggestions:RecommendationResponse['relaxation_suggestions']=[];
    if(!selected.length){for(const cap of criteria.filter(c=>c.field==='price'&&c.strength==='hard'&&c.operator==='lte')){
      const others=criteria.filter(c=>c.strength==='hard'&&c.criterion_id!==cap.criterion_id);
      const alternative=[...eligible,...unverified,...excluded].filter(c=>c.listing.price!==null&&c.listing.price>Number(cap.value)&&others.every(x=>c.criterion_results.find(r=>r.criterion_id===x.criterion_id)?.status==='pass')).sort((a,b)=>a.listing.price!-b.listing.price!)[0];
      if(alternative){const evidenceIds=alternative.criterion_results.flatMap(x=>x.evidence_ids);evidenceIds.forEach(id=>used.add(id));suggestions.push({criterion_id:cap.criterion_id,proposed_value:alternative.listing.price,reason:`保持其余硬条件不变，已评估样本中最低已记录月租为 SGD ${alternative.listing.price}/月（房源 ${alternative.listing.listing_id}）。仅建议调整，尚未修改需求。`,evidence_ids:evidenceIds});}
    }}
    // Persist derived evidence for displayed candidates and relaxation proofs only.
    // All records are assessed above; avoid tens of thousands of redundant writes.
    for(const e of evidence)if(used.has(e.evidence_id)&&geoProofs.has(e.evidence_id))e.source_ref=await this.save(ctx,geoProofs.get(e.evidence_id));
    return validateResponse({contract_version:'1.0',request_id:req.request_id,conversation_id:req.conversation_id,requirements_version:req.requirements_version,status:selected.length?'success':'no_match',recommendations:selected,unverified_candidates:uv,evidence:evidence.filter(e=>used.has(e.evidence_id)),clarifications:[],relaxation_suggestions:suggestions,warnings:[{code:'LOCAL_BASELINE',message:'本地可解释规则基线用于联调；尚未接入崔同学的推荐 Agent、行为兴趣模型。'},{code:'SNAPSHOT_ONLY',message:`${ctx.dataset} 历史快照，当前可租状态均未核实。`},{code:'LIMITED_DATASET',message:'覆盖范围仅为当前安装的数据集，不代表新加坡全市场。'},...(req.feedback.length?[{code:'FEEDBACK_NOT_RERANKED',message:'反馈已保存，本地基线暂不使用行为信号改分；明确需求优先。'}]:[])],state:{state_id:`baseline:${req.request_id}`,candidate_pool_ref:poolRef,coverage:{dataset_version:ctx.dataset,dataset_scope:ctx.dataset,retrieval_scope:full?'Complete canonical SQL RENT scan; no hard pushdown; nulls retained':'RENT records from data API; no hard pushdown; nulls retained',complete_within_dataset:!pool.truncated,truncated:pool.truncated},counts:{assessed:pool.items.length,eligible:eligible.length,unverified:unverified.length,excluded:excluded.length}},execution:{strategy:'deterministic_local_baseline',trace_ref:poolRef,tool_calls:[{name:'listings/search',source_refs:pool.refs},{name:full?'canonical_assessment_scan':'listing_detail',count:full?pool.refs.length:pool.items.length},...(needsPlaces?[{name:'places/search',source_refs:places.refs}]:[])],timing:{total_ms:Date.now()-start},scoring_version:'baseline-explicit-soft-v2-geospatial-pivot',score_semantics:'explicit_preference_fit_not_probability'},error:null},req);
  }
}
