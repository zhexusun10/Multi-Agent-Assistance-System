import type {Criterion,RecommendationResponse,Requirements} from '../../contracts/recommendation-v1.js';
import {FIELD_RULES} from '../../contracts/recommendation-v1.js';
import {nativeResponseSchema} from '../../contracts/recommendation-native.js';
import {criterionText} from '../../domain/explanation.js';
import {displayedCandidates} from '../../domain/selection.js';
import type {ExplanationBundle,ExplanationFact} from './contracts.js';

const placeNames:Record<string,string>={'Nanyang Technological University':'南洋理工大学','National University of Singapore':'新加坡国立大学'};
const featureNames:Record<string,string>={property_type:'房型',bedrooms:'卧室数量',floor_area_sqft:'面积',geography:'位置'};
const numeric=(v:unknown):number[]=>typeof v==='number'&&Number.isFinite(v)?[v]:Array.isArray(v)?v.flatMap(numeric):[];
function dateLiterals(value:string):string[]{
  const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(value);if(!match)return [value];
  const [,year,month,day]=match,m=Number(month),d=Number(day),date=new Date(value+'T00:00:00Z');
  if(!Number.isFinite(date.getTime())||date.toISOString().slice(0,10)!==value)return [value];
  return [value,`${year}年${m}月${d}日`,`${year}年${month}月${day}日`,`${m}月${d}日`,
    ...[month,String(m)].flatMap(mm=>[day,String(d)].map(dd=>`${year}/${mm}/${dd}`)),
    ...['en-GB','en-US'].flatMap(locale=>(['long','short'] as const).map(format=>new Intl.DateTimeFormat(locale,{year:'numeric',month:format,day:'numeric',timeZone:'UTC'}).format(date)))];
}
export function makeExplanationBundle(req:Requirements,rec:RecommendationResponse,question:string,archive?:unknown):ExplanationBundle{
  const facts:ExplanationFact[]=[],required_refs:string[]=[];
  const add=(ref:string,kind:ExplanationFact['kind'],text:string,listing_ids:string[]=[],source_refs:string[]=[],numbers:number[]=[])=>{facts.push({ref,kind,text,listing_ids,source_refs,numbers});return ref;};
  const clean=(s:string)=>Object.entries(placeNames).reduce((t,[a,b])=>t.replaceAll(a,b),s);
  const desc=(c:Criterion)=>clean(criterionText(c)).replace(/，偏好权重 [\d.]+/,'，希望尽量满足').replace('SGD/month','新币/月').replace('count','间').replace('sqft','平方英尺');
  const nearer=(c:Criterion)=>c.field==='place_proximity'&&c.strength==='soft'&&c.distance_preference!=='bounded';
  const criterionNumbers=(c:Criterion)=>nearer(c)?[]:numeric(c.value);
  // A district/postcode/date is an exact string, never a measurable quantity.
  // Keep its full value for reference-scoped grounding instead of turning D05
  // into the numeric allowance 5 (which could incorrectly authorize a rent).
  const literalValues=(c:Criterion,value:unknown):string[]=>['string','date'].includes(FIELD_RULES[c.field]?.kind)
    ?(Array.isArray(value)?value:[value]).filter((v):v is string=>typeof v==='string'&&/\d/.test(v)).flatMap(v=>FIELD_RULES[c.field].kind==='date'?dateLiterals(v):[v]):[];
  const rows=displayedCandidates(rec),byId=new Map(req.criteria.map(c=>[c.criterion_id,c]));
  const rankedIds=rec.recommendations.map(r=>r.listing.listing_id);
  if(req.criteria.some(c=>c.field==='bathrooms')&&!req.criteria.some(c=>c.field==='amenities'&&c.value==='private_bathroom'))add('bathroom-count-not-privacy','limitation','本次卫浴要求仅为卫生间数量，不是专用或独立使用权。数量尚未确认时应说“卫生间数量待确认”，不能把它称为“独卫待确认”。1B1B不证明独卫，也不等于用户要求独卫。');
  const lists=rows.map((item,index)=>{
    const id=item.listing.listing_id,refs:string[]=[],unverified=rec.unverified_candidates.includes(item);
    refs.push(add('listing:'+id,'listing',`第${index+1}套：${item.listing.title}。${unverified?'部分必须条件缺少资料，还不能确认这套房是否全部符合用户要求；不属于本次入选排名，即使有已知距离，也只是补充展示。':'已提供资料满足本次必须条件，属于本次入选排名。'}`,[id],[],[index+1]));
    if(item.listing.price!==null)refs.push(add('price:'+id,'listing',`记录月租${item.listing.price}新币；现在价格还需联系确认。`,[id],[],[item.listing.price]));
    // Zero is a source placeholder in this dataset, never assert a zero-bedroom home.
    if(item.listing.bedrooms!==null&&item.listing.bedrooms>0)refs.push(add('beds:'+id,'listing',`卧室数${item.listing.bedrooms}。`,[id],[],[item.listing.bedrooms]));
    if(item.listing.floor_area_sqft&&item.listing.floor_area_sqft>0)refs.push(add('area:'+id,'listing',`室内面积${item.listing.floor_area_sqft}平方英尺。`,[id],[],[item.listing.floor_area_sqft]));
    for(const r of item.criterion_results){
      const c=byId.get(r.criterion_id);if(!c)throw Error('EXPLANATION_UNKNOWN_CRITERION');
      const observed=numeric(r.observed_value),distance=c.field==='place_proximity';
      let text=desc(c)+'；'+(r.status==='unknown'?'尚不能确认。':nearer(c)?'已用于比较离目的地的远近，没有另一达标半径。':r.status==='pass'?'符合。':c.strength==='hard'?'未达到这项必须要求。':'未达到这项偏好。');
      if(r.status!=='unknown'&&r.observed_value!==null){
        const value=distance&&typeof r.observed_value==='number'?`${Math.round(r.observed_value)}米（直线距离，不能推断步行时间）`:String(r.observed_value)+(r.unit==='SGD/month'?'新币/月':r.unit||'');
        text+='已知记录：'+value+'。';
      }
      if(r.status==='unknown')text+='资料不足不代表该设施不存在；不能据此说交通方便或条件已满足。';
      const nums=[...criterionNumbers(c),...observed];
      if(typeof c.value==='number'&&typeof r.observed_value==='number'){
        nums.push(Math.abs(c.value-r.observed_value));
        if(c.field==='price'&&c.operator==='lte')text+=`记录租金比这一上限${r.observed_value>c.value?'高':'低'}${Math.abs(c.value-r.observed_value)}新币。`;
      }
      if(distance)nums.push(...nums.map(n=>n/1000));
      refs.push(add('condition:'+id+':'+c.criterion_id,r.status==='unknown'?'limitation':'condition',text,[id],r.evidence_ids,nums));
      facts.at(-1)!.literals=[...new Set([...literalValues(c,c.value),...(r.status==='unknown'?[]:literalValues(c,r.observed_value))])];
    }
    return {listing_id:id,position:index+1,title:item.listing.title,group:unverified?'hard_unknown' as const:'hard_pass' as const,ranking_status:unverified?'not_ranked' as const:'ranked' as const,fact_refs:refs};
  });
  // Provide the user's complete requirements once, separately from per-home
  // observations. Merge only the same destination's radius + nearer preference;
  // the native distance scale is not another user-requested radius.
  const destination=(c:Criterion)=>c.target?.place_id||c.target?.category||c.target?.name;
  const sameDestination=(a:Criterion,b:Criterion)=>a.field==='place_proximity'&&b.field==='place_proximity'&&a.distance_type===b.distance_type&&a.transport_mode===b.transport_mode&&destination(a)===destination(b);
  const grouped=new Map<string,Criterion[]>();
  for(const c of req.criteria){
    const bounded=nearer(c)?req.criteria.find(x=>!nearer(x)&&sameDestination(c,x)):undefined;
    const key=bounded?.criterion_id||c.criterion_id;
    grouped.set(key,[...(grouped.get(key)||[]),c]);
  }
  const requirements=[...grouped.entries()].map(([id,criteria])=>{
    const primary=criteria.find(c=>!nearer(c))||criteria[0];
    const label=desc(primary)+(criteria.some(nearer)&&!nearer(primary)?'；在此范围内希望离目的地更近':'');
    const numbers=criteria.flatMap(criterionNumbers);if(primary.field==='place_proximity')numbers.push(...numbers.map(n=>n/1000));
    const ref=add('requirement:'+id,'requirement',label,[],[],numbers);
    facts.at(-1)!.literals=[...new Set(criteria.flatMap(c=>literalValues(c,c.value)))];
    return {ref,label,strength:primary.strength,comparison_only:nearer(primary),criterion_ids:criteria.map(c=>c.criterion_id),results:rows.map((row,index)=>({
      listing_id:row.listing.listing_id,
      position:index+1,title:row.listing.title,
      status:row.criterion_results.find(r=>r.criterion_id===primary.criterion_id)?.status||'unknown' as const,
      observed_value:row.criterion_results.find(r=>r.criterion_id===primary.criterion_id)?.observed_value??null,
      unit:primary.unit??null,
      fact_refs:criteria.map(c=>'condition:'+row.listing.listing_id+':'+c.criterion_id).filter(ref=>facts.some(f=>f.ref===ref)),
    }))};
  });
  const missing=requirements.flatMap(r=>{
    const unknown=r.results.filter(x=>x.status==='unknown');
    return unknown.length?[{requirement:r,unknown}]:[];
  });
  if(missing.length){
    const refs=missing.flatMap(x=>x.unknown.flatMap(r=>r.fact_refs));
    add('coverage:missing','limitation','逐项缺资料清单（各条件的房源集合不同，禁止合并成同一组）：'+missing.map(x=>x.requirement.label+'：仅'+x.unknown.map(r=>r.title).join('、')+'尚不能确认').join('；')+'。不在某项缺资料清单中的房源，不能被说成该项也无记录。',[...new Set(missing.flatMap(x=>x.unknown.map(r=>r.listing_id)))]);
    facts.at(-1)!.derived_from=refs;
  }
  let native;
  if(archive){
    native=nativeResponseSchema.parse((archive as any).response);
    if(native.request_id!==rec.request_id||native.conversation_id!==rec.conversation_id||native.requirements_version!==rec.requirements_version+1)throw Error('EXPLANATION_ARCHIVE_IDENTITY');
    // Native rationale is a list of criterion IDs. Translate it against accepted facts, never render raw IDs.
    for(const item of native.recommendations){
      if(!rows.some(r=>r.listing.listing_id===item.listing.listing_id))continue;
      const source=[...item.strengths,...item.trade_offs,...item.unknowns].filter(id=>byId.has(id));
      const matching=facts.filter(f=>source.some(id=>f.ref==='condition:'+item.listing.listing_id+':'+id));
      if(matching.length)add('native-rationale:'+item.listing.listing_id,'listing','推荐方给出的符合项、取舍项和待确认项已对应到本套房的逐项条件。',[item.listing.listing_id],matching.flatMap(f=>f.source_refs));
    }
  }
  const scores=rec.native?Object.values(rec.native.scores):[],active=new Set<string>(),behavior=new Set<string>();
  for(const score of scores){
    for(const c of score.contributions){
      if(c.unknown||c.similarity===null||c.weight<=0)continue;
      if(c.reference===null&&score.explicit_weight>0&&byId.get(c.feature)?.strength==='soft')active.add(c.feature);
      if(c.reference!==null&&score.implicit_weight>0&&featureNames[c.feature]&&!score.suppressed_features.includes(c.feature))behavior.add(c.feature);
    }
  }
  if(!rec.native)for(const row of rows)for(const b of row.score_breakdown)if(b.origin==='stated_preference'&&row.criterion_results.find(r=>r.criterion_id===b.criterion_id)?.status!=='unknown')active.add(b.criterion_id);
  const modes=[...new Set(scores.map(s=>s.ordering))],mode=rec.native?(modes.length===1?modes[0]:modes.length?'mixed':'unscored'):'baseline';
  const activeText=[...active].map(id=>desc(byId.get(id)!));
  const source=(archive as any)?.request?.feedback as {source?:string}[]|undefined;
  const sampleOnly=source?.length&&source.every(x=>x.source==='sample');
  let rankText=rows.length?(rec.recommendations.length?(rec.unverified_candidates.length?'部分房源的已有资料满足必须要求；另一些房源资料不完整，不能笼统声称所有房源都符合全部要求。':'这些房源的已有资料满足本次必须要求。'):'这几套房源有部分必须要求缺少资料，还不能确定全部符合用户要求。'):'本次没有可展示的房源，不能推断整个市场没有合适房源。';
  // Selection is already described by the requirement/result pairs. Keep this
  // fact about the actual order, so it does not prime repetitive disclaimers.
  if(activeText.length)rankText+='有资料可比较的排序偏好是：'+activeText.join('；')+'。';
  if(active.size===1&&!behavior.size)rankText+='在已评分且有资料可比的房源之间，这就是唯一实际生效的排序偏好；相同匹配程度时保持固定次序，不能据此说谁更优。';
  if(active.size>1)rankText+='这些偏好共同决定顺序，不能简化成只按其中一个排序。';
  const filterOnly=req.criteria.filter(c=>c.strength==='hard');
  if(filterOnly.length)rankText+='仅用于筛选、不用于奖励排名的条件：'+filterOnly.map(desc).join('；')+'。不能因为某套租金更低就把它追加为排序原因。';
  if(behavior.size)rankText+=`${activeText.length?'同时参考':'排序参考'}${sampleOnly?'本轮提供的浏览样例':'已有浏览记录'}中相似的${[...behavior].map(x=>featureNames[x]).join('、')}；只作为兴趣线索，不能说成用户已经明确要求。`;
  if(active.size&&behavior.size&&scores.every(s=>s.explicit_weight>s.implicit_weight))rankText+='明确说出的偏好占主要作用，浏览兴趣起辅助作用。';
  if(!active.size&&!behavior.size&&rows.length)rankText+='目前资料还不足以根据用户偏好判断哪套更合适，前后顺序仅供浏览；用这一句话说明即可，无须再说租金不是原因。';
  const rankedRows=rows.filter(r=>rec.native?.scores[r.listing.listing_id]);
  if(rankedRows.length>1&&rankedRows.every(r=>Math.abs(r.score-rankedRows[0].score)<1e-12))rankText+='当前展示的已评分房源排序依据没有拉开差距，沿用固定次序，不应称第一套明显更好。';
  const unscored=rec.native?.unscored_listing_ids||[];
  if(rankedIds.length)rankText+='入选排名的比较范围仅为：'+rec.recommendations.map(r=>r.listing.title).join('、')+'。';
  if(rec.unverified_candidates.length)rankText+='其余补充展示的房源未进入这组排名；它们可能仍有学校、超市等已知距离。已知资料可供用户比较，但不代表通过全部必须条件或参与排名；不能用其某一项距离更短来否定入选排名。';
  add('ranking:method','ranking',rankText,rows.map(r=>r.listing.listing_id),rec.native?[rec.native.exchange_ref]:[],[...active].flatMap(id=>{const c=byId.get(id)!;return c.field==='place_proximity'?[...criterionNumbers(c),...criterionNumbers(c).map(n=>n/1000)]:criterionNumbers(c);}));
  required_refs.push('ranking:method');
  if(behavior.size){add('ranking:behavior','ranking',`本轮确实还使用了${sampleOnly?'浏览样例':'已有浏览记录'}中的${[...behavior].map(x=>featureNames[x]).join('、')}作兴趣线索；解释排序时须简短提到，不能只讲明确偏好。`);required_refs.push('ranking:behavior');}
  if(rows.length){add('availability:current','availability','这些记录不能证明现在仍可租；当前可租情况和价格需向经纪人确认。');required_refs.push('availability:current');}
  if(!rows.length)add('results:empty','limitation',rec.status==='needs_clarification'?'需要先补充：'+rec.clarifications.map(x=>x.question).join('；'):'本次条件下没有找到能展示的选项；可以和用户讨论想调整哪项条件，不能自动放宽要求或声称全市场无房。');
  for(let a=0;a<rows.length;a++)for(let b=a+1;b<rows.length;b++){
    const x=rows[a],y=rows[b];
    if(x.listing.price!==null&&y.listing.price!==null){
      const ref=add(`compare:${a+1}:${b+1}`,'comparison',`第${a+1}套记录月租${x.listing.price}新币，第${b+1}套${y.listing.price}新币；后者比前者${y.listing.price>=x.listing.price?'高':'低'}${Math.abs(y.listing.price-x.listing.price)}新币；价格差异本身不代表排名原因。`,[x.listing.listing_id,y.listing.listing_id],[],[a+1,b+1,x.listing.price,y.listing.price,Math.abs(y.listing.price-x.listing.price)]);
      facts.find(f=>f.ref===ref)!.derived_from=[`price:${x.listing.listing_id}`,`price:${y.listing.listing_id}`,...req.criteria.filter(c=>c.field==='price').flatMap(c=>[x,y].filter(row=>row.criterion_results.find(r=>r.criterion_id===c.criterion_id)?.observed_value===row.listing.price).map(row=>`condition:${row.listing.listing_id}:${c.criterion_id}`))];
    }
    const sx=rec.native?.scores[x.listing.listing_id],sy=rec.native?.scores[y.listing.listing_id];
    if(sx&&sy&&rankedIds.includes(x.listing.listing_id)&&rankedIds.includes(y.listing.listing_id)){
      const factors=[...active].flatMap(id=>{
        const cx=sx.contributions.find(v=>v.feature===id&&v.reference===null&&!v.unknown&&v.similarity!==null),cy=sy.contributions.find(v=>v.feature===id&&v.reference===null&&!v.unknown&&v.similarity!==null);
        if(!cx||!cy)return [];
        const direction=cx.contribution===cy.contribution?'两套在此项对排名的贡献相同':cx.contribution>cy.contribution?`此项更有利于第${a+1}套`:`此项更有利于第${b+1}套`;
        return [desc(byId.get(id)!)+':'+direction];
      });
      if(factors.length)add(`compare-ranking:${a+1}:${b+1}`,'comparison',`仅比较本次入选排名：第${a+1}套${x.listing.title}在第${b+1}套${y.listing.title}之前。推荐方实际逐项贡献显示：${factors.join('；')}。各项允许取舍；不要求排前的房源在每一项都最好，不把这些比较扩展到未进入排名的补充房源。`,[x.listing.listing_id,y.listing.listing_id],rec.native?[rec.native.exchange_ref]:[],[a+1,b+1]);
    }
    const usedBehavior=[sx,sy].map(score=>score?.contributions.filter(c=>c.reference!==null&&!c.unknown&&c.similarity!==null&&c.weight>0&&featureNames[c.feature]&&!score.suppressed_features.includes(c.feature)).map(c=>c.feature)||[]);
    if(behavior.size&&sx&&sy&&usedBehavior.every(features=>features.length>0)&&sx.implicit_weight>0&&sy.implicit_weight>0&&sx.implicit_score!==null&&sy.implicit_score!==null){
      const matching=sx.implicit_score===sy.implicit_score?'两套的浏览兴趣整体匹配程度相同':sx.implicit_score>sy.implicit_score?`第${a+1}套${x.listing.title}整体上比第${b+1}套${y.listing.title}更贴近已使用的浏览兴趣线索`:`第${b+1}套${y.listing.title}整体上比第${a+1}套${x.listing.title}更贴近已使用的浏览兴趣线索`;
      const features=[...new Set(usedBehavior.flat())].map(key=>featureNames[key]).join('、');
      add(`compare-interest:${a+1}:${b+1}`,'comparison',`推荐方实际返回的浏览兴趣比较：${matching}。这是${features}等已参与因素的整体匹配结果；不代表每项特征都单独更相似，也不说明用户偏爱某个具体面积或房型值。不重新计算或改变原顺序。`,[x.listing.listing_id,y.listing.listing_id],rec.native?[rec.native.exchange_ref]:[],[a+1,b+1]);
    }
    for(const c of req.criteria.filter(c=>c.field==='place_proximity')){
      const rx=x.criterion_results.find(r=>r.criterion_id===c.criterion_id),ry=y.criterion_results.find(r=>r.criterion_id===c.criterion_id);
      if(!rx||!ry||rx.status==='unknown'||ry.status==='unknown'||typeof rx.observed_value!=='number'||typeof ry.observed_value!=='number')continue;
      const difference=Math.abs(rx.observed_value-ry.observed_value),roundedDifference=Math.abs(Math.round(rx.observed_value)-Math.round(ry.observed_value));
      const direction=rx.observed_value===ry.observed_value?'两套直线距离相同':rx.observed_value<ry.observed_value?`第${a+1}套更近，第${b+1}套更远`:`第${b+1}套更近，第${a+1}套更远`;
      const ref=add(`compare-distance:${a+1}:${b+1}:${c.criterion_id}`,'comparison',`到${clean(c.target?.name||c.target?.category||'该地点')}的直线距离：第${a+1}套${x.listing.title}为${rx.observed_value}米；第${b+1}套${y.listing.title}为${ry.observed_value}米。${direction}，距离相差${Number(difference.toFixed(2))}米。推荐次序不因这段比较改变；不能推断实际步行或通勤的差别。`,[x.listing.listing_id,y.listing.listing_id],[...rx.evidence_ids,...ry.evidence_ids],[a+1,b+1,rx.observed_value,ry.observed_value,difference,roundedDifference,difference/1000,roundedDifference/1000]);
      facts.find(f=>f.ref===ref)!.derived_from=[`condition:${x.listing.listing_id}:${c.criterion_id}`,`condition:${y.listing.listing_id}:${c.criterion_id}`];
    }
  }
  return {question,requirements,listings:lists,facts,required_refs,ranking:{mode,active_criteria:[...active],behavior_features:[...behavior],ranked_listing_ids:rankedIds,filter_only_criteria:filterOnly.map(c=>c.criterion_id),unscored_listing_ids:unscored}};
}
