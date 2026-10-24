// Independent select-v1 oracle for the env-gated live tests (spec section 8, R6). The caller computes the
// full-pool score order O itself (SQL rows + its own scoring formula) and never reads it from the native
// response; this helper then checks that the native selection is one select-v1 can produce from O:
//   - K = min(result_limit, |O|) unique items, rank = display position, score_rank = position in O + 1;
//   - display order = leaders in O order, then the drawn items in O order (score_rank strictly increasing);
//   - empty / small_pool: everything in O order; ranked: O's prefix exactly (|G| == m);
//   - near_tie_sampling: leaders = O prefix, the other m items drawn from G = the |G| items right after them;
//   - cold_start_exploration: leaders = O prefix, the other m items drawn from R = the candidate_count items
//     right after them (R contains G);
//   - optional: with the caller's own scores, the tie group start/size match |s_i - s_K| <= epsilon/2
//     (evaluated with a small tolerance, because the oracle's doubles may differ in the last bits).
// select-v2 (current) is select-v1 outside cold start. In cold start it is checked by selectV2ColdViolations:
// window = O's first window_size items (window_size <= exploration window, all of O when smaller),
// intermediate = leaders (O prefix) + sampled non-leaders of the window, in O order, intermediate_limit =
// min(intermediate size, window); returned = intermediate[:K] (leader reasons first, never rebalanced).
// Which items were drawn depends on the seed, so it is never compared to a fixed list.
export type SelectV1Item={listing:{listing_id:string};rank:number;score_rank:number;selection_reason:string};
export type SelectV2Exploration={window_size:number;intermediate_limit:number;intermediate_ids:string[];intermediate_leaders:number;
  intermediate_ranked:number;final_ids:string[];final_leaders:number;final_sampled:number;final_ranked:number};
export type SelectV1Native={recommendations:SelectV1Item[];selection:null|{mode:string;algorithm_version:string;result_limit:number;leaders:number;
  tie_group:null|{size:number;epsilon:number};exploration:null|{candidate_count:number}|(SelectV2Exploration&{candidate_count:number})}};

/** select-v2 cold start: the window / intermediate / returned-prefix rules, from O alone. */
export function selectV2ColdViolations(native:SelectV1Native,order:string[],resultLimit:number,scores?:Map<string,number>,tolerance=1e-9,explorationWindow=30,intermediateSize=10):string[]{
  const v:string[]=[],sel=native.selection!,e=sel.exploration as SelectV2Exploration,ids=native.recommendations.map(c=>c.listing.listing_id);
  const window=Math.min(explorationWindow,order.length),limit=Math.min(intermediateSize,window),k=Math.min(resultLimit,order.length);
  const position=new Map(order.map((id,i)=>[id,i+1])),same=(a:string[],b:string[])=>a.length===b.length&&a.every((x,i)=>x===b[i]);
  if(e.window_size!==window)v.push(`window_size ${e.window_size} != min(${explorationWindow}, |O|) = ${window}`);
  if(e.intermediate_limit!==limit||e.intermediate_ids.length!==limit)v.push(`intermediate must hold ${limit} listings`);
  if(new Set(e.intermediate_ids).size!==e.intermediate_ids.length)v.push('duplicate intermediate IDs');
  const ranks=e.intermediate_ids.map(id=>position.get(id)??Infinity);
  if(ranks.some(r=>r>window))v.push(`intermediate listings must come from O positions 1..${window}`);
  if(ranks.some((r,i)=>i&&r<=ranks[i-1]))v.push('intermediate selection not in O order');
  if(!same(e.intermediate_ids.slice(0,e.intermediate_leaders),order.slice(0,e.intermediate_leaders)))v.push('intermediate leaders must be O prefix');
  if(!same(ids,e.intermediate_ids.slice(0,k))||!same(ids,e.final_ids))v.push('returned listings must be the first K intermediate listings');
  const ranked=sel.mode==='ranked',leaders=ranked?0:Math.min(e.intermediate_leaders,k),reason=ranked?'ranked':'price_exploration_sample';
  if(ranked&&(window>intermediateSize||e.intermediate_leaders||e.intermediate_ranked!==window))v.push('ranked cold start keeps a window of at most intermediate size, all as ranked');
  const expected=ranked?Array(k).fill('ranked'):[...Array(leaders).fill('leader'),...Array(k-leaders).fill(reason)];
  if(!same(native.recommendations.map(c=>c.selection_reason),expected))v.push(`reasons must be leader x ${leaders} then ${reason}`);
  if(sel.leaders!==leaders||e.final_leaders!==leaders||e.final_ranked!==(ranked?k:0)||e.final_sampled!==(ranked?0:k-leaders))v.push('leader / sampled / ranked counts disagree with the returned prefix');
  if(scores&&!ranked){
    // Leaders = the window items before the tie group around rank min(intermediate size, window), within the window.
    const s=order.slice(0,window).map(id=>scores.get(id));
    if(s.some(x=>x===undefined))v.push('oracle scores missing for some window items');
    else{
      const half=(sel.tie_group?.epsilon??0)/2,sk=s[limit-1]!,inside=(x:number,slack:number)=>Math.abs(x-sk)<=half+slack;
      const startOf=(slack:number)=>{let i=limit-1;while(i>0&&inside(s[i-1]!,slack))i--;return i;};
      const [startMin,startMax]=[startOf(tolerance),startOf(-tolerance)];
      if(e.intermediate_leaders<startMin||e.intermediate_leaders>startMax)v.push(`intermediate leaders ${e.intermediate_leaders} outside the oracle tie-group start [${startMin}, ${startMax}] around rank ${limit}`);
    }
  }
  return v;
}

export function selectV1Violations(native:SelectV1Native,order:string[],resultLimit:number,scores?:Map<string,number>,tolerance=1e-9):string[]{
  const v:string[]=[],sel=native.selection,items=native.recommendations,ids=items.map(c=>c.listing.listing_id);
  if(!sel)return ['selection summary missing'];
  if(sel.algorithm_version!=='select-v1'&&sel.algorithm_version!=='select-v2')v.push(`algorithm_version ${sel.algorithm_version} is not select-v1/select-v2`);
  if(sel.result_limit!==resultLimit)v.push(`selection.result_limit ${sel.result_limit} != ${resultLimit}`);
  const k=Math.min(resultLimit,order.length),position=new Map(order.map((id,i)=>[id,i+1]));
  if(ids.length!==k)v.push(`returned ${ids.length} items, expected K = ${k}`);
  if(new Set(ids).size!==ids.length)v.push('duplicate listing IDs');
  items.forEach((c,i)=>{
    if(c.rank!==i+1)v.push(`rank ${c.rank} at display position ${i+1}`);
    const p=position.get(c.listing.listing_id);
    if(p===undefined)v.push(`${c.listing.listing_id} is not in the independently computed pool`);
    else if(c.score_rank!==p)v.push(`${c.listing.listing_id}: score_rank ${c.score_rank} != position in O ${p}`);
    if(i&&c.score_rank<=items[i-1].score_rank)v.push(`display order not in O order at position ${i+1}`);
  });
  const reasons=items.map(c=>c.selection_reason),same=(a:string[],b:string[])=>a.length===b.length&&a.every((x,i)=>x===b[i]);
  if(order.length===0){if(sel.mode!=='empty')v.push(`mode ${sel.mode} for an empty pool`);return v;}
  if(order.length<=resultLimit){
    if(sel.mode!=='small_pool')v.push(`mode ${sel.mode} for a pool of ${order.length} <= ${resultLimit}`);
    if(!same(ids,order))v.push('small_pool must return the whole pool in O order');
    if(reasons.some(r=>r!=='small_pool'))v.push('small_pool reasons expected');
    return v;
  }
  if(sel.algorithm_version==='select-v2'&&sel.exploration&&'window_size' in sel.exploration)
    return [...v,...selectV2ColdViolations(native,order,resultLimit,scores,tolerance)];
  const leaders=sel.leaders,m=k-leaders,g=sel.tie_group?.size??0;
  if(!same(ids.slice(0,leaders),order.slice(0,leaders)))v.push(`leaders are not O's first ${leaders} items`);
  if(m<1)v.push(`m = K - leaders = ${m} < 1`);
  if(!sel.tie_group)v.push('tie_group missing for a pool larger than result_limit');
  if(scores&&sel.tie_group){
    const s=order.map(id=>scores.get(id)),sk=s[k-1];
    if(s.some(x=>x===undefined))v.push('oracle scores missing for some pool items');
    else{
      const half=sel.tie_group.epsilon/2,inside=(x:number,slack:number)=>Math.abs(x-sk!)<=half+slack;
      // Contiguous group around position K (no chaining): earliest/latest start and end under +/- tolerance.
      const startOf=(slack:number)=>{let i=k-1;while(i>0&&inside(s[i-1]!,slack))i--;return i;};
      const endOf=(slack:number)=>{let i=k;while(i<s.length&&inside(s[i]!,slack))i++;return i;};
      const [startMin,startMax]=[startOf(tolerance),startOf(-tolerance)],[endMin,endMax]=[endOf(-tolerance),endOf(tolerance)];
      if(leaders<startMin||leaders>startMax)v.push(`leaders ${leaders} outside the oracle tie-group start [${startMin}, ${startMax}]`);
      if(leaders+g<endMin||leaders+g>endMax)v.push(`tie group end ${leaders+g} outside the oracle range [${endMin}, ${endMax}]`);
    }
  }
  const drawnRanks=items.slice(leaders).map(c=>c.score_rank);
  if(sel.mode==='ranked'){
    if(!same(ids,order.slice(0,k)))v.push('ranked must return O prefix exactly');
    if(reasons.some(r=>r!=='ranked'))v.push('ranked reasons expected');
    if(sel.tie_group&&leaders+g!==k)v.push(`ranked requires |G| == m (leaders ${leaders} + |G| ${g} != K ${k})`);
  }else if(sel.mode==='near_tie_sampling'||sel.mode==='cold_start_exploration'){
    const tie=sel.mode==='near_tie_sampling',reason=tie?'tie_group_sample':'price_exploration_sample';
    const window=tie?g:(sel.exploration?.candidate_count??0);
    if(!tie&&!sel.exploration)v.push('exploration summary missing in cold_start_exploration');
    if(!same(reasons,[...Array(leaders).fill('leader'),...Array(Math.max(0,m)).fill(reason)]))v.push(`reasons must be leader x ${leaders} then ${reason}`);
    if(tie&&g<=m)v.push(`near_tie_sampling requires |G| ${g} > m ${m}`);
    if(!tie&&window<Math.max(g,m))v.push(`exploration candidates ${window} must cover the tie group (${g}) and m (${m})`);
    if(drawnRanks.some(r=>r<=leaders||r>leaders+window))v.push(`drawn items must come from O positions ${leaders+1}..${leaders+window}`);
  }else v.push(`mode ${sel.mode} invalid for a pool of ${order.length} > ${resultLimit}`);
  return v;
}

/** Full-pool order O: score descending, then listing_id in code-point (numpy str) order, like scoring_v2. */
export function scoreOrder(scores:Map<string,number>):string[]{
  return [...scores].sort(([a,x],[b,y])=>y-x||(a<b?-1:a>b?1:0)).map(([id])=>id);
}

/** numpy.percentile(values, q, method="linear") over the finite values (R11); NaN when there are none. */
export function linearPercentile(values:number[],q:number):number{
  const x=values.filter(Number.isFinite).sort((a,b)=>a-b);
  if(!x.length)return NaN;
  const h=(x.length-1)*q/100,lo=Math.floor(h),hi=Math.min(lo+1,x.length-1);
  return x[lo]+(h-lo)*(x[hi]-x[lo]);
}

// ---- fused-v2 behaviour oracle (spec section 7, R1d, R11), independent of the Python scorer -------------
export type OracleRow={listing_id:string;price:number|null;bedrooms:number|null;floor_area_sqft:number|null;property_type:string|null;
  district_code:string|null;latitude:number|null;longitude:number|null};
export const SIMILARITY_FEATURES=['price','property_type','bedrooms','floor_area_sqft','geography'] as const;
export type OracleSettings={soft_weight:number;behavior_weight:number;appointment_weight:number;browsing_weight:number;
  feature_weights:Record<typeof SIMILARITY_FEATURES[number],number>;numeric_scaling:string;geographic_scale_m:number;
  geography_district_fallback:boolean;min_comparison_coverage:number};

/** The few result-affecting values the oracle needs, read from recommendation/settings/recommendation.yaml text. */
export function oracleSettings(yaml:string):OracleSettings{
  const value=(key:string,after?:string)=>{const text=after?yaml.slice(yaml.indexOf(after)):yaml;const m=text.match(new RegExp(`^\\s*${key}:\\s*([^\\s#]+)`,'m'));if(!m)throw Error(`settings key ${key} not found`);return m[1];};
  const num=(key:string,after?:string)=>{const n=Number(value(key,after));if(!Number.isFinite(n))throw Error(`settings key ${key} is not a number`);return n;};
  return {soft_weight:num('soft_weight'),behavior_weight:num('behavior_weight'),appointment_weight:num('appointment_weight'),browsing_weight:num('browsing_weight'),
    feature_weights:Object.fromEntries(SIMILARITY_FEATURES.map(f=>[f,num(f,'feature_weights:')])) as OracleSettings['feature_weights'],
    numeric_scaling:value('numeric_scaling'),geographic_scale_m:num('geographic_scale_m'),geography_district_fallback:value('geography_district_fallback')==='true',
    min_comparison_coverage:num('min_comparison_coverage')};
}

const finite=(x:unknown):x is number=>typeof x==='number'&&Number.isFinite(x);
const validPoint=(r:OracleRow)=>finite(r.latitude)&&finite(r.longitude)&&Math.abs(r.latitude)<=90&&Math.abs(r.longitude)<=180;
export function haversine(a:{latitude:number;longitude:number},b:{latitude:number;longitude:number}){
  const rad=(x:number)=>x*Math.PI/180,h=Math.sin(rad(b.latitude-a.latitude)/2)**2+Math.cos(rad(a.latitude))*Math.cos(rad(b.latitude))*Math.sin(rad(b.longitude-a.longitude)/2)**2;
  return 6371008.8*2*Math.asin(Math.sqrt(Math.min(1,Math.max(0,h))));
}

/** Pair similarity of every pool row to one reference (null = unavailable; identity pair = 1).
 * Active features = weight > 0, not suppressed, numeric spread (p05_p95 over the FULL pool) > 0; a feature missing
 * on either side is not compared; renormalised over compared weight; unavailable below min_comparison_coverage. */
export function pairSimilarities(pool:OracleRow[],referenceId:string,settings:OracleSettings,suppressed:string[]):Map<string,number|null>{
  if(!['p05_p95','minmax'].includes(settings.numeric_scaling))throw Error(`unsupported scaling ${settings.numeric_scaling}`);
  const ref=pool.find(r=>r.listing_id===referenceId);if(!ref)throw Error(`reference ${referenceId} is not in the pool`);
  const spread:Record<string,number>={},active:string[]=[];
  for(const f of SIMILARITY_FEATURES){
    if(!(settings.feature_weights[f]>0)||suppressed.includes(f))continue;
    if(f==='price'||f==='bedrooms'||f==='floor_area_sqft'){const values=pool.map(r=>r[f]).filter(finite);const s=settings.numeric_scaling==='minmax'?Math.max(...values)-Math.min(...values):linearPercentile(values,95)-linearPercentile(values,5);if(!(s>0))continue;spread[f]=s;}
    if(f==='property_type'&&new Set(pool.map(r=>r.property_type).filter(x=>x!=null)).size<=1)continue;
    if(f==='geography'){
      const points=new Set(pool.filter(validPoint).map(r=>`${r.latitude},${r.longitude}`));
      const districts=new Set(pool.map(r=>r.district_code).filter(x=>x!=null));
      if(points.size<=1&&!(settings.geography_district_fallback&&pool.some(r=>!validPoint(r))&&districts.size>1))continue;
    }
    active.push(f);
  }
  const total=active.reduce((t,f)=>t+settings.feature_weights[f],0),out=new Map<string,number|null>();
  const maxDistance=validPoint(ref)?Math.max(0,...pool.filter(validPoint).map(r=>haversine(r as any,ref as any))):0;
  for(const row of pool){
    if(row.listing_id===referenceId){out.set(row.listing_id,1);continue;}
    if(total<=0){out.set(row.listing_id,null);continue;}
    let compared=0,weighted=0;
    for(const f of active){
      const w=settings.feature_weights[f];let s:number|null=null;
      if(f==='property_type'){if(row.property_type!=null&&ref.property_type!=null)s=Number(row.property_type===ref.property_type);}
      else if(f==='geography'){
        if(validPoint(row)&&validPoint(ref))s=maxDistance>0?Math.max(0,1-haversine(row as any,ref as any)/maxDistance):0.5;
        else if(settings.geography_district_fallback&&row.district_code!=null&&ref.district_code!=null)s=Number(row.district_code===ref.district_code);
      }else{const a=row[f],b=ref[f];if(finite(a)&&finite(b))s=Math.max(0,1-Math.abs(a-b)/spread[f]);}
      if(s!==null){compared+=w;weighted+=w*s;}
    }
    out.set(row.listing_id,compared>0&&compared/total>=settings.min_comparison_coverage?Math.min(1,Math.max(0,weighted/compared)):null);
  }
  return out;
}

/** combined_score with no soft criteria, no appointments and ONE available browsing reference: the normalised
 * reference weight is exactly 1, so the duration cancels (single-reference normalisation). */
export function singleBrowsingReferenceScores(pool:OracleRow[],referenceId:string,settings:OracleSettings,suppressed:string[]):Map<string,number>{
  const sim=pairSimilarities(pool,referenceId,settings,suppressed);
  return new Map([...sim].map(([id,s])=>[id,s===null?0:settings.behavior_weight*settings.browsing_weight*s]));
}

/** Canonical listing row as recommendation/repositories/listing_repository.py normalises it (oracle side). */
export function oracleRow(p:any):OracleRow{
  const num=(x:unknown)=>{if(x===null||x===undefined||x==='')return null;const n=Number(x);return Number.isFinite(n)&&n>=0?n:null;};
  const coordinate=(x:unknown,limit:number)=>{if(x===null||x===undefined||x==='')return null;const n=Number(x);return Number.isFinite(n)&&Math.abs(n)<=limit?n:null;};
  const type=typeof p.property_type==='string'&&p.property_type.trim()?p.property_type.trim().toLowerCase():null;
  return {listing_id:String(p.listing_id),price:p.currency&&p.currency!=='SGD'?null:num(p.price),bedrooms:num(p.bedrooms),floor_area_sqft:num(p.floor_area_sqft),
    property_type:type,district_code:p.district_code==null||p.district_code===''?null:String(p.district_code),latitude:coordinate(p.latitude,90),longitude:coordinate(p.longitude,180)};
}
