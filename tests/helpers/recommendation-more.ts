import {explanationFixture} from '../../evaluation/explanation-cases.js';
import {validateResponse,type RecommendationRequest} from '../../src/contracts/recommendation-v1.js';
export function moreFixture(count=10){
  const f=explanationFixture('stable_order');delete f.rec.native;
  const original=f.rec.recommendations[0],oldId=original.listing.listing_id;
  const evidence=f.rec.evidence.filter(e=>e.listing_id===oldId);
  const rename=(value:unknown,from:string,to:string):any=>{let text=JSON.stringify(value);for(const e of evidence)text=text.replaceAll(JSON.stringify(e.evidence_id),JSON.stringify(e.evidence_id+':'+to));return JSON.parse(text.replaceAll(from,to));};
  const rows=Array.from({length:count},(_,i)=>{
    const id=String(87000001+i),row=rename(original,oldId,id);row.rank=i+1;row.listing.title='Batch Home '+String.fromCharCode(65+i);
    return row;
  });
  f.rec.recommendations=rows;f.rec.unverified_candidates=[];
  f.rec.evidence=rows.flatMap(c=>rename(evidence,oldId,c.listing.listing_id));
  f.rec.state.counts={assessed:count,eligible:count,unverified:0,excluded:0};
  f.rec.status=count?'success':'no_match';
  return f;
}
export function moreResponse(request:RecommendationRequest,count=10){
  const f=moreFixture(count),rec={...f.rec,request_id:request.request_id,conversation_id:request.conversation_id,requirements_version:request.requirements_version};
  return validateResponse(rec,request);
}
