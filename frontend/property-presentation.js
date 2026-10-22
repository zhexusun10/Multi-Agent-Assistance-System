const names={price:'月租',bedrooms:'卧室数量',bathrooms:'卫浴数量',floor_area_sqft:'室内面积',land_area_sqft:'土地面积',build_year:'建成年份',property_type:'房屋类型',district:'邮区',region_code:'区域',postal_code:'邮编',lease_months:'租期',furnishing:'家具',rental_scope:'租赁范围',commute_time:'通勤时间',move_in_date:'入住日期',current_availability:'当前是否可租'};
const amenities={private_bathroom:'独立卫浴',master_room:'主卧',common_room:'普通房',air_conditioning:'空调',cooking_allowed:'做饭规定',pet_policy:'宠物规定',no_live_in_landlord:'是否与房东同住',swimming_pool:'游泳池',gym:'健身房',balcony:'阳台',parking:'停车位',elevator:'电梯',wifi:'网络',washer:'洗衣机',refrigerator:'冰箱',security:'保安'};
const places={'National University of Singapore':'新加坡国立大学','Nanyang Technological University':'南洋理工大学'};
const finite=v=>typeof v==='number'&&Number.isFinite(v);
export const money=value=>finite(value)?'$'+value.toLocaleString('en-SG'):'租金待确认';
export const distance=value=>Math.round(value).toLocaleString('zh-CN')+' 米';
const targetName=c=>places[c.target?.name]||c.target?.name||c.target?.category||'目的地';
const placeKey=c=>JSON.stringify([c.target?.place_id||c.target?.name||c.target?.category,c.distance_type]);
const label=c=>c.field==='amenities'?(amenities[c.value]||String(c.value)):c.field==='place_proximity'?`到${targetName(c)}的距离${c.distance_preference==='bounded'?`（${{lte:'≤',gte:'≥',between:'范围'}[c.operator]||''}${Array.isArray(c.value)?c.value.map(distance).join('–'):distance(c.value)}）`:''}`:names[c.field]||c.field;

// Presentation only: preserve the recommendation's statuses and measured values.
export function matchSummary(candidate,criteria,formatCriterion){
  const rows=[],pending=new Set(),seenPlaces=new Set();
  const results=candidate.criterion_results.map(r=>({r,c:criteria.find(c=>c.criterion_id===r.criterion_id)})).filter(x=>x.c);
  results.sort((a,b)=>(a.c.strength==='soft')-(b.c.strength==='soft'));
  const boundedPlaces=new Set(results.filter(({c})=>c.field==='place_proximity'&&c.distance_preference==='bounded').map(({c})=>placeKey(c)));
  for(const {r,c} of results){
    if(c.field==='place_proximity'&&c.distance_preference==='nearer'&&boundedPlaces.has(placeKey(c)))continue;
    if(r.status==='unknown'){pending.add(label(c));continue;}
    let text;const relative=c.field==='place_proximity'&&c.strength==='soft'&&c.distance_preference==='nearer';
    if(c.field==='place_proximity'){
      const key=placeKey(c);
      if(seenPlaces.has(key))continue;
      if(!finite(r.observed_value)){pending.add(label(c));continue;}
      seenPlaces.add(key);
      const region=/^URA:/.test(c.target?.place_id||'');
      const metric=c.distance_type==='walking'?'步行':c.distance_type==='driving'?'驾车':'直线';
      text=region&&r.observed_value===0?`位于${targetName(c)}区域内`:`距${targetName(c)}${region?'区域边界':''} ${distance(r.observed_value)}（${metric}`;
      if(!(region&&r.observed_value===0))text+=((c.strength==='hard'||c.distance_preference==='bounded')&&finite(c.value)?`，${c.strength==='soft'?'希望':'要求'} ${{lte:'≤',gte:'≥'}[c.operator]||''} ${distance(c.value)}`:relative?'，更近优先':'')+'）';
    }else if(c.field==='price'&&c.operator==='lte')text=r.status==='fail'?`月租超过 ${money(c.value)} 预算`:`月租在 ${money(c.value)} 预算内`;
    else text=formatCriterion(c).replace(/^(必须|偏好) · /,'').replace('SGD/month','新币/月').replace(/ count\b/,' 间');
    // A soft preference affects ranking; it is not a pass/fail requirement.
    rows.push({status:c.strength==='soft'?'preference':r.status,text:(c.strength==='soft'?'偏好 · ':'')+text});
  }
  const l=candidate.listing;
  if(!finite(l.bedrooms)||l.bedrooms<=0)pending.add('卧室数量');
  if(!finite(l.floor_area_sqft)||l.floor_area_sqft<=0)pending.add('室内面积');
  if(!l.property_type)pending.add('房屋类型');
  if(!l.availability_status||l.availability_status==='unknown')pending.add('当前是否可租');
  return {rows,pending:[...pending]};
}

export function renderMatches({el,candidate,criteria,formatCriterion,detail=false}){
  const section=el('section',detail?'detail-match':'listing-match-summary');
  section.append(el(detail?'h3':'h4','','与你的需求匹配情况'));
  const summary=matchSummary(candidate,criteria,formatCriterion),chips=el('div','match-chips');
  for(const row of summary.rows)chips.append(el('span','match-chip '+row.status,(row.status==='preference'?'':row.status==='fail'?'× ':'✓ ')+row.text));
  section.append(chips);
  if(summary.pending.length)section.append(el('p','pending-summary','待确认：'+summary.pending.join('、')));
  return section;
}

export function rentElement(el,value,className){const p=el('div',className);p.append(el('span','rent-amount',money(value)));if(finite(value))p.append(el('span','rent-period',' / 月'));return p;}
