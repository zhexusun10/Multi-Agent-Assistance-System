/** Public errors never expose SDK payloads, Zod arrays or provider diagnostics. */
export function recommendationFailureMessage(code:string){
  if(code==='MODEL_UNAVAILABLE')return '找房服务暂时无法连接，请稍后重试。你的找房条件已保留。';
  if(code==='DATA_UNAVAILABLE')return '房源资料暂时读取失败，请稍后重试。你的找房条件已保留。';
  return '这次找房暂时没能完成，请稍后重试。你的找房条件已保留。';
}
export function describeAgentFailure(agent:string,error:string){
  if(agent==='a')return {code:'REQUIREMENT_STAGE_FAILED',message:'需求理解暂未完成，请稍后重试。本轮尚未进入房源推荐。'};
  if(agent==='c')return {code:'EXPLANATION_STAGE_FAILED',message:'推荐理由暂时没能整理好，请再试一次。你可以继续查看当前房源。'};
  if(error.startsWith('DATA_'))return {code:'DATA_SERVICE_UNAVAILABLE',message:'数据服务暂不可用，请检查数据服务后重试。'};
  return {code:'AGENT_FAILED',message:'当前处理阶段暂未完成，请稍后重试。原始诊断已保留在任务记录中。'};
}
