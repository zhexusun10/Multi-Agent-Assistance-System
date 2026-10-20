import type { Candidate,Criterion,RecommendationResponse,Requirements } from '../contracts/recommendation-v1.js';
import {amenityLabels,valueLabels} from './field-capabilities.js';
export const labels:Record<string,string>={price:'月租',bedrooms:'卧室',bathrooms:'卫浴',floor_area_sqft:'室内面积',land_area_sqft:'土地面积',district:'邮区',property_type:'房型',place_proximity:'地点距离',mrt_distance_m:'平台标注 MRT 距离',mrt_walking_mins:'平台标注步行分钟',furnishing:'家具',rental_scope:'整租/单间',lease_months:'租期',commute_time:'通勤时间',current_availability:'当前可租',move_in_date:'入住日',amenities:'室内设施'};
export function criterionText(c:Criterion){if(c.field==='amenities')return `${amenityLabels[String(c.value)]||c.value}${c.strength==='hard'?'，必须满足':'，软偏好'}`;if(c.field==='place_proximity'&&c.strength==='soft')return `更靠近 ${c.target?.name||c.target?.category||c.target?.place_id}（越近越好，软偏好；不设硬半径）`;const op={eq:'等于',in:'属于',gte:'至少',lte:'不超过',between:'介于'}[c.operator];const value=Array.isArray(c.value)?c.value.join(' — '):valueLabels[String(c.value)]||c.value;const target=c.target?[c.target.name||c.target.category||c.target.place_id,c.distance_type==='straight_line'?'直线距离':c.distance_type,c.transport_mode==='unspecified'?'交通方式未指定':c.transport_mode==='transit'?'公共交通':c.transport_mode].filter(Boolean).join('；'):null;return `${labels[c.field]||c.field}${op} ${value}${c.unit?' '+c.unit:''}${target?'（'+target+'）':''}${c.strength==='soft'?`，偏好权重 ${c.weight}`:'，必须满足'}`;}
export function explainCandidate(item:Candidate,requirements:Requirements){
  return {listing_id:item.listing.listing_id,rank:item.rank,title:item.listing.title,
    facts:[item.listing.price===null?'月租未知':`快照月租 SGD ${item.listing.price}/月`,item.listing.bedrooms===null?'卧室数量未知':`${item.listing.bedrooms} 间卧室`,item.listing.district||'邮区未知'],
    claims:item.criterion_results.map(r=>{const c=requirements.criteria.find(c=>c.criterion_id===r.criterion_id)!;const observed=r.observed_value===null?'未知':typeof r.observed_value==='number'?(Number.isInteger(r.observed_value)?r.observed_value:r.observed_value.toFixed(2)):String(r.observed_value);return {criterion_id:r.criterion_id,evidence_ids:r.evidence_ids,status:r.status,text:`${criterionText(c)}：${r.status==='unknown'?'待核实':c.field==='place_proximity'&&c.strength==='soft'?'已计入就近排序':r.status==='pass'?'通过':'未满足'}；记录值 ${observed}${r.unit?' '+r.unit:''}${r.reason?'；'+r.reason:''}${c.field==='place_proximity'?'，按已收录地点/区域的直线距离，未验证实际步行路线':''}`,budget_margin:c.field==='price'&&c.operator==='lte'&&typeof r.observed_value==='number'?Number(c.value)-r.observed_value:null};}),
    score:item.score,score_meaning:'明确软偏好匹配分，不是成交概率',unknowns:['当前可租状态未核实',...item.criterion_results.filter(r=>r.status==='unknown').map(r=>labels[requirements.criteria.find(c=>c.criterion_id===r.criterion_id)!.field]||r.criterion_id)],
  };
}
export function buildExplanation(req:Requirements,rec:RecommendationResponse,dataset:string,question=''){
  const verified=rec.recommendations.map(c=>explainCandidate(c,req)),unverified=rec.unverified_candidates.map(c=>explainCandidate(c,req));
  const counts=rec.state.counts;
  let summary=rec.status==='needs_clarification'?rec.clarifications.map(x=>x.question).join('；'):verified.length?`在 ${dataset} 已评估的 ${counts.assessed??'未知数量'} 套候选中，${counts.eligible??'未知数量'} 套通过全部硬条件，下面按推荐方原排名展示 ${verified.length} 套。`:unverified.length?`已找到待核实候选：本次评估中有 ${counts.unverified??unverified.length} 套因部分硬条件缺少证据而需要确认，下面展示 ${unverified.length} 套。尚未标为全部条件满足，也未自动放宽你的要求。`:`本次已评估范围内没有可展示的匹配房源。未自动放宽需求，也不表示全市场没有合适房源。`;
  const interpretations=rec.warnings.filter(w=>w.code==='GEO_INTERPRETATION').map(w=>w.message);if(interpretations.length)summary=interpretations.join('\n')+'\n\n'+summary;
  if(unverified.length)summary+='\n可以从待核实候选点击“我想看这套”或“核实并咨询”，生成逐项核实和看房询问草稿；这不会将未知条件改成已满足。';
  if((verified.length||unverified.length)&&/为什么|解释|理由|比较|对比|分数|得分|compare|why|explain/i.test(question)){
    const map:Record<string,number>={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10};
    const ordinals=[...question.matchAll(/第\s*([一二三四五六七八九十\d]+)/g)].map(m=>map[m[1]]??Number(m[1]));
    const comparing=/比较|对比|compare/i.test(question);
    const display=[...verified,...unverified].map((c,i)=>({...c,rank:i+1}));
    const candidates=[...rec.recommendations,...rec.unverified_candidates];
    const selected=(ordinals.length?display.filter(c=>ordinals.includes(c.rank)):display.slice(0,comparing?2:1));
    if(!selected.length)summary+='\n当前列表没有指定序号的房源，请按右侧当前列表选择。';
    else{
      const lines=selected.flatMap(c=>{
        const raw=candidates.find(x=>x.listing.listing_id===c.listing_id)!;
        return [`第 ${c.rank} 套 ${c.title}（${c.listing_id}）${rec.unverified_candidates.includes(raw)?'是待核实候选，不代表全部硬条件已通过':''}：${c.facts.join('；')}。`,...c.claims.map(x=>`${x.text} [证据 ${x.evidence_ids.join(', ')}]`),...(raw.score_breakdown.length?[`软偏好匹配分 ${c.score.toFixed(4)}；`+raw.score_breakdown.map(x=>`${x.criterion_id} 权重 ${x.weight}、贡献 ${x.contribution.toFixed(4)}`).join('；')]:['本轮没有软偏好评分项，保持推荐方返回的顺序。']),`待核实：${c.unknowns.join('、')}。`];
      });
      if(comparing&&selected.length>=2){const a=candidates.find(c=>c.listing.listing_id===selected[0].listing_id)!,b=candidates.find(c=>c.listing.listing_id===selected[1].listing_id)!;if(a.listing.price!==null&&b.listing.price!==null){const delta=b.listing.price-a.listing.price;lines.push(`月租差异：第 ${selected[1].rank} 套比第 ${selected[0].rank} 套${delta>=0?'高':'低'} SGD ${Math.abs(delta)}/月。`);}}
      summary+='\n\n'+lines.join('\n');
    }
  }
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
