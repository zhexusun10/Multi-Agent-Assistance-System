/** Public errors never expose SDK payloads, Zod arrays or provider diagnostics. */
export function describeAgentFailure(agent:string,error:string){
  if(agent==='a')return {code:'REQUIREMENT_STAGE_FAILED',message:'需求识别暂未完成，请将预算、位置和其他要求分别说明后重试。本轮尚未进入房源推荐。'};
  if(error.startsWith('DATA_'))return {code:'DATA_SERVICE_UNAVAILABLE',message:'数据服务暂不可用，请检查数据服务后重试。'};
  return {code:'AGENT_FAILED',message:'当前处理阶段暂未完成，请稍后重试。原始诊断已保留在任务记录中。'};
}
