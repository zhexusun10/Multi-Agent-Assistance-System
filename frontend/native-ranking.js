export function rankingDescription(rec,id){
  const native=rec.native;if(!native)return null;
  const score=native.scores[id];
  if(!score)return '原生模块未给此待核实候选打分，按原生返回顺序展示。';
  if(score.ordering==='stable_listing_id')return '本轮没有可用的软偏好或合格浏览反馈，按原生房源ID顺序返回；0分不代表不合适。';
  const parts=[`原生融合匹配分 ${score.combined_score.toFixed(4)}`];
  if(score.explicit_score!==null)parts.push(`显式偏好 ${score.explicit_score.toFixed(4)} × ${score.explicit_weight}`);
  if(score.implicit_score!==null)parts.push(`浏览偏好估计 ${score.implicit_score.toFixed(4)} × ${score.implicit_weight}`);
  parts.push(`原生评分字段证据覆盖 ${(score.evidence_coverage*100).toFixed(1)}%`);
  if(score.suppressed_features.length)parts.push('明确要求优先，已停用相应隐式特征：'+score.suppressed_features.join('、'));
  return parts.join('；')+'。这是匹配分，不是成交概率。';
}

export function sampleDurationEvent(listingId,requestId,seconds){
  if(!Number.isFinite(seconds)||seconds<=0||seconds>3600)throw Error('请输入大于0且不超过3600的秒数。');
  return {event_id:crypto.randomUUID(),listing_id:listingId,occurred_at:new Date().toISOString(),event_type:'active_view',payload:{source:'sample',recommendation_request_id:requestId,view_session_id:crypto.randomUUID(),active_duration_ms:seconds*1000}};
}
