import {randomUUID} from 'node:crypto';
import type {Candidate,Criterion,Requirements,RecommendationResponse} from '../src/contracts/recommendation-v1.js';
import type {NativeScores} from '../src/contracts/recommendation-native.js';
export const SCENARIOS=['distance','price','multiple','behavior','hybrid','stable','tie','hard_unknown','unscored','empty','unknown_soft','area','numeric_titles','mixed_groups','bath_count','jurong_layout','close_distance','district','district_mixed','postal_date'] as const;
export type Scenario=typeof SCENARIOS[number];
export function explanationFixture(scenario:Scenario){
  if(['district','district_mixed','postal_date'].includes(scenario)){
    const f=explanationFixture('stable'),mixed=scenario==='district_mixed';
    f.req.criteria=[
      {criterion_id:'rent',field:'price',operator:'lte',value:5000,unit:'SGD/month',strength:'hard'},
      {criterion_id:'bedrooms',field:'bedrooms',operator:'gte',value:2,unit:'count',strength:'hard'},
      {criterion_id:'district',field:'district',operator:'eq',value:'D05',strength:'hard'},
      ...(scenario==='postal_date'?[
        {criterion_id:'postcode',field:'postal_code',operator:'eq',value:'139409',strength:'hard'},
        {criterion_id:'move-in',field:'move_in_date',operator:'lte',value:'2026-12-01',strength:'hard'},
      ] as Criterion[]:[]),
    ];
    const base=f.rec.recommendations[0];f.rec.evidence=[];f.rec.native!.scores={};
    const names=['17 Dover Crescent','456 Clementi Avenue 3','508 West Coast Drive','511 West Coast Drive','NEWest','Lyf One-North Singapore','80 Nepal Park','345 Clementi Avenue 5'];
    const prices=[4000,4500,3350,3980,3200,3764,2800,2500];
    const rows=Array.from({length:mixed?8:2},(_,i)=>{
      const row=structuredClone(base),id=String(83000001+i);
      row.listing={...row.listing,listing_id:id,title:names[i],price:prices[i],bedrooms:i>=4?null:i%2?3:2,district:'D05'};row.rank=i+1;
      row.criterion_results=f.req.criteria.map(c=>{
        const value=c.field==='price'?prices[i]:c.field==='bedrooms'?row.listing.bedrooms:c.field==='district'?'D05':c.field==='postal_code'?'139409':null;
        const evidence_id='test:'+id+':'+c.criterion_id;
        f.rec.evidence.push({evidence_id,listing_id:id,field_path:c.field,value,unit:c.unit,source_kind:'fixture',source_ref:'categorical-explanation',dataset_version:'synthetic',retrieved_at:new Date().toISOString(),verified_at:null});
        return {criterion_id:c.criterion_id,status:value===null?'unknown' as const:'pass' as const,observed_value:value,unit:c.unit,evidence_ids:[evidence_id]};
      });
      row.explanation_evidence={strengths:row.criterion_results.filter(r=>r.status==='pass').map(r=>({criterion_ids:[r.criterion_id],evidence_ids:r.evidence_ids})),compromises:[],unknowns:row.criterion_results.filter(r=>r.status==='unknown').map(r=>({field:f.req.criteria.find(c=>c.criterion_id===r.criterion_id)!.field,evidence_ids:r.evidence_ids}))};
      if(!row.criterion_results.some(r=>r.status==='unknown'))f.rec.native!.scores[id]=structuredClone(Object.values(explanationFixture('stable').rec.native!.scores)[0]);
      return row;
    });
    f.rec.recommendations=rows.filter(r=>!r.criterion_results.some(c=>c.status==='unknown'));f.rec.unverified_candidates=rows.filter(r=>r.criterion_results.some(c=>c.status==='unknown'));
    f.rec.status=f.rec.recommendations.length?'success':'no_match';f.rec.native!.status=f.rec.status;
    f.rec.native!.ranking=f.rec.recommendations.map(r=>r.listing.listing_id);f.rec.native!.unscored_listing_ids=f.rec.unverified_candidates.map(r=>r.listing.listing_id);
    f.rec.state.counts={assessed:21431,eligible:f.rec.recommendations.length,unverified:f.rec.unverified_candidates.length,excluded:21431-rows.length};
    return f;
  }
  const price:Criterion={criterion_id:'rent',field:'price',operator:'lte',value:3000,unit:'SGD/month',strength:'hard'};
  const location:Criterion={criterion_id:'university',field:'place_proximity',operator:'lte',value:5000,unit:'m',strength:'hard',distance_type:'straight_line',distance_preference:'bounded',target:{name:'Nanyang Technological University',place_id:'OSM:way:university'}};
  if(scenario==='jurong_layout'){location.target={name:'裕廊东',place_id:'URA:AREA:JURONG_EAST'};price.value=2500;}
  const near:Criterion={...location,criterion_id:'closer',value:1000,strength:'soft',weight:1,distance_preference:'nearer'};
  const transit:Criterion={...near,criterion_id:'transit',distance_preference:'bounded',target:{category:'PublicTransport',name:'MRT或公交站'}};
  const bath:Criterion={criterion_id:'bath',field:'amenities',operator:'eq',value:'private_bathroom',strength:'hard'};
  const cheap:Criterion={criterion_id:'cheaper',field:'price',operator:'lte',value:1800,unit:'SGD/month',strength:'soft',weight:1};
  const area:Criterion={criterion_id:'large',field:'floor_area_sqft',operator:'gte',value:600,unit:'sqft',strength:'soft',weight:1};
  const criteria=[price,...(['behavior','stable','empty','area'].includes(scenario)?[]:[location])];
  if(['distance','numeric_titles','mixed_groups','multiple','hybrid','tie','hard_unknown','jurong_layout','close_distance'].includes(scenario))criteria.push(near);
  if(['distance','numeric_titles','mixed_groups','unknown_soft','jurong_layout'].includes(scenario))criteria.push(transit);
  if(['price','multiple'].includes(scenario))criteria.push(cheap);
  if(scenario==='area')criteria.push(area);
  if(['hard_unknown','unscored'].includes(scenario))criteria.push(bath);
  if(['bath_count','jurong_layout'].includes(scenario))criteria.push({criterion_id:'bedroom-count',field:'bedrooms',operator:'eq',value:1,unit:'count',strength:'hard'},{criterion_id:'bathroom-count',field:'bathrooms',operator:'eq',value:1,unit:'count',strength:'hard'});
  const req:Requirements={listing_type:'RENT',criteria},evidence:RecommendationResponse['evidence']=[],scores:Record<string,NativeScores>={};
  let rows:Candidate[]=(scenario==='mixed_groups'?[0,1,2,3,4,5,6,7]:[0,1]).map(i=>{
    const id=String(83000001+i),rent=scenario==='price'?(i?2100:1700):i?1600:1900,meters=scenario==='tie'?900:scenario==='close_distance'?(i?858.67:850):i?1600:850;
    const results=criteria.map(c=>{
      const value=c.field==='price'?rent:c.field==='bedrooms'?1:c.field==='floor_area_sqft'?(i?500:700):c.field==='bathrooms'||c.field==='amenities'||c.criterion_id==='transit'||(scenario==='mixed_groups'&&i>=4)||(scenario==='jurong_layout'&&c.field==='place_proximity')?null:meters;
      const status=value===null?'unknown' as const:c.operator==='gte'?value>=Number(c.value)?'pass' as const:'fail' as const:value<=Number(c.value)?'pass' as const:'fail' as const;
      const eid='test:'+id+':'+c.criterion_id;
      evidence.push({evidence_id:eid,listing_id:id,field_path:c.field,value,unit:c.unit,...(c.target?{target:c.target}:{}),source_kind:'fixture',source_ref:'synthetic-explanation',dataset_version:'synthetic',retrieved_at:new Date().toISOString(),verified_at:null,...(c.field==='place_proximity'?{distance_type:'straight_line',route_verified:false}:{})});
      return {criterion_id:c.criterion_id,status,observed_value:value,unit:c.unit,evidence_ids:[eid]};
    });
    const soft=criteria.filter(c=>c.strength==='soft'),hasBehavior=['behavior','hybrid'].includes(scenario),ew=soft.length?(hasBehavior?.8:1):0,iw=hasBehavior?(soft.length?.2:1):0;
    const contributions:NativeScores['contributions']=soft.map(c=>{const r=results.find(r=>r.criterion_id===c.criterion_id)!,similarity=r.status==='unknown'?null:c.field==='place_proximity'?1/(1+Number(r.observed_value)/Number(c.value)):Number(r.status==='pass');return {feature:c.criterion_id,reference:null,raw_difference:null,scale:null,similarity,weight:1/soft.length,contribution:(similarity||0)/soft.length,unknown:similarity===null};});
    const explicit=soft.length?contributions.reduce((n,c)=>n+c.contribution,0):null,implicit=hasBehavior?(i?.6:.9):null;
    if(hasBehavior)for(const feature of ['property_type','floor_area_sqft'])contributions.push({feature,reference:'83000009',raw_difference:null,scale:null,similarity:implicit,weight:.5,contribution:implicit!*.5,unknown:false});
    const score:NativeScores={explicit_score:explicit,implicit_score:implicit,combined_score:ew*(explicit||0)+iw*(implicit||0),explicit_weight:ew,implicit_weight:iw,evidence_coverage:scenario==='distance'?.5:1,contributions,suppressed_features:['geography'],ordering:ew&&iw?'weighted':ew?'explicit':iw?'behavioral':'stable_listing_id'};
    if(!['unscored','bath_count','jurong_layout'].includes(scenario)&&!(scenario==='mixed_groups'&&i>=4))scores[id]=score;
    return {listing:{listing_id:id,title:scenario==='numeric_titles'?(i?'906 Jurong West Street 91':'941 Jurong West Street 91'):i?'Garden Court':'Willow Residence',price:rent,price_unit:'SGD/month',bedrooms:['bath_count','jurong_layout'].includes(scenario)?1:i?2:0,district:'D22',source_url:null,availability_status:'unknown',floor_area_sqft:i?500:700,property_type:'Condominium'},rank:i+1,score:['unscored','bath_count','jurong_layout'].includes(scenario)?0:score.combined_score,score_breakdown:[],criterion_results:results,explanation_evidence:{strengths:results.filter(r=>r.status==='pass').map(r=>({criterion_ids:[r.criterion_id],evidence_ids:r.evidence_ids})),compromises:results.filter(r=>r.status==='fail').map(r=>({criterion_ids:[r.criterion_id],evidence_ids:r.evidence_ids})),unknowns:results.filter(r=>r.status==='unknown').map(r=>({field:criteria.find(c=>c.criterion_id===r.criterion_id)!.field,evidence_ids:r.evidence_ids}))}};
  });
  rows.sort((a,b)=>b.score-a.score||a.listing.listing_id.localeCompare(b.listing.listing_id));rows.forEach((r,i)=>r.rank=i+1);
  if(scenario==='empty'){rows=[];evidence.length=0;for(const id of Object.keys(scores))delete scores[id];}
  const uv=['hard_unknown','unscored','bath_count','jurong_layout'].includes(scenario),rec:RecommendationResponse={contract_version:'1.0',request_id:randomUUID(),conversation_id:randomUUID(),requirements_version:1,status:rows.length&&!uv?'success':'no_match',recommendations:uv?[]:rows,unverified_candidates:uv?rows:[],evidence,clarifications:[],relaxation_suggestions:[],warnings:[],state:{state_id:'fixture',candidate_pool_ref:'fixture',coverage:{dataset_version:'synthetic',dataset_scope:'synthetic',retrieval_scope:'synthetic',complete_within_dataset:true,truncated:false},counts:{assessed:21431,eligible:uv?0:rows.length,unverified:uv?rows.length:0,excluded:21431-rows.length}},execution:{strategy:'fixture',trace_ref:null,tool_calls:[],timing:{total_ms:0},scoring_version:'weighted-v1',score_semantics:'not_probability'},error:null,native:{provider:'jiangshan-native',upstream_commit:'907cfa8',exchange_ref:randomUUID(),status:rows.length&&!uv?'success':'no_match',state_id:null,ranking:uv?[]:rows.map(r=>r.listing.listing_id),scores,criterion_results:{},unsupported_criterion_ids:[],unscored_listing_ids:['unscored','bath_count','jurong_layout'].includes(scenario)?rows.map(r=>r.listing.listing_id):[]}};
  if(scenario==='mixed_groups'){
    rec.recommendations=rows.filter(r=>scores[r.listing.listing_id]);rec.unverified_candidates=rows.filter(r=>!scores[r.listing.listing_id]);
    rec.unverified_candidates.forEach((r,i)=>{r.rank=i+1;r.score=0;r.listing.title='Pending Court '+(i+1);});
    rec.recommendations.forEach((r,i)=>{r.rank=i+1;r.listing.title=i===0?'941 Jurong West Street 91':'Garden Court '+i;});
    rec.native!.ranking=rec.recommendations.map(r=>r.listing.listing_id);rec.native!.unscored_listing_ids=rec.unverified_candidates.map(r=>r.listing.listing_id);
    rec.state.counts={assessed:21431,eligible:4,unverified:4,excluded:21423};
  }
  return {req,rec};
}
const gold:Record<Scenario,string>={
  district:'要求月租不超过5000新币、至少2间卧室、D05邮区。两套记录4000和4500、2和3间卧室，邮区D05，三项全部符合。没有软偏好或浏览依据，固定展示顺序，不代表按价格排，也不能杜撰缺少已确认的卧室资料。当前价格与是否可租需确认。分项呈现三项要求及符合项，不把D05当数值5。',
  district_mixed:'要求月租5000以内、至少2卧室、D05。全部记录租金2500至4500且都在D05。Dover/Clementi/West Coast的已知卧室2或3符合；NEWest、Lyf、Nepal Park、345 Clementi缺卧室资料不能说没有或未达到，不能把整组笼统说全部符合或整组都未知。无软偏好及浏览依据，沿用固定顺序而非便宜排前。分项要求、具体符合项、清楚说明缺卧室资料，再一条短排序及注意。',
  postal_date:'用户要求5000以内、至少2卧室、D05、邮编139409及最晚2026-12-01入住。租金4000至4500、卧室2或3、D05和139409的记录符合；入住日期未知，不能确认12月1日前可入住，不是已经确定不符合。没有排序偏好，顺序供浏览。精确邮区邮编和日期不是可舍入数量；不能从编码数字推断房价或卧室数。当前租金可租仍需确认。',
  close_distance:'预算3000和NTU直线5公里用于筛选，两套已符合。唯一排序依据更靠近学校：850米比858.67米稍近，差约8.67米，故第一套在前。不能由小直线差推断实际步行或通勤体验相同、可能差不多或更快。当前可租价格仍需确认。',
  jurong_layout:'用户要求预算2500、一间卧室一个卫生间、裕廊东5公里内，最好MRT或公交1公里内。记录月租1600至1900及1间卧室符合。卫生间数量、裕廊东距离和交通距离均未知；不能误称独卫，不能说没有卫生间或未满足。逐项列要求、符合部分、缺资料，再以一句顺序只供浏览和注意说明还不能确认全部符合；不反复强调价格不决定排序。',
  bath_count:'要求1B1B只表示一间卧室和一个卫生间；预算及卧室符合，卫生间数量未知。不能误称独卫未知，不能暗示用户要求专用卫浴。当前只是待确认选项，无可比较的排名依据，不能说全满足或第一套更好。当前可租仍需确认。',
  distance:'硬条件预算3000、NTU直线5公里。唯一有资料的排序偏好是靠近NTU；MRT或公交1公里仍未知不能说满足。不要把预算当价格排序。',
  price:'预算3000。实际排序偏好是月租1800以内，第一套1700符合，第二套2100不符这项软偏好；不能说第二套超过硬预算或断言按距离排序。',
  multiple:'预算与学校直线5公里先筛选，排序同时考虑更靠近学校及租金1800以内，不能说唯一按距离或价格排序。',
  behavior:'仅预算硬条件，无明确软偏好，实际排序来自已有浏览记录相似的房型和面积；不能说用户明确要求这些或说按地点距离。',
  hybrid:'预算学校范围先筛选，排序以更靠近学校为主，已有浏览记录的房型与面积作为辅助；不能说只按距离。',
  stable:'没有可用软偏好或浏览兴趣，顺序固定，不代表第一套更合适，不能虚构按价格或面积排序。',
  tie:'两套到学校都是直线900米，唯一排序偏好就是更近，实际同分；现顺序不能证明第一套更优，月租不决定顺序。',
  hard_unknown:'独卫是必须条件但未知，整个列表只能待确认不能说硬条件全满足；记录的预算及学校范围符合，已有距离排序依据有效，仍需问独卫和是否可租。',
  unscored:'独卫必须但未知，这组没有评分，只沿用返回顺序，不能说按近远或价格排好、不能说全部条件满足。',
  empty:'本次无可展示结果，只限这次范围，不等于全市场没房，不擅自放宽或编造第一第二套。',
  unknown_soft:'有预算与学校范围，但唯一软偏好MRT/公交1公里未知，没有能区分优劣的可用偏好依据，不能说优选了交通方便或更近。',
  area:'预算先筛选，排序偏好是至少600平方英尺，第一套700符合、第二套500未达偏好，不能说第二套不满足硬条件；没有距离排序。',
  numeric_titles:'预算和学校范围先筛选，唯一实际排序因素是更靠近学校。第一套941 Jurong West Street 91，第二套906 Jurong West Street 91；941、906可作为楼号简称，不是虚构数值。MRT/公交1公里尚未知，不能说满足，不能把预算当价格排序。',
  mixed_groups:'同一列表含符合预算学校范围的已确认项，以及学校距离未知而待确认的选项。前者唯一实际排序因素是更靠近学校，后者没有评分、原顺序展示；MRT/公交1公里全都未知。解释要概括，不能说所有房源都满足硬条件，也不能说待确认组已按距离排好。',
};
const generic=['为什么推荐这几套房子？','说人话讲讲它们怎么选、怎么排的，不用报分。','为什么第一套放在前面？','前两套帮我比一下，重点讲跟我要求有什么关系。','这几套是越便宜排得越前吗？','Why were these homes selected and ordered this way?'];
const emptyQuestions=['怎么这次一间都没推荐？','没有结果的话能解释下吗？','是不是新加坡整个市场都没有合适的了？','我这条件为什么没找到？','没房就说明我的预算绝对不可能租到吗？','Why are there no results this time?'];
export const explanationCases=SCENARIOS.flatMap(s=>[...(s==='empty'?emptyQuestions:generic).map((query,i)=>({id:s+'-dev-'+(i+1),scenario:s,split:'development',query,gold:gold[s]})),...(s==='empty'?['那我该从哪一个条件重新考虑？','Does no result mean no suitable home exists anywhere?']:['别逐条念资料，简单解释一下当前这一组为什么是这个顺序。','Can you explain the current order without assuming the top home is definitely best?']).map((query,i)=>({id:s+'-hold-'+(i+1),scenario:s,split:'holdout',query,gold:gold[s]}))]);
