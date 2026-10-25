import {z} from 'zod';
import type {DataHttpAdapter} from '../adapters/data-http.js';

const entity=z.object({entity_id:z.string(),label:z.string(),properties:z.record(z.string(),z.unknown())}).passthrough();
const detailSchema=z.object({item:entity}).passthrough();
const imagesSchema=z.object({items:z.array(z.object({id:z.number(),image_type:z.string(),image_url:z.string(),caption:z.string().nullable(),display_order:z.number().nullable().optional(),source_page:z.string()}).passthrough()),has_more:z.boolean(),next_cursor:z.string().nullable()}).passthrough();
const contextSchema=z.object({relationships:z.array(z.object({type:z.string(),target:entity,evidence:z.record(z.string(),z.unknown())}))}).passthrough();
const fields=['listing_id','title','property_type','price','currency','bedrooms','bathrooms','floor_area_sqft','land_area_sqft','full_address','short_address','street_name','street_number','postal_code','district_code','district_text','region_code','region_text','floor_level','build_year','tenure','tenure_category','nearest_mrt','mrt_distance_m','mrt_walking_mins','psf','agent_name','agency_name','posted_at','updated_at','detail_fetched','thumbnail_url','url'] as const;
export function propertyImageUrl(value:unknown):string|null{
  if(typeof value!=='string'||value.length>4000)return null;
  try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&!u.port&&(u.hostname==='pgimgs.com'||u.hostname.endsWith('.pgimgs.com'))?u.href:null;}catch{return null;}
}
const text=(x:unknown)=>typeof x==='string'&&x.trim()?x.trim():null;
const number=(x:unknown)=>typeof x==='number'&&Number.isFinite(x)&&x>=0?x:null;
export function normalizeListingDetails(raw:Record<string,unknown>){
  const result:Record<string,unknown>={};for(const field of fields)result[field]=raw[field]??null;
  for(const field of ['price','bedrooms','bathrooms','floor_area_sqft','land_area_sqft','build_year','mrt_distance_m','mrt_walking_mins','psf'])result[field]=number(raw[field]);
  result.thumbnail_url=propertyImageUrl(raw.thumbnail_url);return result;
}
export function normalizePhotos(rows:z.infer<typeof imagesSchema>['items']){
  const priority:Record<string,number>={PHOTO:0,PREVIEW:1,THUMBNAIL:2,FLOOR_PLAN:3};
  const seen=new Set<string>();return [...rows].filter(r=>Object.hasOwn(priority,r.image_type)).sort((a,b)=>priority[a.image_type]-priority[b.image_type]||(a.display_order??0)-(b.display_order??0)||a.id-b.id).flatMap(r=>{
    const url=propertyImageUrl(r.image_url);if(!url)return [];const u=new URL(url),identity=(u.hostname+u.pathname).replace(/\.V\d+(?=\/|\.|$)/gi,'');if(seen.has(identity))return [];seen.add(identity);
    return [{id:r.id,url,caption:r.caption||'',kind:r.image_type==='FLOOR_PLAN'?'floor_plan':'photo',source_page:r.source_page}];
  });
}
export function normalizeNearby(rows:z.infer<typeof contextSchema>['relationships']){
  const seen=new Set<string>();return rows.filter(r=>['NEAR_PLACE','NEAR_MRT'].includes(r.type)).flatMap(r=>{
    const p=r.target.properties,e=r.evidence,category=text(p.category)||(r.target.label==='MRT'?'MRT':r.target.label),distance=number(e.distance_m);
    // Named public amenities, not hundreds of individual campus rooms/buildings.
    if(category==='CampusBuilding'||distance===null||seen.has(r.target.entity_id))return [];
    const name=text(p.name_en)||text(p.name)||text(p.station_name)||text(p.name_local);if(!name)return [];seen.add(r.target.entity_id);
    const metric=r.type==='NEAR_MRT'?'platform_reported':'straight_line';
    let source_url:string|null=null;try{const u=new URL(String(p.source_url||''));if(u.protocol==='https:'&&(u.hostname==='www.openstreetmap.org'||u.hostname==='openstreetmap.org'))source_url=u.href;}catch{}
    return [{id:r.target.entity_id,name,category,distance_m:distance,metric,walking_mins:r.type==='NEAR_MRT'?number(e.walking_mins):null,source_url,source:r.type==='NEAR_MRT'?"PropertyGuru listing information":'OpenStreetMap / Neo4j',computed_at:text(e.computed_at),radius_m:number(e.radius_m),route_verified:false}];
  }).sort((a,b)=>a.distance_m-b.distance_m||a.id.localeCompare(b.id));
}
export type ListingPresentation=Awaited<ReturnType<ListingPresentationService['load']>>;
/** Public display data enriches a chosen candidate; it never filters, reranks or changes its evidence. */
export class ListingPresentationService{
  private cache=new Map<string,{until:number;value:ListingPresentation}>();
  private pending=new Map<string,Promise<ListingPresentation>>();
  constructor(readonly data:Pick<DataHttpAdapter,'request'>,readonly dataset:string){}
  async get(id:string){
    if(!/^\d{1,20}$/.test(id))throw Error('INVALID_LISTING_ID');
    const key=this.dataset+':'+id,cached=this.cache.get(key);if(cached&&cached.until>Date.now())return cached.value;
    if(this.pending.has(key))return this.pending.get(key)!;
    const promise=this.load(id).then(value=>{if(this.cache.size>=200)this.cache.delete(this.cache.keys().next().value!);this.cache.set(key,{value,until:Date.now()+(value.warnings.length?5000:300000)});return value;}).finally(()=>this.pending.delete(key));this.pending.set(key,promise);return promise;
  }
  async load(id:string){
    const results=await Promise.allSettled([
      this.data.request(`/api/v1/listings/${id}`).then(x=>detailSchema.parse(x)),
      this.data.request(`/api/v1/listings/${id}/images?page_size=1000`).then(x=>imagesSchema.parse(x)),
      this.data.request(`/api/v1/listings/${id}/context`).then(x=>contextSchema.parse(x)),
    ]);
    const [detail,images,context]=results,warnings:string[]=[];
    if(detail.status==='rejected')warnings.push("Listing details could not be loaded. Try again later.");
    if(images.status==='rejected')warnings.push("The photo catalogue could not be loaded; this does not mean no photos exist.");
    if(context.status==='rejected')warnings.push("Nearby places could not be loaded; this does not mean no amenities exist.");
    const details=detail.status==='fulfilled'?normalizeListingDetails(detail.value.item.properties):null;
    const photos=images.status==='fulfilled'?normalizePhotos(images.value.items):[];
    const nearby=context.status==='fulfilled'?normalizeNearby(context.value.relationships):[];
    return {listing_id:id,dataset:this.dataset,details,photos,nearby,warnings,
      sections:{details:detail.status==='fulfilled'?'available':'unavailable',photos:images.status==='fulfilled'?'available':'unavailable',nearby:context.status==='fulfilled'?'available':'unavailable'},
      photos_has_more:images.status==='fulfilled'&&images.value.has_more,
      sources:{details:"PostgreSQL historical listing record",photos:"PropertyGuru recorded photo link",nearby:"Neo4j / OpenStreetMap and property-reported transport"},
      unknown_facilities:"Current structured data does not establish furnishing, private bathroom use, air conditioning, cooking or pet policies. Ask the agent; photos and property type do not prove these facilities are included.",
      nearby_note:"Only recorded nearby places are shown, without complete coverage. OSM uses straight-line distances; property-reported transport is identified separately. Actual routes are unverified.",
    };
  }
}
