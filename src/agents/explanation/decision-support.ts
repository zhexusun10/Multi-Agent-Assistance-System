import type {RecommendationResponse,Requirements} from '../../contracts/recommendation-v1.js';
import {displayedCandidates,RECOMMENDATION_DISPLAY_LIMIT} from '../../domain/selection.js';
import type {ExplanationBundle,ExplanationFact} from './contracts.js';
import type {ExplanationEnrichment} from './enrichment.js';
import {describeCohort,unitRentAssessment,type CohortObservation} from './cohort.js';

const categories:Record<string,string>={MRT:'MRT站',BusStop:'公交站',BusStation:'公交站',PublicTransport:'公共交通站点',Supermarket:'超市',Market:'市场',FoodCourt:'食阁',Mall:'商场',Park:'公园',Clinic:'诊所',Hospital:'医院',Library:'图书馆',School:'学校'};
const priority=Object.keys(categories);
const typeNames:Record<string,string>={condominium:'公寓',apartment:'普通公寓','hdb flat':'组屋','executive condominium':'执行共管公寓','landed house':'有地住宅'};
const positive=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v)&&v>0;
const safeText=(v:unknown)=>typeof v==='string'&&v.trim()&&v.length<=180&&!/[\r\n\x00-\x1f]/.test(v)?v.trim():null;
const round=(v:number)=>Number(v.toFixed(2));

export function explanationTargets(bundle:ExplanationBundle){
  const selected=new Set<string>(),q=bundle.question;
  for(const l of bundle.listings)if(q.includes(l.title)||q.includes(l.listing_id))selected.add(l.listing_id);
  const ordinal:Record<string,number>={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10};
  for(const m of q.matchAll(/第\s*([一二三四五六七八九十\d]+)\s*套|(?:property|listing)\s*#?\s*(\d{1,2})\b/gi)){
    const n=ordinal[m[1]]??Number(m[1]||m[2]);if(bundle.listings[n-1])selected.add(bundle.listings[n-1].listing_id);
  }
  const first=q.match(/前\s*([两二三四五六七八\d]+)\s*套|first\s+(two|three|\d+)\s+(?:homes|properties|listings)/i);
  if(first){const words:Record<string,number>={two:2,three:3,...ordinal},n=words[first[1]||first[2]]??Number(first[1]||first[2]);for(const l of bundle.listings.slice(0,n))selected.add(l.listing_id);}
  return (selected.size?bundle.listings.filter(l=>selected.has(l.listing_id)):bundle.listings.slice(0,RECOMMENDATION_DISPLAY_LIMIT)).map(l=>l.listing_id);
}

/** Adds decision facts to a COPY. Criterion outcomes and native ordering stay
 * authoritative even when the display-only detail API knows another field. */
export function addDecisionSupport(source:ExplanationBundle,req:Requirements,rec:RecommendationResponse,enrichment:ExplanationEnrichment[]=[]):ExplanationBundle{
  const bundle=structuredClone(source),rows=displayedCandidates(rec),ids=new Set(rows.map(r=>r.listing.listing_id));
  const supplements=new Map<string,ExplanationEnrichment>();
  for(const e of enrichment){
    if(!ids.has(e.listing_id)||supplements.has(e.listing_id)||e.dataset!==rec.state.coverage.dataset_version)throw Error('EXPLANATION_ENRICHMENT_IDENTITY');
    supplements.set(e.listing_id,e);
  }
  const add=(ref:string,kind:ExplanationFact['kind'],text:string,listing_ids:string[],source_refs:string[]=[],numbers:number[]=[],derived_from?:string[])=>{
    bundle.facts.push({ref,kind,text,listing_ids,source_refs,numbers,...(derived_from?{derived_from}:{})});return ref;
  };
  const profiles=rows.map((row,index)=>{
    const id=row.listing.listing_id,e=supplements.get(id),p=e?.sections.details==='available'?e.details:null,sourceRefs=e?[e.source_ref]:[];
    const known_refs:string[]=[],extra_refs:string[]=[],highlight_refs:string[]=[],caution_refs:string[]=[];
    const known=(ref:string)=>{if(bundle.facts.some(f=>f.ref===ref))known_refs.push(ref);};
    known(`price:${id}`);known(`beds:${id}`);known(`area:${id}`);
    const addExtra=(field:string,label:string,value:unknown,unit='')=>{
      const canonical=(row.listing as Record<string,unknown>)[field];
      // A conflicting reread is never substituted for the accepted result.
      if(canonical!==undefined&&canonical!==null&&canonical!==0&&value!==null&&value!==undefined&&canonical!==value){
        caution_refs.push(add(`profile:${id}:conflict:${field}`,'limitation',`${label}的补充记录与本轮推荐记录不同；解释保持推荐原值，提示该项需联系确认。`,[id],sourceRefs));return;
      }
      if((field==='price'||field==='bedrooms'||field==='floor_area_sqft')&&positive(canonical))return;
      if((field==='price'||field==='bedrooms'||field==='bathrooms'||field==='floor_area_sqft')&&!positive(value))return;
      if(typeof value!=='number'&&!safeText(value))return;
      const text=field==='bathrooms'?`挂牌记录有${value}个卫生间；数量不证明独立或专用使用权，也不改变推荐阶段尚未验证的条件。`:
        field==='floor_area_sqft'?`挂牌面积${value}平方英尺，约${round(Number(value)/10.763910416709722)}平方米；这是挂牌口径，不保证为单独卧室面积。`:
        `${label}：${value}${unit}。`;
      const numbers=typeof value==='number'?[value,...(field==='floor_area_sqft'?[value/10.763910416709722]:[])]:[];
      extra_refs.push(add(`profile:${id}:${field}`,'listing',text,[id],sourceRefs,numbers));
    };
    if(p){addExtra('price','记录月租',p.price,'新币/月');addExtra('bedrooms','卧室数量',p.bedrooms,'间');addExtra('bathrooms','卫生间数量',p.bathrooms,'个');addExtra('floor_area_sqft','房源面积',p.floor_area_sqft);addExtra('floor_level','楼层',p.floor_level);addExtra('build_year','建成年份',p.build_year,'年');}
    const property=row.listing.property_type||safeText(p?.property_type);
    if(property){const label=typeNames[property.toLowerCase()]||property;extra_refs.push(add(`profile:${id}:type`,'listing',`房屋类型记录为${label}；类型不证明室内设施。`,[id],sourceRefs));}
    if(positive(row.listing.floor_area_sqft)){
      const sqft=row.listing.floor_area_sqft;
      extra_refs.push(add(`profile:${id}:area-unit`,'listing',`挂牌面积${sqft}平方英尺，约${round(sqft/10.763910416709722)}平方米。这是挂牌口径，不保证为单独卧室面积。`,[id],[],[sqft,sqft/10.763910416709722],[`area:${id}`]));
    }
    // Select one known place per category; the graph is a sampled catalogue,
    // so this is the nearest RECORDED place, never a claim of exhaustive search.
    const nearby=e?.sections.nearby==='available'?e.nearby:[];
    const chosen=priority.flatMap(category=>{
      const points=nearby.filter(n=>n.category===category&&Number.isFinite(n.distance_m)&&n.distance_m>=0&&n.distance_m<=3000).sort((a,b)=>a.distance_m-b.distance_m);
      return points.length?[points[0]]:[];
    }).slice(0,6);
    // A platform MRT annotation may also be available from SQL without an edge.
    if(!chosen.some(n=>n.category==='MRT')&&p&&safeText(p.nearest_mrt)&&positive(p.mrt_distance_m))chosen.unshift({id:'platform:mrt',name:safeText(p.nearest_mrt)!,category:'MRT',distance_m:p.mrt_distance_m,metric:'platform_reported',walking_mins:null,source_url:null,source:'PropertyGuru 挂牌标注',computed_at:null,radius_m:null,route_verified:false});
    for(const n of chosen.slice(0,6)){
      if(!safeText(n.name))continue;
      const metric=n.metric==='straight_line'?'直线约':'资料里写的距离约',distance=round(n.distance_m),label=categories[n.category];
      const meaning=['MRT','BusStop','BusStation','PublicTransport'].includes(n.category)?'公共交通选择的参考，实际路线与班次未核实。':
        ['Supermarket','Market','FoodCourt','Mall'].includes(n.category)?'采购或用餐多一个选择，实际路线与营业未核实。':n.category==='Park'?'户外活动地点可考虑，实际入口与路线未核实。':'生活配套参考，实际路线与服务未核实。';
      extra_refs.push(add(`profile:${id}:nearby:${n.category}`,'listing',`附近${label}有${n.name}，${metric}${distance}米。${meaning}`,[id],sourceRefs,[distance,distance/1000]));
      bundle.facts.at(-1)!.literals=[n.name];
      bundle.facts.at(-1)!.distance_metric=n.metric;
    }
    for(const r of row.criterion_results){
      const c=req.criteria.find(c=>c.criterion_id===r.criterion_id);
      if(!c)continue;const ref=`condition:${id}:${r.criterion_id}`;
      if((r.status==='unknown'||r.status==='fail')&&c.strength==='hard')caution_refs.push(ref);
      else if(r.status!=='unknown'&&c.field==='place_proximity')known(ref);
    }
    if(!known_refs.length&&!extra_refs.length)caution_refs.push(add(`profile:${id}:sparse`,'limitation','可用资料较少，不能为此房源编造优势；直接说明目前缺少足够资料作具体比较。',[id]));
    // Additional information is a semantic field, not a particular duplicate
    // reference. The original area observation is as valid as its unit-conversion
    // supplement when the user never requested area.
    const knownFields:Record<string,string>={price:'price',beds:'bedrooms',area:'floor_area_sqft'};
    const additional_refs=known_refs.filter(ref=>knownFields[ref.split(':')[0]]&&!req.criteria.some(c=>c.field===knownFields[ref.split(':')[0]])).concat(extra_refs.filter(ref=>{
      const field=ref.split(':')[2];
      if(field==='nearby'){
        const category=ref.split(':')[3],requestedCategory=category==='MRT'?'MRTStation':category;return !req.criteria.some(c=>c.field==='place_proximity'&&(c.target?.category===requestedCategory||c.target?.category==='PublicTransport'&&['MRT','BusStop','BusStation','PublicTransport'].includes(category)));
      }
      const actual=field==='area-unit'?'floor_area_sqft':field==='type'?'property_type':field;
      return !req.criteria.some(c=>c.field===actual);
    }));
    const core_refs=['price','area'].flatMap(field=>{const ref=`${field}:${id}`,fallback=`profile:${id}:${field==='price'?'price':'floor_area_sqft'}`;return bundle.facts.some(f=>f.ref===ref)?[ref]:bundle.facts.some(f=>f.ref===fallback)?[fallback]:[];});
    return {listing_id:id,position:index+1,title:row.listing.title,known_refs,extra_refs,additional_refs,highlight_refs,caution_refs,core_refs};
  });
  // Ratios remain private arithmetic until a useful, same-scope comparison
  // exists. Never feed the writer an isolated unit-rent number.
  const unitObservations:CohortObservation[]=[],unitSources=new Map<string,string[]>();
  for(const profile of profiles){
    const priceRef=profile.core_refs.find(r=>r===`price:${profile.listing_id}`||r.endsWith(':price'));
    const areaRef=profile.core_refs.find(r=>r===`area:${profile.listing_id}`||r.endsWith(':floor_area_sqft'));
    const price=bundle.facts.find(f=>f.ref===priceRef)?.numbers[0],area=bundle.facts.find(f=>f.ref===areaRef)?.numbers[0];
    if(!positive(price)||!positive(area)||profile.caution_refs.some(r=>/conflict:(price|floor_area_sqft)$/.test(r)))continue;
    unitSources.set(profile.listing_id,[priceRef!,areaRef!]);
    unitObservations.push({listing_id:profile.listing_id,value:round(price/area),ref:priceRef!});
  }
  const observations=(field:'price'|'floor_area_sqft')=>profiles.flatMap(p=>{
    const ref=p.core_refs.find(r=>r===`${field==='price'?'price':'area'}:${p.listing_id}`||r.endsWith(':'+field));
    const value=bundle.facts.find(f=>f.ref===ref)?.numbers[0];
    return ref&&positive(value)?[{listing_id:p.listing_id,value,ref}]:[];
  });
  const groups=[{name:'recommended' as const,ids:rows.filter(r=>rec.recommendations.includes(r)).map(r=>r.listing.listing_id)},
    {name:'unverified' as const,ids:rows.filter(r=>rec.unverified_candidates.includes(r)).map(r=>r.listing.listing_id)}];
  const positions={lowest:'最低',highest:'最高',tied_lowest:'并列最低',tied_highest:'并列最高',lower:'偏低',middle:'中等',higher:'偏高',similar:'基本相同'};
  // Compare distances only to the SAME destination and using the SAME metric.
  // The hard radius and softer nearer preference share one observation, not
  // two editorial topics. This is context for the writer, never another ranker.
  const destinations=new Map<string,typeof req.criteria>();
  for(const c of req.criteria.filter(c=>c.field==='place_proximity')){
    const target=c.target?.place_id?['place',c.target.place_id]:c.target?.category?['category',c.target.category]:['name',c.target?.name];
    const key=JSON.stringify([target,c.distance_type,c.transport_mode,c.unit]);
    destinations.set(key,[...(destinations.get(key)||[]),c]);
  }
  const distancePositions={lowest:'最近',highest:'最远',tied_lowest:'并列最近',tied_highest:'并列最远',lower:'相对较近',middle:'居中',higher:'相对较远',similar:'距离相同'};
  for(const [key,criteria] of destinations)for(const group of groups){
    const sources=new Map<string,string[]>();
    const values=rows.filter(r=>group.ids.includes(r.listing.listing_id)).flatMap(row=>{
      const known=row.criterion_results.filter(r=>criteria.some(c=>c.criterion_id===r.criterion_id)&&r.status!=='unknown'&&typeof r.observed_value==='number'&&Number.isFinite(r.observed_value)&&r.observed_value>=0);
      if(!known.length||new Set(known.map(r=>r.observed_value)).size!==1)return [];
      const refs=known.map(r=>`condition:${row.listing.listing_id}:${r.criterion_id}`);sources.set(row.listing.listing_id,refs);
      return [{listing_id:row.listing.listing_id,value:known[0].observed_value as number,ref:refs[0]}];
    });
    const c=criteria[0],name=c.target?.name||c.target?.category||c.target?.place_id||'目标地点',region=/^URA:/.test(c.target?.place_id||'');
    for(const value of values){
      const summary=describeCohort(value.listing_id,group.ids,values,{allowZero:true});if(!summary)continue;
      const ref=add(`cohort:${value.listing_id}:proximity:${c.criterion_id}`,'comparison',
        `到同一目的地${name}${region?'区域（区内记0，区外到最近区域边界）':''}的${c.distance_type==='straight_line'?'直线':c.distance_type}距离为${Math.round(value.value)}米，在${summary.complete?'本组全部房源':'本组有该距离资料的房源'}中${distancePositions[summary.position]}。${summary.clustered?'距离整体接近，不夸大差异。':''}这是同口径数值比较，不是最近地铁站的断言；不受软偏好达标标记影响，不说明实际通勤便利或改变推荐次序。`,
        values.map(v=>v.listing_id),[],[value.value,summary.minimum,summary.maximum,summary.median,summary.scope_count,summary.known_count],values.flatMap(v=>sources.get(v.listing_id)!));
      const fact=bundle.facts.at(-1)!;fact.subject_listing_id=value.listing_id;fact.cohort={...summary,field:'place_proximity',group:group.name};
      fact.proximity={target_name:name,target_key:key,distance_type:c.distance_type!,...(c.transport_mode?{transport_mode:c.transport_mode}:{})};
      profiles.find(p=>p.listing_id===value.listing_id)!.highlight_refs.push(ref);
    }
  }
  for(const group of groups)for(const field of ['price','floor_area_sqft','rent_per_sqft'] as const){
    const values=(field==='rent_per_sqft'?unitObservations:observations(field)).filter(x=>group.ids.includes(x.listing_id));
    // Relative unit-rent judgements require matching, explicitly recorded rental
    // scopes; an unexplained mixture of rooms and whole units is not comparable.
    if(field==='rent_per_sqft'){
      const scopes=group.ids.map(id=>{const e=supplements.get(id);return e?.sections.details==='available'?e.details?.rental_scope:null;});
      if(!scopes.length||!scopes.every(s=>['room','whole_unit'].includes(String(s))&&s===scopes[0]))continue;
    }
    for(const value of values){
      const summary=describeCohort(value.listing_id,group.ids,values);if(!summary)continue;
      const unitAssessment=field==='rent_per_sqft'?unitRentAssessment(summary):null;
      if(field==='rent_per_sqft'&&!unitAssessment)continue;
      const label=field==='price'?'月租':field==='floor_area_sqft'?'房源面积':'每平方英尺月租';
      const scope=summary.complete?`本轮${group.name==='recommended'?'推荐':'资料尚待确认'}的${summary.scope_count}套`:`本轮${summary.scope_count}套中有${label}资料的${summary.known_count}套`;
      const position=field==='floor_area_sqft'?positions[summary.position].replace('最低','最小').replace('最高','最大').replace('偏低','偏小').replace('偏高','偏大'):positions[summary.position];
      const cluster=summary.clustered?'这些数值整体很接近，不夸大细小差别。':'';
      const standout=summary.stands_out?'这项差异较明显，可优先作为介绍重点。':'';
      const profile=profiles.find(p=>p.listing_id===value.listing_id)!;
      const unitMeaning=field==='rent_per_sqft'?(unitAssessment==='higher'?'摊到同样的面积，这套月租相对高；单看空间和月租不太划算，可以结合其他特点判断是否值得。':'摊到同样的面积，这套月租相对低；单看空间和月租更划算，能用较少租金换到相同面积。'):'';
      const ref=add(`cohort:${value.listing_id}:${field}`,'comparison',`${profile.title}的${label}为${value.value}${field==='rent_per_sqft'?'新币/平方英尺/月':''}，在${scope}中${position}。${cluster}${standout}${summary.complete?'':'其他房源该项未知，不能称整组最低/最高。'}${unitMeaning}`,values.map(x=>x.listing_id),[],[value.value,summary.minimum,summary.maximum,summary.median,summary.scope_count,summary.known_count],field==='rent_per_sqft'?values.flatMap(x=>unitSources.get(x.listing_id)!):values.map(x=>x.ref));
      const fact=bundle.facts.at(-1)!;fact.subject_listing_id=value.listing_id;fact.cohort={...summary,field,group:group.name};
      profile.highlight_refs.push(ref);
      if(field==='rent_per_sqft')profile.additional_refs.push(ref);
    }
  }
  // Auditable relative advantages within THIS display set. They describe a
  // buyer's trade-offs, not a new score, ranking or inferred user preference.
  for(const field of ['price','floor_area_sqft'] as const){
    const values=rows.flatMap(row=>{
      const value=row.listing[field],id=row.listing.listing_id;
      if(positive(value))return [{id,value,ref:field==='price'?`price:${id}`:`area:${id}`}];
      if(field==='floor_area_sqft'){
        const e=supplements.get(id),v=e?.sections.details==='available'?e.details?.floor_area_sqft:null,ref=`profile:${id}:floor_area_sqft`;
        if(positive(v)&&bundle.facts.some(f=>f.ref===ref))return [{id,value:v,ref}];
      }return [];
    });
    if(values.length<2)continue;
    for(const x of values){
      const others=values.filter(y=>field==='price'?y.value>x.value:y.value<x.value).sort((a,b)=>Math.abs(a.value-x.value)-Math.abs(b.value-x.value));
      if(!others.length)continue;const y=others[0],peer=rows.find(r=>r.listing.listing_id===y.id)!.listing.title,difference=Math.abs(x.value-y.value);
      const title=rows.find(r=>r.listing.listing_id===x.id)!.listing.title;
      const text=field==='price'?`${title}的记录月租为${x.value}新币，${peer}为${y.value}新币；${title}比${peer}每月少${difference}新币。可描述为月租较低或预算余量较大；不等于更划算、总费用更低或实际排名原因。`:
        `${title}的挂牌面积为${x.value}平方英尺，${peer}为${y.value}平方英尺；${title}比${peer}多${difference}平方英尺。可描述为挂牌面积较大；挂牌范围、整租或单间口径需确认，不能据此保证卧室更大、居住更舒适或使用人数。`;
      const ref=add(`advantage:${x.id}:${field}`,'comparison',text,[x.id,y.id],[],[x.value,y.value,difference,...(field==='floor_area_sqft'?[x.value/10.763910416709722,y.value/10.763910416709722,difference/10.763910416709722]:[])],[x.ref,y.ref]);
      bundle.facts.at(-1)!.subject_listing_id=x.id;
      // Pairwise comparisons remain available for a focused comparison, but
      // are no longer the default highlight fed to a general listing guide.
    }
  }
  const target_listing_ids=explanationTargets(bundle),explain_order=/排序|排名|排(?:在|到|前|后)|顺序|为什么第|\b(?:rank\w*|order\w*)\b/i.test(bundle.question);
  const focused=target_listing_ids.length!==Math.min(RECOMMENDATION_DISPLAY_LIMIT,rows.length)||explain_order||/只(?:问|看|说|谈|比较)|仅(?:问|看|比较)|比较前[两二2]套|对比前[两二2]套|\b(?:only|versus)\b/i.test(bundle.question);
  bundle.decision_support={mode:focused?'focused':'guide',target_listing_ids,explain_order,profiles};
  add('guide:purpose','limitation','向用户逐套介绍本轮房源的具体特点和有依据的相对优势，补充用户未提出的已知信息，帮助选择；不是重排或保证每套都满足全部条件。',[]);
  bundle.required_refs=rows.length?['availability:current',...(explain_order?['ranking:method',...(bundle.ranking.behavior_features.length?['ranking:behavior']:[])]:[])]:['results:empty'];
  return bundle;
}
