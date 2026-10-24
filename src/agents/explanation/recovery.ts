import type {Requirements,RecommendationResponse} from '../../contracts/recommendation-v1.js';
import type {ExplanationBundle,ExplanationOutput,ExplanationFact} from './contracts.js';
import {displayedCandidates} from '../../domain/selection.js';
import {validateExplanation} from './validation.js';

/** A failed writer never becomes a factual source. Compose a short, independently
 * validated answer from accepted facts only; keep the original failure in storage. */
export function recoverExplanation(bundle:ExplanationBundle,req:Requirements,rec:RecommendationResponse):ExplanationOutput{
  const english=!/[\u3400-\u9fff]/.test(bundle.question)&&/[a-z]{2}/i.test(bundle.question),g=bundle.decision_support!;
  const facts=new Map(bundle.facts.map(f=>[f.ref,f])),rows=displayedCandidates(rec);
  const paragraphs:ExplanationOutput['paragraphs']=[];
  if(!g.target_listing_ids.length)return validateExplanation({version:'2.0',focus_listing_ids:[],paragraphs:[{listing_id:null,text:english?'There are no homes to display for these conditions. Which condition would you like to adjust?':'这些条件下暂时没有可展示的房源，您愿意先调整哪项要求？',support_refs:['results:empty']}]},bundle);
  paragraphs.push({listing_id:null,text:english?'These details may help you choose between the homes:':'以下信息可能帮助您更好地选择这几套房子：',support_refs:['guide:purpose']});
  for(const id of g.target_listing_ids){
    const profile=g.profiles.find(p=>p.listing_id===id)!,row=rows.find(r=>r.listing.listing_id===id)!;
    const refs=new Set<string>(profile.core_refs),parts:string[]=[];
    const price=profile.core_refs?.map(r=>facts.get(r)!).find(f=>f.ref===`price:${id}`||f.ref.endsWith(':price'));
    const area=profile.core_refs?.map(r=>facts.get(r)!).find(f=>f.ref===`area:${id}`||f.ref.endsWith(':floor_area_sqft'));
    const highlights=profile.highlight_refs.map(r=>facts.get(r)!);
    const relative=(field:string)=>{
      const f=highlights.find(f=>f.cohort?.field===field);if(!f)return '';
      refs.add(f.ref);const c=f.cohort!,scope=c.complete?(english?'in this group':'在这几套里'):(english?'among homes with available data':'在有资料的房源里');
      const position=c.clustered?(english?'similar':'相差不大'):({lowest:english?'the lowest':'最低',tied_lowest:english?'joint lowest':'并列最低',highest:english?'the highest':'最高',tied_highest:english?'joint highest':'并列最高',lower:english?'on the lower side':'偏低',higher:english?'on the higher side':'偏高',middle:english?'in the middle':'居中',similar:english?'similar':'相差不大'})[c.position];
      const words=field==='floor_area_sqft'?position.replace('低','小').replace('高','大'):position;
      return english?`, ${words} ${scope}`:`，${scope}${words}`;
    };
    const pricePosition=highlights.find(f=>f.cohort?.field==='price')?.cohort;
    const areaPosition=highlights.find(f=>f.cohort?.field==='floor_area_sqft')?.cohort;
    const rent=price?(english?`At S$${price.numbers[0].toLocaleString('en-SG')} a month${relative('price')}`:`月租${price.numbers[0].toLocaleString('en-SG')}新币${relative('price')}`)+
      (pricePosition&&!pricePosition.clustered&&['lowest','tied_lowest','lower'].includes(pricePosition.position)?(english?', leaving more of your budget for other needs.':'，能少占一些预算。'):(english?'.':'。')):'';
    const space=area?(english?`The listed area is ${area.numbers[0]} sq ft${relative('floor_area_sqft')}`:`挂牌面积${area.numbers[0]}平方英尺${relative('floor_area_sqft')}`)+
      (areaPosition&&!areaPosition.clustered?(english?', worth comparing with the layout and the space you need.':'，可以结合格局看看是否够用。'):(english?'.':'。')):'';
    const areaFirst=areaPosition&&!areaPosition.clustered&&['highest','tied_highest'].includes(areaPosition.position);
    parts.push(...(areaFirst?[space,rent]:[rent,space]).filter(Boolean));
    const describedDistances=new Set<string>();
    for(const r of row.criterion_results){
      const c=req.criteria.find(c=>c.criterion_id===r.criterion_id);
      if(c?.field!=='place_proximity'||r.status==='unknown'||typeof r.observed_value!=='number')continue;
      const name=c.target?.name==='CLEMENTI'?'金文泰':c.target?.name||c.target?.category||'目标地点',region=/^URA:/.test(c.target?.place_id||'');
      // Radius filtering and nearer ranking may cite the same measurement.
      // Retain both condition references, but describe that distance only once.
      // Different destinations, metrics, modes or values remain separate facts.
      refs.add(`condition:${id}:${r.criterion_id}`);
      const target=c.target?.place_id?['place',c.target.place_id]:c.target?.category?['category',c.target.category]:['name',c.target?.name];
      const measurement=JSON.stringify([target,c.distance_type,c.transport_mode,r.unit??c.unit,r.observed_value]);
      if(describedDistances.has(measurement))continue;
      describedDistances.add(measurement);
      parts.push(english?(region&&r.observed_value===0?`It is within ${name}.`:`It is about ${Math.round(r.observed_value)} metres in a straight line from ${region?'the boundary of ':''}${name}.`):region&&r.observed_value===0?`位于${name}区域内。`:`距${name}${region?'区域边界':''}直线约${Math.round(r.observed_value).toLocaleString('zh-CN')}米。`);
    }
    const describe=(f:ExplanationFact)=>{
      refs.add(f.ref);
      if(f.cohort){
        const c=f.cohort,low=['lowest','tied_lowest','lower'].includes(c.position),high=['highest','tied_highest','higher'].includes(c.position);
        const scope=c.complete?(english?'in this group':'在这组房源中'):(english?'among homes with available data':'在有资料的房源中');
        if(c.field==='rent_per_sqft')return english?`At about S$${c.value} per sq ft per month, the unit rent is ${low?'lower':'higher'} ${scope}; the same space ${low?'costs less':'costs more'} in rent.`:`摊下来每平方英尺月租约${c.value}新币，${scope}${low?'偏低，单看面积和月租更划算':'偏高，同样面积要付更多月租'}。`;
        const label=c.field==='price'?(english?'rent':'月租'):(english?'listed area':'面积');
        const position=c.clustered?(english?'similar':'比较接近'):c.position==='tied_lowest'?(english?'joint lowest':'并列最低'):c.position==='tied_highest'?(english?'joint highest':'并列最高'):c.position==='lowest'?(english?'lowest':'最低'):c.position==='highest'?(english?'highest':'最高'):low?(english?'lower':'偏低'):high?(english?'higher':'偏高'):(english?'in the middle':'居中');
        return english?`Its ${label} is ${position} ${scope}.`:`${scope}${label}${c.field==='floor_area_sqft'?position.replace('低','小').replace('高','大'):position}。`;
      }
      if(f.ref.includes(':nearby:'))return english?`${f.literals?.[0]} is about ${f.numbers[0]} metres away${f.distance_metric==='straight_line'?' in a straight line':' according to the property information'}; check the actual route.`:`附近有${f.literals?.[0]}，${f.distance_metric==='straight_line'?'直线':'资料显示'}约${f.numbers[0]}米，实际路线可以再了解。`;
      if(f.ref.endsWith(':bathrooms'))return english?`The information lists ${f.numbers[0]} bathrooms; this does not establish private use.`:`资料记录有${f.numbers[0]}个卫生间，是否专用还需确认。`;
      if(f.ref.startsWith('beds:')||f.ref.endsWith(':bedrooms'))return english?`The information lists ${f.numbers[0]} bedrooms.`:`资料记录有${f.numbers[0]}间卧室。`;
      if(f.ref.endsWith(':type'))return english?`The recorded property type is ${row.listing.property_type}.`:f.text.split('；')[0]+'。';
      if(f.ref.endsWith(':build_year'))return english?`The recorded construction year is ${f.numbers[0]}.`:`楼盘建成年份记录为${f.numbers[0]}年，可以再看看实际维护情况。`;
      if(f.ref.endsWith(':floor_level'))return english?'The property information includes a recorded floor level.':f.text;
      return '';
    };
    // A useful introduction should retain several concrete neighbourhood
    // choices, instead of falling back to the first (often build-year) field.
    const normalize=(s:string)=>s.toLowerCase().replace(/\b(?:mrt|station)\b/g,'').replace(/[^\p{L}\p{N}]/gu,'');
    const stationTargets=req.criteria.filter(c=>c.field==='place_proximity'&&/MRT|STATION/i.test(c.target?.name||'')).map(c=>normalize(c.target!.name!));
    const nearby=profile.extra_refs.filter(r=>r.includes(':nearby:')).map(r=>facts.get(r)!).filter(f=>!f.ref.endsWith(':MRT')||!stationTargets.includes(normalize(f.literals?.[0]||''))).slice(0,3);
    for(const f of nearby)refs.add(f.ref);
    if(nearby.length){
      const places=nearby.map(f=>`${f.literals?.[0]}${english?` about ${f.numbers[0]} metres${f.distance_metric==='straight_line'?' in a straight line':' in the property information'}`:`${f.distance_metric==='straight_line'?'直线':'资料里写'}约${f.numbers[0]}米`}`);
      const hasTransport=nearby.some(f=>/:nearby:(MRT|BusStop|BusStation|PublicTransport)$/.test(f.ref));
      const hasShopping=nearby.some(f=>/:nearby:(Supermarket|Market|Mall|FoodCourt)$/.test(f.ref));
      parts.push(english?`Nearby options include ${places.join('; ')}. ${hasShopping?'These give you more options for groceries or meals. ':''}${hasTransport?'They can help you consider public transport; check the actual route.':''}`:`附近有${places.join('、')}。${hasShopping?'采购或用餐多几个选择；':''}${hasTransport?'选公共交通时可以参考，':''}实际路线可以再看看。`);
    }
    if(profile.additional_refs.length&&!profile.additional_refs.some(r=>refs.has(r))){
      const extra=profile.additional_refs.find(r=>!r.endsWith(':area-unit')&&!r.includes(':nearby:'));
      if(extra){const sentence=describe(facts.get(extra)!);if(sentence)parts.push(sentence);}
    }else if(!nearby.length){
      const extra=profile.extra_refs.find(r=>r.endsWith(':bathrooms'))||profile.extra_refs.find(r=>r.endsWith(':type'));
      if(extra&&!refs.has(extra)){const sentence=describe(facts.get(extra)!);if(sentence)parts.push(sentence);}
    }
    const unknown=row.criterion_results.filter(r=>r.status==='unknown').map(r=>{
      const c=req.criteria.find(c=>c.criterion_id===r.criterion_id);if(!c)return null;
      const label=c.field==='place_proximity'?`到${c.target?.name||'目标地点'}的距离`:({bedrooms:'卧室数量',bathrooms:'卫生间数量',floor_area_sqft:'面积',amenities:'设施情况',price:'月租'} as Record<string,string>)[c.field]||'部分要求';
      refs.add(`condition:${id}:${r.criterion_id}`);return label;
    }).filter(Boolean);
    for(const ref of profile.caution_refs)refs.add(ref);
    if(unknown.length)parts.push(english?'Some requested conditions still lack data, so please confirm them before deciding.':`${[...new Set(unknown)].join('、')}还缺少资料，需要确认后再决定。`);
    else if(profile.caution_refs.length)parts.push(english?'Some information is incomplete or conflicting; please check it with the agent.':'部分资料还不完整或有出入，需要向经纪人确认。');
    if(!refs.size){refs.add(`listing:${id}`);parts.push(english?'There is limited information for this home; ask the agent for details.':'这套房源目前资料较少，可以先向经纪人进一步了解。');}
    paragraphs.push({listing_id:id,text:(english?`${profile.position}. ${profile.title}: `:`第${profile.position}套 ${profile.title}：`)+parts.join(english?' ':''),support_refs:[...refs]});
  }
  paragraphs.push({listing_id:null,text:english?'Please confirm current rent and availability with the agent.':'当前租金和是否仍可租，请联系经纪人确认。',support_refs:['availability:current']});
  if(g.explain_order){paragraphs.at(-1)!.support_refs.push(...bundle.required_refs.filter(r=>r.startsWith('ranking:')));paragraphs.at(-1)!.text+=(english?' The displayed recommendation order is preserved.':'这里保持推荐时的顺序。')+(bundle.ranking.behavior_features.length?(english?' That order also uses the available browsing-interest signals.':'推荐顺序也参考了已有浏览兴趣线索。'):'');}
  return validateExplanation({version:'2.0',focus_listing_ids:g.target_listing_ids,paragraphs},bundle);
}
