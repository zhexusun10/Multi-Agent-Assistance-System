import { z } from 'zod';
import { ToolExecutor, type ToolContext } from '../tools/executor.js';
export const entitySchema=z.object({entity_id:z.string(),label:z.string(),properties:z.record(z.string(),z.unknown())}).strict();
const source=z.object({store:z.string(),role:z.string(),distance_type:z.string().nullable().optional(),route_verified:z.boolean().optional()}).strict();
const pageSchema=z.object({items:z.array(entitySchema),next_cursor:z.string().nullable(),has_more:z.boolean(),page_size:z.number(),source,warnings:z.array(z.string())}).strict();
const itemSchema=z.object({item:entitySchema,source,warnings:z.array(z.string())}).strict();
export type Entity=z.infer<typeof entitySchema>;
export class DataHttpAdapter {
  readonly baseUrl:string;
  constructor(baseUrl:string,private tools:ToolExecutor,private save:(ctx:ToolContext,body:unknown)=>Promise<string>){
    const u=new URL(baseUrl);if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.search||u.hash)throw Error('Invalid data API origin');this.baseUrl=baseUrl.replace(/\/$/,'');
  }
  async request(path:string,body?:unknown):Promise<unknown>{
    if(!path.startsWith('/api/v1/')&&path!=='/ready')throw Error('Invalid data path');
    const response=await fetch(this.baseUrl+path,{method:body===undefined?'GET':'POST',headers:{'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(12000),redirect:'error'});
    if(!response.ok)throw Error(`DATA_HTTP_${response.status}`);
    const reader=response.body!.getReader(),chunks:Uint8Array[]=[];let size=0;
    try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>8*1024*1024)throw Error('DATA_RESPONSE_TOO_LARGE');chunks.push(value);}}finally{await reader.cancel();}
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  }
  async capabilities(ctx:ToolContext){return this.tools.run(ctx,'capabilities',{},()=>this.request('/api/v1/capabilities'),30000);}
  async assessment(ctx:ToolContext):Promise<{items:Entity[];truncated:false;refs:string[];rowRefs:Map<string,string>}>{
    return this.tools.run(ctx,'listings',{kind:'canonical_assessment',dataset:ctx.dataset},async()=>{
      const cap=await this.capabilities(ctx) as any;
      if(cap.canonical_assessment_scan!=='/api/v1/listings/assessment/search')throw Error('CANONICAL_ASSESSMENT_SCAN_REQUIRED');
      const items:Entity[]=[],refs:string[]=[],rowRefs=new Map<string,string>(),seen=new Set<string>();let cursor:string|null=null;
      do{
        const raw=await this.request('/api/v1/listings/assessment/search',{listing_type:'RENT',cursor,page_size:1000});
        const page=pageSchema.parse(raw);
        if(page.source.role!=='canonical_listings'||page.has_more!==(page.next_cursor!==null)||page.has_more&&!page.items.length)throw Error('DATA_PAGE_INCONSISTENT');
        const ref=await this.save(ctx,raw);refs.push(ref);
        for(const row of page.items){if(rowRefs.has(row.entity_id))throw Error('DATA_DUPLICATE_ENTITY');rowRefs.set(row.entity_id,ref);items.push(row);}
        if(items.length>100000)throw Error('FULL_DATASET_SCAN_BUDGET_EXCEEDED');
        cursor=page.next_cursor;if(cursor&&seen.has(cursor))throw Error('DATA_REPEATED_CURSOR');if(cursor)seen.add(cursor);
        if(seen.size>=100)throw Error('DATA_PAGE_BUDGET_EXCEEDED');
      }while(cursor);
      const expected=cap.listing_snapshot?.expected_counts?.properties;
      if(!Number.isInteger(expected)||items.length!==expected)throw Error('FULL_DATASET_COUNT_MISMATCH');
      return {items,truncated:false as const,refs,rowRefs};
    },30000);
  }
  async all(ctx:ToolContext,kind:'listings'|'places',max=2000):Promise<{items:Entity[];truncated:boolean;refs:string[]}>{
    const tool=ctx.agent==='a'?'resolve_place':kind;return this.tools.run(ctx,tool,{kind,max},async()=>{
      const seen=new Set<string>(),items:Entity[]=[],refs:string[]=[];let cursor:string|null=null;
      do{
        const raw=await this.request(`/api/v1/${kind}/search`,{filters:kind==='listings'?{listing_type:'RENT',include_planned:true,coordinates_only:false}:{},cursor,page_size:1000});
        const page=pageSchema.parse(raw);if(page.has_more!==(page.next_cursor!==null)||page.has_more&&!page.items.length)throw Error('DATA_PAGE_INCONSISTENT');
        refs.push(await this.save(ctx,raw));items.push(...page.items);
        if(items.length>max)return {items:items.slice(0,max),truncated:true,refs};
        cursor=page.next_cursor;if(cursor&&seen.has(cursor))throw Error('DATA_REPEATED_CURSOR');if(cursor)seen.add(cursor);
        if(seen.size>100)throw Error('DATA_PAGE_BUDGET_EXCEEDED');
      }while(cursor);
      if(new Set(items.map(x=>x.entity_id)).size!==items.length)throw Error('DATA_DUPLICATE_ENTITY');
      return {items,truncated:false,refs};
    },30000);
  }
  async listing(ctx:ToolContext,id:string){
    if(!/^\d+$/.test(id))throw Error('Invalid listing ID');
    return this.tools.run(ctx,'listing_detail',{id},async()=>{const raw=await this.request(`/api/v1/listings/${encodeURIComponent(id)}`),value=itemSchema.parse(raw);return {value:value.item.properties,ref:await this.save(ctx,raw)};},30000);
  }
  async resolve(ctx:ToolContext,name:string){
    return this.tools.run(ctx,'resolve_place',{name},async()=>{
      const raw=await this.request('/api/v1/places/search',{filters:{q:name},page_size:50,cursor:null});const page=pageSchema.parse(raw);return {items:page.items,ref:await this.save(ctx,raw)};
    },30000);
  }
}
