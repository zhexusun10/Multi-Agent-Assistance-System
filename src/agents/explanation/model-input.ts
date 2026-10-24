import type {ExplanationBundle,ExplanationOutput} from './contracts.js';

/** Lossless condition observations, shorter prompt refs, and focused comparisons.
 * Full source IDs/numerical allowances stay in the server-side validator bundle.
 * This does not select homes, change their order, or recompute recommendation.
 */
export function explanationModelInput(bundle:ExplanationBundle){
  const selected=new Set(bundle.decision_support?.target_listing_ids||bundle.listings.filter(l=>bundle.question.includes(l.title)||bundle.question.includes(l.listing_id)).map(l=>l.listing_id));
  const ordinals:Record<string,number>={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10};
  for(const match of bundle.question.matchAll(/第\s*([一二三四五六七八九十\d]+)\s*套|(?:property|listing)\s*#?\s*(\d+)\b/gi)){
    const index=(ordinals[match[1]]??Number(match[1]||match[2]))-1;
    if(bundle.listings[index])selected.add(bundle.listings[index].listing_id);
  }
  if(!selected.size)for(const id of (bundle.ranking.ranked_listing_ids.length?bundle.ranking.ranked_listing_ids:bundle.listings.map(l=>l.listing_id)).slice(0,2))selected.add(id);
  // A focused explanation may compare a selected home with the first ranked one.
  if(selected.size===1&&bundle.listings[0])selected.add(bundle.listings[0].listing_id);
  // General guides use per-home highlights instead of every pairwise table.
  // Focused questions keep the relevant pairwise comparisons as before.
  const guide=bundle.decision_support?.mode==='guide';
  const highlights=new Set(bundle.decision_support?.profiles.filter(p=>selected.has(p.listing_id)).flatMap(p=>p.highlight_refs)||[]);
  const facts=bundle.facts.filter(f=>!f.ref.startsWith('native-rationale:')&&!f.ref.startsWith('listing:')&&
    (!bundle.decision_support||bundle.decision_support.explain_order||f.kind!=='ranking')&&
    (f.kind!=='comparison'||highlights.has(f.ref)||(!guide&&f.listing_ids.every(id=>selected.has(id)))));
  const toModel=new Map(facts.map((f,i)=>[f.ref,'f'+(i+1)])),toInternal=new Map([...toModel].map(([a,b])=>[b,a]));
  // Titles are already keyed once by listing_id in listings; do not repeat
  // every street address in each requirement's observation matrix.
  const requirements=bundle.requirements.map(r=>({...r,unit:r.results[0]?.unit??null,results:r.results.map(({title,unit,...observation})=>observation)}));
  const listings=bundle.listings.map(({fact_refs,...listing})=>listing);
  const input=JSON.parse(JSON.stringify({...bundle,requirements,listings,facts}),function(key,value){
    if(['source_refs','numbers','literals','derived_from','kind'].includes(key))return undefined;
    return typeof value==='string'&&toModel.has(value)?toModel.get(value):value;
  });
  return {input,toModel,toInternal,source_characters:JSON.stringify(bundle).length,prompt_characters:JSON.stringify(input).length};
}

export function translateExplanationRefs(raw:unknown,aliases:ReadonlyMap<string,string>):unknown{
  if(!raw||typeof raw!=='object'||!Array.isArray((raw as any).paragraphs))return raw;
  return {...raw,paragraphs:(raw as any).paragraphs.map((p:any)=>p&&typeof p==='object'&&Array.isArray(p.support_refs)
    ?{...p,support_refs:p.support_refs.map((ref:unknown)=>typeof ref==='string'?(aliases.get(ref)||ref):ref)}:p)};
}
export function modelExplanationOutput(output:ExplanationOutput,aliases:ReadonlyMap<string,string>){return translateExplanationRefs(output,aliases);}
