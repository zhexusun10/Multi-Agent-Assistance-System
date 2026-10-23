const SELECTION_LABELS={ranked:'按分数顺序选入',leader:'明显领先，直接保留',tie_group_sample:'分数难以区分，组内随机选入',price_exploration_sample:'冷启动价格分层探索选入',small_pool:'合格房源不超过展示数量，全部返回'};
// Native response 2.0 scores (soft requirement fit / behaviour similarity / selection reason); archived
// contract 1.0 scores (explicit/implicit) and missing unverified lists stay readable.
export function rankingDescription(rec,id){
  const native=rec.native;if(!native)return null;
  const score=native.scores?.[id],selection=native.selection_reasons?.[id];
  const reason=selection?`选择原因：${SELECTION_LABELS[selection.selection_reason]||selection.selection_reason}（全池分数排名第 ${selection.score_rank}）。`:'';
  if(!score)return '原生模块未给此待核实候选打分，按原生返回顺序展示。';
  if(score.ordering==='stable_listing_id')return '本轮没有可用的软需求或合格行为信号，按原生房源ID顺序返回；0分不代表不合适。'+reason;
  const parts=[`原生融合匹配分 ${score.combined_score.toFixed(4)}`];
  if('soft_score' in score){
    if(score.soft_score!==null)parts.push(`软需求匹配 ${score.soft_score.toFixed(4)} × ${score.soft_weight}`);
    if(score.behavior_score!==null)parts.push(`行为相似度 ${score.behavior_score.toFixed(4)} × ${score.behavior_weight}`);
    parts.push(`软需求证据覆盖 ${(score.soft_evidence_coverage*100).toFixed(1)}%`);
  }else{
    if(score.explicit_score!==null)parts.push(`显式偏好 ${score.explicit_score.toFixed(4)} × ${score.explicit_weight}`);
    if(score.implicit_score!==null)parts.push(`浏览偏好估计 ${score.implicit_score.toFixed(4)} × ${score.implicit_weight}`);
    parts.push(`原生评分字段证据覆盖 ${(score.evidence_coverage*100).toFixed(1)}%`);
  }
  if(score.suppressed_features?.length)parts.push('明确硬条件优先，已停用相应行为相似度特征：'+score.suppressed_features.join('、'));
  return parts.join('；')+'。这是匹配分，不是成交概率。'+reason;
}

export function sampleDurationEvent(listingId,requestId,seconds){
  if(!Number.isFinite(seconds)||seconds<=0||seconds>3600)throw Error('请输入大于0且不超过3600的秒数。');
  return {event_id:crypto.randomUUID(),listing_id:listingId,occurred_at:new Date().toISOString(),event_type:'active_view',payload:{source:'sample',recommendation_request_id:requestId,view_session_id:crypto.randomUUID(),active_duration_ms:seconds*1000}};
}
