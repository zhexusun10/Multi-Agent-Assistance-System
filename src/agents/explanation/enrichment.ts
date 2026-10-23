import {z} from 'zod';
import type {RecommendationResponse} from '../../contracts/recommendation-v1.js';
import type {DataHttpAdapter} from '../../adapters/data-http.js';
import type {ToolContext,ToolExecutor} from '../../tools/executor.js';
import {normalizeListingDetails,normalizeNearby} from '../../domain/listing-presentation.js';
import {displayedCandidates} from '../../domain/selection.js';

const entity=z.object({entity_id:z.string(),label:z.string(),properties:z.record(z.string(),z.unknown())});
const detailSchema=z.object({item:entity});
const contextSchema=z.object({listing:entity,relationships:z.array(z.object({type:z.string(),target:entity,evidence:z.record(z.string(),z.unknown())}))});
export type ExplanationEnrichment={
  listing_id:string;dataset:string;source_ref:string;
  details:Record<string,unknown>|null;nearby:ReturnType<typeof normalizeNearby>;
  sections:{details:'available'|'unavailable';nearby:'available'|'unavailable'};
};
/** Only reads candidates already selected by Recommendation. No search, images,
 * new homes, new criterion outcomes, scores or ranking are produced here. */
export class ExplanationEnricher{
  constructor(private data:Pick<DataHttpAdapter,'request'>,private tools:ToolExecutor,private save:(ctx:ToolContext,body:unknown)=>Promise<string>){}
  async load(rec:RecommendationResponse,ctx:ToolContext):Promise<ExplanationEnrichment[]>{
    if(ctx.agent!=='c'||rec.state.coverage.dataset_version!==ctx.dataset)throw Error('EXPLANATION_ENRICHMENT_SCOPE');
    const rows=displayedCandidates(rec);
    if(rows.length>8||new Set(rows.map(r=>r.listing.listing_id)).size!==rows.length)throw Error('EXPLANATION_CANDIDATE_SCOPE');
    const out:ExplanationEnrichment[]=[];
    // Two homes at a time; each uses two bounded read-only requests. Partial
    // failures retain recommendation facts and never become "no amenities".
    for(let i=0;i<rows.length;i+=2){
      const batch=await Promise.all(rows.slice(i,i+2).map(async row=>{
        const id=row.listing.listing_id;if(!/^\d{1,20}$/.test(id))throw Error('INVALID_LISTING_ID');
        const [detail,context]=await Promise.allSettled([
          this.tools.run(ctx,'listing_detail',{id,purpose:'decision_support'},async()=>{
            const value=detailSchema.parse(await this.data.request(`/api/v1/listings/${id}`));
            if(value.item.entity_id!==`Listing:${id}`||String(value.item.properties.listing_id)!==id)throw Error('EXPLANATION_DETAIL_IDENTITY');
            const detail=normalizeListingDetails(value.item.properties);
            return Object.fromEntries(['listing_id','price','bedrooms','bathrooms','floor_area_sqft','property_type','nearest_mrt','mrt_distance_m'].map(field=>[field,detail[field]]));
          },30000),
          this.tools.run(ctx,'listing_context',{id,purpose:'decision_support'},async()=>{
            const value=contextSchema.parse(await this.data.request(`/api/v1/listings/${id}/context`));
            if(value.listing.entity_id!==`Listing:${id}`)throw Error('EXPLANATION_CONTEXT_IDENTITY');
            return normalizeNearby(value.relationships);
          },30000),
        ]);
        const value={listing_id:id,dataset:ctx.dataset,details:detail.status==='fulfilled'?detail.value:null,nearby:context.status==='fulfilled'?context.value:[],
          sections:{details:detail.status==='fulfilled'?'available' as const:'unavailable' as const,nearby:context.status==='fulfilled'?'available' as const:'unavailable' as const}};
        // Store just the normalized evidence actually available to this reader,
        // not agent contact data, arbitrary metadata or photo descriptions.
        const source_ref=await this.save(ctx,{kind:'explanation_enrichment_v1',request_id:rec.request_id,retrieved_at:new Date().toISOString(),paths:[`/api/v1/listings/${id}`,`/api/v1/listings/${id}/context`],...value});
        return {...value,source_ref};
      }));out.push(...batch);
    }
    return out;
  }
}
