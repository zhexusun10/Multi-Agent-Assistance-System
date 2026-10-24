/** Public errors never expose SDK payloads, Zod arrays or provider diagnostics. */
export function recommendationFailureMessage(code:string){
  if(code==='MODEL_UNAVAILABLE')return "The search service is temporarily unreachable. Try again later; your requirements are saved.";
  if(code==='DATA_UNAVAILABLE')return "Listing details could not be read. Try again later; your requirements are saved.";
  return "The search could not be completed. Try again later; your requirements are saved.";
}
export function describeAgentFailure(agent:string,error:string){
  if(agent==='a')return {code:'REQUIREMENT_STAGE_FAILED',message:"Requirement understanding could not be completed. Please try again; no recommendation search has started this turn."};
  if(agent==='c')return {code:'EXPLANATION_STAGE_FAILED',message:"The property introduction could not be prepared. Try again; you can still browse the current homes."};
  if(error.startsWith('DATA_'))return {code:'DATA_SERVICE_UNAVAILABLE',message:"The data service is unavailable. Check it and try again."};
  return {code:'AGENT_FAILED',message:"This stage could not be completed. Try again later. Diagnostics are retained in the task record."};
}
