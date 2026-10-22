import type {BaseChatModel} from '@langchain/core/language_models/chat_models';

/** Test-only guard. Production request handling does not import this module. */
export const MODEL_TEST_LIMITS={cases:1,requests:3,maxInputCharacters:24000,maxOutputTokens:1600,concurrency:1} as const;

export function modelTestPlan<T>(cases:T[],args:string[]){
  const i=args.indexOf('--limit'),limit=i<0?MODEL_TEST_LIMITS.cases:Number(args[i+1]);
  if(!Number.isInteger(limit)||limit<1||limit>MODEL_TEST_LIMITS.cases)throw Error('MODEL_TEST_CASE_LIMIT: cost policy permits 1 targeted case; do not run bulk paid regression without a new user request.');
  return {live:args.includes('--live'),modelJudge:args.includes('--model-judge'),cases:cases.slice(0,limit),limits:MODEL_TEST_LIMITS};
}

export function budgetTestModel(model:BaseChatModel,maxRequests:number=MODEL_TEST_LIMITS.requests){
  let requests=0;
  const wrap=(target:any):any=>new Proxy(target,{get(object,key){
    const value=Reflect.get(object,key,object);
    if(key==='invoke')return async(...args:any[])=>{
      // A conservative serialized-character ceiling, not a claimed exact token
      // count or currency budget. Reject oversized contexts before any request.
      if((JSON.stringify(args[0])?.length||0)>MODEL_TEST_LIMITS.maxInputCharacters)throw Error('MODEL_TEST_CONTEXT_LIMIT: context exceeds the local test ceiling; shorten the targeted fixture before making a paid request.');
      if(requests>=maxRequests)throw Error('MODEL_TEST_REQUEST_LIMIT: stopped at '+maxRequests+' model requests; no further paid calls.');
      requests++;return Reflect.apply(value,object,args);
    };
    if(key==='withStructuredOutput')return (...args:any[])=>wrap(Reflect.apply(value,object,args));
    return typeof value==='function'?value.bind(object):value;
  }});
  return {model:wrap(model) as BaseChatModel,requests:()=>requests};
}
