import { createHash, randomUUID } from 'node:crypto';
import { AGENT_PROFILES } from '../agents/profiles.js';
export type ToolContext={sessionId:string;operationId:string;agent:'a'|'b'|'c'|'d';dataset:string;deadline:number;calls:number};
export type Trace={id:string;session_id:string;operation_id:string;agent:string;tool:string;status:string;duration_ms:number;cache_hit:boolean;error?:string};
export class ToolExecutor {
  private cache=new Map<string,{expires:number;value:unknown}>();
  private failures=new Map<string,{count:number;until:number}>();
  constructor(private record:(trace:Trace)=>Promise<void>=async()=>{}){}
  async run<T>(ctx:ToolContext,name:string,args:unknown,call:()=>Promise<T>,cacheMs=0):Promise<T>{
    if(!AGENT_PROFILES[ctx.agent].tools.includes(name))throw Error(`TOOL_FORBIDDEN:${name}`);
    if(Date.now()>ctx.deadline||++ctx.calls>1500)throw Error('TOOL_BUDGET_EXCEEDED');
    const key=createHash('sha256').update(JSON.stringify([ctx.sessionId,ctx.agent,ctx.dataset,name,args])).digest('hex');
    const start=Date.now(),cached=this.cache.get(key),trace:Trace={id:randomUUID(),session_id:ctx.sessionId,operation_id:ctx.operationId,agent:ctx.agent,tool:name,status:'ok',duration_ms:0,cache_hit:false};
    try{
      if(cached&&cached.expires>Date.now()){trace.cache_hit=true;return structuredClone(cached.value as T);}
      const circuit=this.failures.get(name);if(circuit&&circuit.count>=3&&circuit.until>Date.now())throw Error('TOOL_CIRCUIT_OPEN');
      const result=await call();this.failures.delete(name);
      if(cacheMs){if(this.cache.size>3000)this.cache.clear();this.cache.set(key,{expires:Date.now()+cacheMs,value:structuredClone(result)});}
      return result;
    }catch(error){trace.status='error';trace.error=error instanceof Error?error.name:'ToolError';const old=this.failures.get(name);this.failures.set(name,{count:(old?.count||0)+1,until:Date.now()+30000});throw error;}
    finally{trace.duration_ms=Date.now()-start;await this.record(trace);}
  }
}
