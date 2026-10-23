import type { Candidate,Criterion,RecommendationResponse,Requirements } from '../contracts/recommendation-v1.js';
import {amenityLabels,valueLabels} from './field-capabilities.js';
import type {NativeScores,LegacyNativeScores} from '../contracts/recommendation-native.js';
type SelectionReason={selection_reason:string;selection_note:string;score_rank:number};
const selectionLabels:Record<string,string>={ranked:'按分数顺序选入',leader:'明显领先，直接保留',tie_group_sample:'与其他候选分数难以区分，组内随机选入',price_exploration_sample:'冷启动阶段按价格分层探索选入',small_pool:'合格房源不超过展示数量，全部返回'};
export function selectionText(reason:SelectionReason|undefined){return reason?`选择原因：${selectionLabels[reason.selection_reason]||reason.selection_reason}（完整探索池分数排名第 ${reason.score_rank}）。`:'';}
// Accepts contract 2.0 scores (soft/behaviour) and archived contract 1.0 scores (explicit/implicit).
export function nativeScoreText(score:NativeScores|LegacyNativeScores|undefined,unscored=false,selection?:SelectionReason){
  if(!score)return unscored?'原生模块没有为此待核实候选打分；按其返回顺序展示。':'没有原生评分信息。';
  if(score.ordering==='stable_listing_id')return '本轮没有可用的软需求或行为信号，原生模块按房源ID字符串顺序返回；0分不代表不合适。'+selectionText(selection);
  const names:Record<string,string>={price:'月租',property_type:'房型',bedrooms:'卧室数',floor_area_sqft:'面积',geography:'地理位置'};
  const parts=[`原生融合匹配分 ${score.combined_score.toFixed(4)}（不是成交概率）`];
  if('soft_score' in score){
    if(score.soft_score!==null)parts.push(`软需求匹配 ${score.soft_score.toFixed(4)} × ${score.soft_weight}`);
    if(score.behavior_score!==null)parts.push(`行为相似度 ${score.behavior_score.toFixed(4)} × ${score.behavior_weight}`+[score.appointment_score===null?'':`看房意向相似 ${score.appointment_score.toFixed(4)}`,score.browsing_score===null?'':`累计浏览相似 ${score.browsing_score.toFixed(4)}`].filter(Boolean).map(x=>`（${x}）`).join(''));
    parts.push(`软需求证据覆盖 ${score.soft_evidence_coverage.toFixed(4)}`);
  }else{
    if(score.explicit_score!==null)parts.push(`显式偏好 ${score.explicit_score.toFixed(4)} × ${score.explicit_weight}`);
    if(score.implicit_score!==null)parts.push(`样例浏览兴趣 ${score.implicit_score.toFixed(4)} × ${score.implicit_weight}`);
    parts.push(`原生评分字段证据覆盖 ${score.evidence_coverage.toFixed(4)}`);
  }
  if(score.suppressed_features.length)parts.push(`已明确提出的特征不再用于行为相似度推断：${score.suppressed_features.map(x=>names[x]||x).join('、')}`);
  return parts.join('；')+'。'+selectionText(selection);
}
export const labels:Record<string,string>={price:'月租',bedrooms:'卧室',bathrooms:'卫浴',floor_area_sqft:'室内面积',land_area_sqft:'土地面积',district:'邮区',property_type:'房型',place_proximity:'地点距离',mrt_distance_m:'平台标注 MRT 距离',mrt_walking_mins:'平台标注步行分钟',furnishing:'家具',rental_scope:'整租/单间',lease_months:'租期',commute_time:'通勤时间',current_availability:'当前可租',move_in_date:'入住日',amenities:'室内设施'};
export function criterionText(c:Criterion){if(c.field==='amenities')return `${amenityLabels[String(c.value)]||c.value}${c.strength==='hard'?'，必须满足':'，软偏好'}`;if(c.field==='place_proximity'&&c.strength==='soft'&&c.distance_preference!=='bounded')return `更靠近 ${c.target?.name||c.target?.category||c.target?.place_id}（越近越好，软偏好；不设硬半径）`;const op={eq:'等于',in:'属于',gte:'至少',lte:'不超过',between:'介于'}[c.operator];const value=Array.isArray(c.value)?c.value.join(' — '):valueLabels[String(c.value)]||c.value;const target=c.target?[c.target.name||c.target.category||c.target.place_id,c.distance_type==='straight_line'?'直线距离':c.distance_type,c.transport_mode==='unspecified'?'交通方式未指定':c.transport_mode==='transit'?'公共交通':c.transport_mode].filter(Boolean).join('；'):null;return `${labels[c.field]||c.field}${op} ${value}${c.unit?' '+c.unit:''}${target?'（'+target+'）':''}${c.strength==='soft'?`，偏好权重 ${c.weight}`:'，必须满足'}`;}
export function explainCandidate(item:Candidate,requirements:Requirements){
  return {listing_id:item.listing.listing_id,rank:item.rank,title:item.listing.title,
    facts:[item.listing.price===null?'月租未知':`记录月租 SGD ${item.listing.price}/月`,item.listing.bedrooms===null||item.listing.bedrooms<=0?'卧室数量未知':`${item.listing.bedrooms} 间卧室`,item.listing.district||'邮区未知'],
    claims:item.criterion_results.map(r=>{const c=requirements.criteria.find(c=>c.criterion_id===r.criterion_id)!;const observed=r.observed_value===null?'未知':typeof r.observed_value==='number'?(Number.isInteger(r.observed_value)?r.observed_value:r.observed_value.toFixed(2)):String(r.observed_value);return {criterion_id:r.criterion_id,evidence_ids:r.evidence_ids,status:r.status,text:`${criterionText(c)}：${r.status==='unknown'?'待核实':c.field==='place_proximity'&&c.strength==='soft'?'已计入就近排序':r.status==='pass'?'通过':'未满足'}；记录值 ${observed}${r.unit?' '+r.unit:''}${r.reason?'；'+r.reason:''}${c.field==='place_proximity'?'，按已收录地点/区域的直线距离，未验证实际步行路线':''}`,budget_margin:c.field==='price'&&c.operator==='lte'&&typeof r.observed_value==='number'?Number(c.value)-r.observed_value:null};}),
    score:item.score,score_meaning:'明确软偏好匹配分，不是成交概率',unknowns:['当前可租状态未核实',...item.criterion_results.filter(r=>r.status==='unknown').map(r=>labels[requirements.criteria.find(c=>c.criterion_id===r.criterion_id)!.field]||r.criterion_id)],
  };
}
export function buildExplanation(req:Requirements,rec:RecommendationResponse,dataset:string,question=''){
  const describe=(c:Candidate)=>({...explainCandidate(c,req),...(rec.native?{native_scores:rec.native.scores[c.listing.listing_id]??null,score_meaning:nativeScoreText(rec.native.scores[c.listing.listing_id],rec.native.unscored_listing_ids.includes(c.listing.listing_id),rec.native.selection_reasons?.[c.listing.listing_id])}:{})});
  const verified=rec.recommendations.map(describe),unverified=rec.unverified_candidates.map(describe);
  const counts=rec.state.counts;
  // Structured evidence/document record only. The Explanation Agent writes chat prose.
  const summary=rec.status==='needs_clarification'?rec.clarifications.map(x=>x.question).join('；'):verified.length?'这些房源的已有资料符合本次必须要求；现在是否仍可租需要确认。':unverified.length?'这些房源仍有必须要求尚不能确认，可以进一步向经纪人了解。':'本次条件下没有可展示的匹配房源，可以讨论要调整哪项要求。';
  return {summary,question,dataset,source_request_id:rec.request_id,requirements_version:rec.requirements_version,verified,unverified,
    warnings:rec.warnings.map(w=>w.message),coverage:rec.state.coverage,counts,relaxation_suggestions:rec.relaxation_suggestions,
    snapshot_notice:`数据来源：${dataset}。历史快照不证明今天仍可租。直线距离、平台标注与真实路线分别处理。`,
    comparison:verified.length>=2?verified.slice(0,3).map(x=>({listing_id:x.listing_id,rank:x.rank,facts:x.facts,score:x.score,unknowns:x.unknowns})):[],
  };
}
export const escapeHtml=(s:unknown)=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
export function renderSummary(requirements:Requirements,rec:RecommendationResponse,dataset:string){
  const model=buildExplanation(requirements,rec,dataset);const lines=['# 新加坡租房需求与推荐总结','',`数据范围：${dataset}`,`需求版本：${rec.requirements_version}；推荐请求：${rec.request_id}`,'',model.snapshot_notice,'','## 当前需求',...requirements.criteria.map(c=>`- ${criterionText(c)}`),'','## 推荐结果',model.summary];
  for(const group of [{title:'通过全部硬条件',items:rec.recommendations},{title:'待核实候选（不能视为匹配）',items:rec.unverified_candidates}]){lines.push('',`## ${group.title}`);for(const c of group.items){const e=explainCandidate(c,requirements);lines.push('',`### ${c.rank}. ${c.listing.title}（${c.listing.listing_id}）`,...e.facts.map(x=>`- ${x}`),...e.claims.map(x=>`- ${x.text} [证据：${x.evidence_ids.join(', ')}]`),'- 当前可租状态：待经纪人核实',`- 来源：${c.listing.source_url||'未提供'}`);}}
  lines.push('','## 覆盖与限制',...model.warnings.map(x=>`- ${x}`),'','## 下一步','- 确认当前可租、租期、家具与实际路线。','- 选定房源后核实联系人，审核消息或看房草稿；本地执行为模拟。','','## 证据索引',...rec.evidence.map(e=>`- ${e.evidence_id}：${e.field_path}；来源引用 ${e.source_ref}；版本 ${e.dataset_version}`));
  const markdown=lines.join('\n');const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>租房推荐总结</title><style>body{font:16px/1.8 system-ui;max-width:900px;margin:48px auto;padding:24px;color:#182a32}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}h1{color:#096b64}</style><h1>租房需求与推荐总结</h1><pre>'+escapeHtml(markdown)+'</pre></html>';
  return {markdown,html,model,template_version:'summary-v1.1'};
}
