import type {Criterion,RecommendationRequest,RecommendationResponse,Candidate,Evidence} from '../contracts/recommendation-v1.js';
import {validateResponse} from '../contracts/recommendation-v1.js';
import {nativeResponseSchema,nativeListingSchema,nativeRequestSchema,coldStartInputSchema,NOT_EXITED,NATIVE_MAX_RESULTS,type ColdStartInput,type NativeResponse,type NativeListing,type NativeScores} from '../contracts/recommendation-native.js';
import type {ToolContext} from '../tools/executor.js';

const fields:Record<string,string>={price:'price',bedrooms:'bedrooms',floor_area_sqft:'floor_area_sqft',district:'district_code',property_type:'property_type',place_proximity:'distance',commute_time:'commute',furnishing:'furnishing',current_availability:'availability',lease_months:'lease_months'};
// Pure: the native body is a function of the persisted V1 request (with its behavior_context) and the
// persisted cold-start state only, so transport retries and projectNative rebuild the identical body
// (native idempotency).
export function nativeRequest(req:RecommendationRequest,coldStart:ColdStartInput=NOT_EXITED){
  const cold=coldStartInputSchema.parse(coldStart);
  const unsupported:Criterion[]=[],criteria:Record<string,unknown>[]=[];
  for(const c of req.requirements.criteria){
    const field=fields[c.field];
    if(!field||(field==='distance'&&!['straight_line','walking','transit'].includes(c.distance_type||''))||(field==='commute'&&!['walk','transit','drive'].includes(c.transport_mode||''))){unsupported.push(c);continue;}
    const value=c.field==='property_type'?(Array.isArray(c.value)?c.value.map(x=>String(x).trim().toLowerCase()):String(c.value).trim().toLowerCase()):c.field==='current_availability'?(c.value?'available':'unavailable'):c.value;
    const criterion:Record<string,unknown>={criterion_id:c.criterion_id,field,operator:c.operator,value,strength:c.strength,weight:c.weight??1,utility:c.strength==='soft'&&field==='distance'&&c.operator==='lte'&&typeof c.value==='number'&&c.value>0?'proximity':'threshold'};
    if(c.unit&&field!=='bedrooms')criterion.unit=field==='commute'?'minutes':field==='lease_months'?'months':c.unit;
    if(field==='distance'||field==='commute'){
      // An ID travels with its name: native verifies the ID, searches the name when the ID is invalid
      // or unsupported, and reports an ID/name conflict as ambiguous (plan Step 5).
      if(c.target?.place_id){criterion.destination_id=c.target.place_id;if(c.target.name)criterion.destination_name=c.target.name;}
      else if(c.target?.category)criterion.category=c.target.category;
      else if(c.target?.name)criterion.destination_name=c.target.name;
      else{unsupported.push(c);continue;}
      criterion.distance_type=c.distance_type;
      if(field==='commute')criterion.transport_mode=c.transport_mode;
    }
    criteria.push(criterion);
  }
  // Main contract: real completed intervals are admitted by the behavior ledger and forwarded as
  // feedback events (validated, not re-scored natively); behavior_context carries the cumulative
  // browsing summary and saved viewing drafts that native scores. Saves and image clicks are not
  // invented durations. cold_start is the recommendation-owned extension.
  const supported=(f:RecommendationRequest['feedback'][number])=>f.event_type==='active_view'&&['sample','web_detail'].includes(String(f.payload.source));
  const feedback=req.feedback.filter(supported).map(f=>({event_id:f.event_id,conversation_id:req.conversation_id,recommendation_request_id:f.payload.recommendation_request_id,listing_id:f.listing_id,event_type:'listing_view_duration' as const,source:f.payload.source as 'sample'|'web_detail',occurred_at:f.occurred_at,payload:{view_session_id:f.payload.view_session_id as string,duration_seconds:Number(f.payload.active_duration_ms)/1000}}));
  const previous=req.previous_state_id?.startsWith('native:')?req.previous_state_id.slice(7):null;
  const request={contract_version:'1.0' as const,request_id:req.request_id,conversation_id:req.conversation_id,requirements_version:req.requirements_version+1,requirements:{listing_type:'RENT' as const,criteria},feedback,...(req.behavior_context?{behavior_context:req.behavior_context}:{}),previous_state_id:previous,result_limit:Math.min(NATIVE_MAX_RESULTS,req.result_limit),cold_start:{exited:Boolean(cold.exited),exited_pool_key:cold.exited_pool_key??null}};
  nativeRequestSchema.parse(request);
  return {request,unsupported,ignored_feedback:req.feedback.filter(f=>!supported(f)).map(f=>f.event_id)};
}

type Counts={assessed:number;eligible:number;unverified:number;excluded:number};
export type NativeEnvelope={request?:Record<string,unknown>;transport_request?:Record<string,unknown>;response:NativeResponse;presentation:{listings:NativeListing[];counts:Counts|null}};
export async function projectNative(req:RecommendationRequest,ctx:ToolContext,envelope:unknown,save:(ctx:ToolContext,body:unknown)=>Promise<string>,coldStart:ColdStartInput=NOT_EXITED):Promise<RecommendationResponse>{
  const input=nativeRequest(req,coldStart),raw=envelope as NativeEnvelope,native=nativeResponseSchema.parse(raw.response);
  if(native.request_id!==req.request_id||native.conversation_id!==req.conversation_id||native.requirements_version!==input.request.requirements_version)throw Error('Native response identity mismatch');
  const proofs=raw.presentation.listings.map(x=>nativeListingSchema.parse(x));
  const byId=new Map(proofs.map(x=>[x.listing_id,x]));
  const expected=new Set(input.request.requirements.criteria.map(c=>String(c.criterion_id)));
  for(const item of native.recommendations){
    const proof=byId.get(item.listing.listing_id);if(!proof||JSON.stringify(proof)!==JSON.stringify(item.listing))throw Error('Missing native listing proof');
    const seen=new Set(item.criterion_results.map(r=>r.criterion_id));
    if(seen.size!==expected.size||seen.size!==item.criterion_results.length||[...expected].some(id=>!seen.has(id)))throw Error('Native criterion results incomplete');
  }
  // Archive the lossless native exchange once. A/C/D receive a small UI contract,
  // not megabytes of hidden pool records or a rewritten native score.
  const archive=await save(ctx,{provider:'jiangshan-native',upstream:'907cfa8',contract:'2.0',request:raw.request??input.request,transport_request:raw.transport_request??input.request,response:raw.response,presentation:raw.presentation,unsupported_criteria:input.unsupported,ignored_feedback:input.ignored_feedback,cold_start:input.request.cold_start});
  const evidence:Evidence[]=[],scores:Record<string,NativeScores>={},nativeResults:Record<string,unknown>={};
  const hardGap=input.unsupported.some(c=>c.strength==='hard');
  // Native 2.0 returns only verified recommendations (no unverified_candidates).
  const rows=native.recommendations.map(item=>({listing:item.listing,results:item.criterion_results,item}));
  const recommendations:Candidate[]=[],unverified:Candidate[]=[];
  for(const row of rows){
    const id=row.listing.listing_id;if(!/^\d+$/.test(id))throw Error('Production listing IDs must be canonical numeric IDs');
    nativeResults[id]=row.results;if(row.item)scores[id]=row.item.scores;
    const results:Candidate['criterion_results']=req.requirements.criteria.map(c=>{
      const result=row.results.find(r=>r.criterion_id===c.criterion_id),geo=native.evidence.find(e=>e.listing_id===id&&e.criterion_id===c.criterion_id);
      let value:unknown=result&&['pass','fail'].includes(result.outcome)?(c.field==='place_proximity'?geo?.value??null:(row.listing as Record<string,unknown>)[fields[c.field]]??null):null;
      // Case-folded native categorical equality is rendered using the user's
      // equivalent spelling; the archived ListingSummary is never altered.
      if(c.field==='property_type'&&typeof value==='string'){
        const alternatives=Array.isArray(c.value)?c.value:[c.value];value=alternatives.find(x=>String(x).trim().toLowerCase()===String(value).toLowerCase())??value;
      }
      const outcome=!result||['unknown','unsupported'].includes(result.outcome)?'unknown':result.outcome as 'pass'|'fail';
      const eid=`native:${req.request_id}:${id}:${c.criterion_id}`;
      evidence.push({evidence_id:eid,listing_id:id,field_path:c.field,value,...(c.unit?{unit:c.unit}:{}),...(c.target?{target:c.target}:{}),source_kind:'data_api',source_ref:archive,dataset_version:ctx.dataset,retrieved_at:geo?.observed_at||new Date().toISOString(),verified_at:null,...(c.field==='place_proximity'?{distance_type:c.distance_type,raw_distance_type:geo?.distance_type??undefined,route_verified:false,measurement_method:geo?.method||'unavailable'}:{})});
      return {criterion_id:c.criterion_id,status:outcome,observed_value:value,...(c.unit?{unit:c.unit}:{}),evidence_ids:[eid],reason:!result?`当前原生推荐契约未提供该字段；原始要求已保留，${c.strength==='soft'?'尚未纳入原生评分':'尚未验证'}。`:result.outcome==='unsupported'?`原生推荐模块暂不支持验证：${result.reason}`:result.outcome==='unknown'?`原生证据未知：${result.reason}`:result.reason};
    });
    const s=row.item?.scores;
    // The two totals are a projection of the ORIGINAL fused score. Per-feature
    // contributions, suppression and coverage stay lossless in native.scores.
    const breakdown:Candidate['score_breakdown']=s?[
      ...(s.soft_weight&&s.soft_score!==null?[{criterion_id:'native:soft',origin:'stated_preference' as const,utility:s.soft_score,weight:s.soft_weight,normalized_weight:s.soft_weight,contribution:s.soft_weight*s.soft_score,evidence_ids:results.flatMap(r=>r.evidence_ids)}]:[]),
      ...(s.behavior_weight&&s.behavior_score!==null?[{criterion_id:'native:behavior',origin:'inferred_interest' as const,utility:s.behavior_score,weight:s.behavior_weight,normalized_weight:s.behavior_weight,contribution:s.behavior_weight*s.behavior_score,evidence_ids:results.flatMap(r=>r.evidence_ids)}]:[]),
    ]:[];
    const r=row.listing;
    const candidate:Candidate={listing:{listing_id:id,title:r.title||'未命名房源',price:r.price,price_unit:'SGD/month',bedrooms:r.bedrooms,district:r.district_code,source_url:r.url,availability_status:'unknown',floor_area_sqft:r.floor_area_sqft,property_type:r.property_type},rank:1,score:s?.combined_score??0,score_breakdown:breakdown,criterion_results:results,explanation_evidence:{strengths:results.filter(x=>x.status==='pass').map(x=>({criterion_ids:[x.criterion_id],evidence_ids:x.evidence_ids})),compromises:results.filter(x=>x.status==='fail').map(x=>({criterion_ids:[x.criterion_id],evidence_ids:x.evidence_ids})),unknowns:results.filter(x=>x.status==='unknown').map(x=>({field:req.requirements.criteria.find(c=>c.criterion_id===x.criterion_id)!.field,evidence_ids:x.evidence_ids}))}};
    // This is a PRESENTATION safety guard for requirements the native contract
    // cannot carry, never a native re-ranking, filtering or substitute algorithm.
    const group=row.item&&!hardGap?recommendations:unverified;candidate.rank=group.length+1;group.push(candidate);
  }
  let counts=raw.presentation.counts;
  if(counts&&hardGap)counts={...counts,eligible:0,unverified:counts.unverified+counts.eligible};
  const response={contract_version:'1.0',request_id:req.request_id,conversation_id:req.conversation_id,requirements_version:req.requirements_version,
    status:native.status==='error'?'error':recommendations.length?'success':unverified.length?'no_match':native.status,
    recommendations,unverified_candidates:unverified,evidence,
    clarifications:unverified.length?[]:native.clarifications.map(question=>({criterion_ids:[],question})),relaxation_suggestions:[],
    warnings:[{code:'JIANGSHAN_NATIVE',message:`使用江山原生推荐模块（契约 2.0）；对完整探索池评分，按其选择策略返回最多${NATIVE_MAX_RESULTS}套，保留评分、选择原因和原生展示顺序。`},...native.warnings.map(message=>({code:'NATIVE_NOTICE',message})),...(input.unsupported.length?[{code:'NATIVE_CONTRACT_GAP',message:`以下要求已保留，但原生契约没有对应字段，展示时保持待核实：${input.unsupported.map(c=>c.field).join('、')}。没有使用旧基线补排。`}]:[]),
      // Native resolves LTA stations (nearest recorded exit), URA areas/regions (0 inside, edge distance outside) and OSM places itself (P4), so no geography-gap warning.
      ...(input.ignored_feedback.length?[{code:'NATIVE_FEEDBACK_UNSUPPORTED',message:'收藏和显式评价已留存；原生行为模型只使用 behavior_context 的会话累计浏览摘要和已保存的看房意向；收藏和显式评价不会转换成浏览秒数或看房信号。'}]:[])],
    state:{state_id:`native:${native.state.state_id||req.request_id}`,candidate_pool_ref:archive,coverage:{dataset_version:ctx.dataset,dataset_scope:ctx.dataset,retrieval_scope:native.state.scope,complete_within_dataset:native.status!=='error'&&!native.state.truncated,truncated:native.state.truncated},counts:counts||{assessed:null,eligible:null,unverified:null,excluded:null}},
    execution:{strategy:native.execution.strategy,trace_ref:archive,tool_calls:[{provider:'jiangshan-native',count:native.execution.tool_calls,model_calls:native.execution.model_calls,native_trace_ref:native.execution.trace_ref}],timing:{total_ms:native.execution.duration_ms},scoring_version:native.execution.scoring_version,score_semantics:'native_soft_behavior_weighted_fit_not_probability'},error:native.error,
    native:{provider:'jiangshan-native',upstream_commit:'907cfa8',exchange_ref:archive,status:native.status,state_id:native.state.state_id,ranking:native.recommendations.map(x=>x.listing.listing_id),scores,criterion_results:nativeResults,unsupported_criterion_ids:input.unsupported.map(c=>c.criterion_id),unscored_listing_ids:[],
      contract:'2.0',pool:native.pool,selection:native.selection,resolution:native.resolution,config_hash:native.execution.config_hash,selection_reasons:Object.fromEntries(native.recommendations.map(x=>[x.listing.listing_id,{selection_reason:x.selection_reason,selection_note:x.selection_note,score_rank:x.score_rank}]))},
  };
  return validateResponse(response,req);
}

export async function callNative(req:RecommendationRequest,ctx:ToolContext,url:string,save:(ctx:ToolContext,body:unknown)=>Promise<string>,coldStart:ColdStartInput=NOT_EXITED){
  const input=nativeRequest(req,coldStart);
  const token=process.env.RECOMMENDATION_SERVICE_TOKEN;if(!token)throw Error('RECOMMENDATION_SERVICE_TOKEN is required');
  const timeout=Number(process.env.RECOMMENDATION_HTTP_TIMEOUT_MS||150000);
  // Retry transport/queue failures with EXACTLY the same body and request ID.
  for(let attempt=0;;attempt++){
    let raw:unknown;
    try{
      const r=await fetch(url.replace(/\/$/,'')+'/app/recommend',{method:'POST',headers:{'content-type':'application/json','x-recommendation-token':token},body:JSON.stringify(input.request),signal:AbortSignal.timeout(timeout),redirect:'error'});
      if(!r.ok){if(attempt===0&&[502,503,504].includes(r.status)){await new Promise(done=>setTimeout(done,1000));continue;}throw Error(`NATIVE_RECOMMENDATION_HTTP_${r.status}`);}
      const buffer=await r.arrayBuffer();if(buffer.byteLength>64*1024*1024)throw Error('Native response exceeds 64 MiB');raw=JSON.parse(Buffer.from(buffer).toString('utf8'));
    }catch(error){if(attempt===0&&!(error instanceof Error&&/HTTP_|64 MiB/.test(error.message)))continue;throw error;}
    return projectNative(req,ctx,raw,save,coldStart);
  }
}
