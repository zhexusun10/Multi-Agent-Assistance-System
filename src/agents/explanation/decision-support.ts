import type {RecommendationResponse,Requirements} from '../../contracts/recommendation-v1.js';
import {displayedCandidates} from '../../domain/selection.js';
import type {ExplanationBundle,ExplanationFact} from './contracts.js';
import type {ExplanationEnrichment} from './enrichment.js';

const categories:Record<string,string>={MRT:'MRT站',BusStop:'公交站',BusStation:'公交站',PublicTransport:'公共交通站点',Supermarket:'超市',Market:'市场',FoodCourt:'食阁',Mall:'商场',Park:'公园',Clinic:'诊所',Hospital:'医院',Library:'图书馆',School:'学校'};
const priority=Object.keys(categories);
const typeNames:Record<string,string>={condominium:'公寓',apartment:'普通公寓','hdb flat':'组屋','executive condominium':'执行共管公寓','landed house':'有地住宅'};
const positive=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v)&&v>0;
const safeText=(v:unknown)=>typeof v==='string'&&v.trim()&&v.length<=180&&!/[\r\n\x00-\x1f]/.test(v)?v.trim():null;
const round=(v:number)=>Number(v.toFixed(2));

export function explanationTargets(bundle:ExplanationBundle){
  const selected=new Set<string>(),q=bundle.question;
  for(const l of bundle.listings)if(q.includes(l.title)||q.includes(l.listing_id))selected.add(l.listing_id);
  const ordinal:Record<string,number>={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8};
  for(const m of q.matchAll(/第\s*([一二三四五六七八\d]+)\s*套|(?:property|listing)\s*#?\s*(\d{1,2})\b/gi)){
    const n=ordinal[m[1]]??Number(m[1]||m[2]);if(bundle.listings[n-1])selected.add(bundle.listings[n-1].listing_id);
  }
  const first=q.match(/前\s*([两二三四五六七八\d]+)\s*套|first\s+(two|three|\d+)\s+(?:homes|properties|listings)/i);
  if(first){const words:Record<string,number>={two:2,three:3,...ordinal},n=words[first[1]||first[2]]??Number(first[1]||first[2]);for(const l of bundle.listings.slice(0,n))selected.add(l.listing_id);}
  return bundle.listings.filter(l=>!selected.size||selected.has(l.listing_id)).map(l=>l.listing_id);
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
    if(p){addExtra('price','挂牌记录月租',p.price,'新币/月');addExtra('bedrooms','记录卧室数量',p.bedrooms,'间');addExtra('bathrooms','卫生间数量',p.bathrooms,'个');addExtra('floor_area_sqft','挂牌面积',p.floor_area_sqft);}
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
      const metric=n.metric==='straight_line'?'直线距离':'挂牌标注距离',distance=round(n.distance_m),label=categories[n.category];
      const meaning=['MRT','BusStop','BusStation','PublicTransport'].includes(n.category)?'公共交通选择的参考，实际路线与班次未核实。':
        ['Supermarket','Market','FoodCourt','Mall'].includes(n.category)?'采购或用餐多一个选择，实际路线与营业未核实。':n.category==='Park'?'户外活动地点可考虑，实际入口与路线未核实。':'生活配套参考，实际路线与服务未核实。';
      extra_refs.push(add(`profile:${id}:nearby:${n.category}`,'listing',`已收录的${label}：${n.name}，${metric}约${distance}米。${meaning}`,[id],sourceRefs,[distance,distance/1000]));
      bundle.facts.at(-1)!.literals=[n.name];
    }
    for(const r of row.criterion_results){
      const c=req.criteria.find(c=>c.criterion_id===r.criterion_id);
      if(!c)continue;const ref=`condition:${id}:${r.criterion_id}`;
      if((r.status==='unknown'||r.status==='fail')&&c.strength==='hard')caution_refs.push(ref);
      else if(r.status!=='unknown'&&c.field==='place_proximity')known(ref);
    }
    if(!known_refs.length&&!extra_refs.length)caution_refs.push(add(`profile:${id}:sparse`,'limitation','可用资料较少，不能为此房源编造优势；直接说明目前缺少足够资料作具体比较。',[id]));
    const additional_refs=extra_refs.filter(ref=>{
      const field=ref.split(':')[2];
      if(field==='nearby'){
        const category=ref.split(':')[3];return !req.criteria.some(c=>c.field==='place_proximity'&&(c.target?.category===category||c.target?.category==='PublicTransport'&&['MRT','BusStop','BusStation','PublicTransport'].includes(category)));
      }
      const actual=field==='area-unit'?'floor_area_sqft':field==='type'?'property_type':field;
      return !req.criteria.some(c=>c.field===actual);
    });
    return {listing_id:id,position:index+1,title:row.listing.title,known_refs,extra_refs,additional_refs,highlight_refs,caution_refs};
  });
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
      const text=field==='price'?`相较本轮${peer}的记录月租${y.value}新币，本套${x.value}新币，每月少${difference}新币。可描述为月租较低或预算余量较大；不等于更划算、总费用更低或实际排名原因。`:
        `相较本轮${peer}的挂牌面积${y.value}平方英尺，本套挂牌面积${x.value}平方英尺，多${difference}平方英尺。可描述为挂牌面积较大；挂牌范围、整租或单间口径需确认，不能据此保证卧室更大、居住更舒适或使用人数。`;
      const ref=add(`advantage:${x.id}:${field}`,'comparison',text,[x.id,y.id],[],[x.value,y.value,difference,...(field==='floor_area_sqft'?[x.value/10.763910416709722,y.value/10.763910416709722,difference/10.763910416709722]:[])],[x.ref,y.ref]);
      profiles.find(p=>p.listing_id===x.id)!.highlight_refs.push(ref);
    }
  }
  const target_listing_ids=explanationTargets(bundle),explain_order=/排序|排名|排(?:在|到|前|后)|顺序|为什么第|\b(?:rank\w*|order\w*)\b/i.test(bundle.question);
  const focused=target_listing_ids.length!==rows.length||explain_order||/只(?:问|看|说|谈|比较)|仅(?:问|看|比较)|\bonly\b/i.test(bundle.question);
  bundle.decision_support={mode:focused?'focused':'guide',target_listing_ids,explain_order,profiles};
  add('guide:purpose','limitation','向用户逐套介绍本轮房源的具体特点和有依据的相对优势，补充用户未提出的已知信息，帮助选择；不是重排或保证每套都满足全部条件。',[]);
  bundle.required_refs=rows.length?['availability:current',...(explain_order?['ranking:method',...(bundle.ranking.behavior_features.length?['ranking:behavior']:[])]:[])]:['results:empty'];
  return bundle;
}
