import type {Criterion,Requirements} from '../contracts/recommendation-v1.js';
import {parseLocationIntents,CATEGORY_NAMES,DEFAULT_PROXIMITY_SCALE_M} from './geo-intake.js';

const digits:Record<string,number>={零:0,〇:0,一:1,二:2,两:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9,one:1,two:2,three:3,four:4,five:5,six:6};
const N='(?:\\d+(?:,\\d{3})*(?:\\.\\d+)?[kK]?|[零〇一二两三四五六七八九十百千万]+|one|two|three|four|five|six)';
export function rentalNumber(text:string):number{
 const t=text.toLowerCase().replaceAll(',','');if(digits[t]!==undefined)return digits[t];if(/^\d/.test(t))return Number(t.replace(/k$/,''))*(t.endsWith('k')?1000:1);
 const units:Record<string,number>={十:10,百:100,千:1000,万:10000};if(!/[十百千万]/.test(t))return Number([...t].map(c=>digits[c]).join(''));
 let total=0,pending=0,lastUnit=1;for(const ch of t){if(units[ch]){const u=units[ch];total+=(pending||1)*u;pending=0;lastUnit=u;}else pending=digits[ch];}return total+pending*(lastUnit>=100?lastUnit/10:1);
}
type Maker=(field:string,operator:Criterion['operator'],value:unknown,unit?:string,soft?:boolean,extra?:Partial<Criterion>)=>Criterion;
export function parseRentalLanguage(query:string,base:Requirements,make:Maker){
 const q=query.trim(),clauses=q.split(/[，,、。；;\n](?!\d{3}\b)/).map(v=>v.trim()).filter(Boolean),upsert:Criterion[]=[],remove_fields:string[]=[],sources:{field:string;quote:string}[]=[],clarifications:string[]=[];
 const note=(field:string)=>sources.push({field,quote:q});
 const add=(field:string,op:Criterion['operator'],value:unknown,unit?:string,soft=false,extra:Partial<Criterion>={})=>{const c=make(field,op,value,unit,soft,extra),key=(x:Criterion)=>x.field+'|'+x.strength+'|'+(x.field==='amenities'?x.value:x.field==='place_proximity'?x.target?.category||x.target?.place_id||'':'');const i=upsert.findIndex(x=>key(x)===key(c));if(i<0)upsert.push(c);else upsert[i]=c;note(field);};
 const soft=(t:string)=>!/必须|一定|硬性|must|required/i.test(t)&&/最好|希望|优先|偏好|prefer|ideally/i.test(t);
 const op=(t:string,fallback:Criterion['operator']='eq'):Criterion['operator']=>/最多|至多|不超过|不能超过|不多于|以内|以下|以前|之前|at most|maximum|under|below|no more|<=|≤/i.test(t)?'lte':/至少|最少|不少于|以上|以后|之后|起|at least|minimum|or more|>=|≥/i.test(t)?'gte':fallback;
 const remove=(field:string)=>{if(!remove_fields.includes(field)){remove_fields.push(field);note(field);}};
 if(/买房|购房|买卖|\bbuy\b|\bfor sale\b/i.test(q))return {upsert:[],remove_fields:[],sources:[],supported:false,clarifications:['当前系统支持新加坡住宅租赁，请确认是否改为找租房。'],location_intents:[]};
 const foreign=/美元|人民币|USD|RMB|CNY/i.test(q);if(foreign&&/预算|租金|月租|budget|rent/i.test(q))clarifications.push('租金以 SGD/月评估。请提供新加坡元月租上限，系统不会自动换算币种。');
 const removal:Record<string,RegExp>={price:/预算不限|租金不限|取消预算|no budget limit/i,bedrooms:/卧室不限|房数不限|取消卧室|any bedrooms/i,district:/区域不限|地区不限|不限邮区|取消(?:区域|地区)|any district|remove district/i,furnishing:/家具不限|取消家具要求/i,amenities:/(?:取消|不要求|不需要).{0,3}(?:独立卫浴|独卫|泳池|宠物|设施)|没有泳池也可以/i,bathrooms:/(?:取消|不限|不要求)(?:卫浴数量|卫生间数量)/i,place_proximity:/取消(?:超市|距离|地点)|不要求靠近|remove proximity/i,commute_time:/取消通勤|通勤不限/i,lease_months:/租期不限|取消租期/i,rental_scope:/取消(?:整租|单间)要求/i,move_in_date:/入住时间不限|取消入住/i,floor_area_sqft:/面积不限|取消面积/i,postal_code:/邮编不限|取消邮编/i,build_year:/年份不限|取消建成年份/i};
 for(const [f,r] of Object.entries(removal))if(r.test(q))remove(f);
 if(/只保留.*预算|only keep.*budget/i.test(q))for(const f of new Set(base.criteria.map(c=>c.field)))if(f!=='price')remove(f);
 const budgets:Criterion[]=[];
 for(const clause of clauses){
  const m=clause.match(/预算|月租|租金|\bbudget\b|\brent\b|每个?月|一个月|monthly|\/(?:月|mo)/i),monetary=m||/(?:最多|under|below).*(?:新币|SGD|S\$|\/月)/i.test(clause);if(!monetary||foreign||/预算不限|取消预算|租金不限/.test(clause))continue;
  if(/(?:预算|月租|租金|budget|rent)[^\d]{0,5}-\s*\d/i.test(clause)){clarifications.push('月租不能为负数，请提供有效的 SGD/月预算。');continue;}
  const part=m&&m[0][0]!=='/'?clause.slice(m.index!+m[0].length):clause;const matches=[...part.matchAll(new RegExp(N,'gi'))];if(!matches.length)continue;
  const first=matches[0],second=matches[1],between=second&&/^\s*(?:新币|SGD)?\s*(?:到|至|—|-|~|～|to|and)\s*$/i.test(part.slice(first.index!+first[0].length,second.index));
  const value=between?[rentalNumber(first[0]),rentalNumber(second[0])]:rentalNumber(first[0]);const c=make('price',between?'between':'lte',value,'SGD/month',soft(clause));budgets.push(c);
 }
 const hardBudgets=budgets.filter(c=>c.strength==='hard');if(new Set(hardBudgets.map(c=>JSON.stringify(c.value))).size>1&&!/改为|改成|提高到|降到|调整到/.test(q)){clarifications.push('这条消息包含不同预算，请明确最终的 SGD/月上限。');}else for(const c of budgets)add(c.field,c.operator,c.value,c.unit,c.strength==='soft');
 const hdbRooms=/\bHDB\b|组屋/i.test(q)&&new RegExp(`${N}\\s*[- ]?room\\s*(?:flat|HDB)?|(?:组屋|HDB)\\s*${N}\\s*房`,'i').test(q);
 if(hdbRooms)clarifications.push('HDB 的 room 型号与卧室数量不同，请明确实际需要几间卧室，或提供官方户型型号。');
 const layout=new RegExp(`(${N})\\s*(?:BR|B)\\s*[/+-]?\\s*(${N})\\s*(?:BA|B)\\b|(${N})\\s*(?:室|房)(?:\\s*${N}\\s*厅)?\\s*(${N})\\s*卫(?!生)`,'i').exec(q);
 if(layout&&!hdbRooms){add('bedrooms','eq',rentalNumber(layout[1]||layout[3]),'count');add('bathrooms','eq',rentalNumber(layout[2]||layout[4]),'count');}
 const bedroomHits:Criterion[]=[];
 for(const clause of clauses){
  if(hdbRooms||layout&&clause.includes(layout[0]))continue;
  const m=clause.match(new RegExp(`(${N})\\s*(?:间|个)?\\s*(卧室|房|室|bedrooms?|beds?)(?:\\s*(?:以上|起|or more|\\+))?`,'i'));if(!m)continue;
  const count=rentalNumber(m[1]),operator=op(clause,'eq'),explicit=operator!=='eq'||/恰好|正好|exactly/i.test(clause)||/bedrooms?|beds?/i.test(m[2]);
  if(!explicit)clarifications.push(`你说的“${m[0]}”是至少 ${count} 房，还是恰好这个数量？`);else bedroomHits.push(make('bedrooms',operator,count,'count',soft(clause)));
 }
 if(bedroomHits.some(c=>c.operator==='gte'&&bedroomHits.some(x=>x.operator==='lte'&&Number(x.value)<Number(c.value)))){clarifications.push('卧室数量的上下限冲突，请确认需要的范围。');bedroomHits.length=0;}for(const c of bedroomHits)add(c.field,c.operator,c.value,c.unit,c.strength==='soft');
 for(const clause of clauses){
  if(layout&&clause.includes(layout[0])||/独立卫浴|独立卫生间|独立浴室|独卫|private\s+(?:bathroom|bath)|en[ -]?suite/i.test(clause)&&!new RegExp(`${N}\\s*(?:个|间)?\\s*(?:卫生间|浴室|卫浴|bathroom)`,'i').test(clause))continue;
  const m=clause.match(new RegExp(`(${N})\\s*(?:个|间)?\\s*(?:卫生间|浴室|卫浴|卫|bathrooms?)|(?:卫浴|卫生间|浴室)[^，,;；\\d一二两三四五六七八九十]{0,8}(${N})`,'i'));if(m)add('bathrooms',op(clause,/bathroom|正好|恰好|改成/i.test(clause)?'eq':'gte'),rentalNumber(m[1]||m[2]),'count',soft(clause));
 }
 const types:[RegExp,string[]][]=[[/walk[- ]?up apartment|无电梯公寓/i,['Walk-up Apartment']],[/semi[- ]?detached|半独立/i,['Semi-Detached House']],[/corner terrace|角头排屋/i,['Corner Terrace']],[/good class bungalow|优质洋房/i,['Good Class Bungalow']],[/terraced? house|排屋/i,['Terraced House']],[/detached house|独立洋房/i,['Detached House']],[/bungalow/i,['Bungalow House']],[/shophouse|店屋/i,['Shophouse']],[/\bcondo(?:minium)?\b|公寓大楼/i,['Condominium']],[/\bapartment\b|普通公寓/i,['Apartment']],[/\bHDB\b|组屋/i,['HDB Flat']],[/公寓/i,['Condominium','Apartment','Walk-up Apartment']]];
 const typeClause=clauses.find(c=>types.some(([r])=>r.test(c)));if(typeClause){let values:string[]=[];for(const [r,v] of types)if(r.test(typeClause)){values.push(...v);if(!/或者|或|\bor\b/i.test(typeClause))break;}values=[...new Set(values)];add('property_type',values.length===1?'eq':'in',values.length===1?values[0]:values,undefined,soft(typeClause));}
 if(!remove_fields.includes('district')){const ds=[...q.matchAll(/\bD(0?[1-9]|1\d|2[0-8])\b/gi)].map(m=>`D${m[1].padStart(2,'0')}`);if(ds.length){const negative=/不要|不想要|排除|except|exclude/i.test(q.slice(0,q.search(/\bD\d/i))),values=negative?Array.from({length:28},(_,i)=>`D${String(i+1).padStart(2,'0')}`).filter(x=>!ds.includes(x)):[...new Set(ds)];add('district',values.length===1?'eq':'in',values.length===1?values[0]:values);}}
 const postal=q.match(/(?:邮编|postal code)\s*[:：]?\s*(\d{6})/i);if(postal)add('postal_code','eq',postal[1]);
 for(const clause of clauses){
  const m=clause.match(new RegExp(`(${N})(?:\\s*(?:到|至|—|-|~|～|to)\\s*(${N}))?\\s*(平方米|平米|㎡|sqm|sqft|平方英尺)`,'i'));
  if(m){const field=/土地|land\s*area/i.test(clause)?'land_area_sqft':'floor_area_sqft',scale=/平方米|平米|㎡|sqm/i.test(m[3])?10.76391041671:1;add(field,m[2]?'between':op(clause,'gte'),m[2]?[rentalNumber(m[1])*scale,rentalNumber(m[2])*scale]:rentalNumber(m[1])*scale,'sqft',soft(clause));}
  else if(/面积|\barea\b/i.test(clause)&&/\d/.test(clause))clarifications.push('请明确面积单位：平方米还是平方英尺（sqft）。');
  const year=clause.match(/(?:建成年份|建成|built|build year).*?(\d{4})|(\d{4}).{0,6}建成/i);if(year)add('build_year',op(clause,'gte'),Number(year[1]||year[2]),'year');
 }
 if(/freehold|永久产权/i.test(q))add('tenure_category','eq','Freehold');else if(/99[- ]?year\s*leasehold|99年产权/i.test(q))add('tenure_category','eq','99-year Leasehold');
 const marketRegion=q.match(/\b(CCR|RCR|OCR)\b/i);if(marketRegion)add('region_code','eq',marketRegion[1].toUpperCase());
 if(/north[- ]east region|north region|east region|west region|central region|东北大区|北部大区|东部大区|西部大区|中部大区/i.test(q)&&!marketRegion&&!/附近|周边|\bnear\b|\baround\b/i.test(q))clarifications.push('这里是指 URA 规划大区，还是房源的 CCR/RCR/OCR 市场分区？两者不能直接映射为同一个区域编码。');
 if(/整租|整套|whole unit|entire unit/i.test(q))add('rental_scope','eq','whole_unit');
 if(/单间|单房出租|普通房|主卧|common room|master room|master bedroom/i.test(q))add('rental_scope','eq','room');
 if(/主卧|master room|master bedroom/i.test(q))add('amenities','eq','master_room');if(/普通房|common room/i.test(q))add('amenities','eq','common_room');
 // A bare Chinese 普通房 describes room scope; common-room subtype is explicit in English.
 if(!/common room/i.test(q)){const i=upsert.findIndex(c=>c.field==='amenities'&&c.value==='common_room');if(i>=0)upsert.splice(i,1);}
 for(const clause of clauses){if(/家具不限|取消家具要求/.test(clause))continue;if(/不要家具|不用配家具|unfurnished/i.test(clause))add('furnishing','eq','unfurnished',undefined,soft(clause));else if(/部分家具|partially furnished/i.test(clause))add('furnishing','eq','partially_furnished',undefined,soft(clause));else if(/全家具|家具齐全|fully furnished/i.test(clause))add('furnishing','eq','fully_furnished',undefined,soft(clause));}
 const lease=q.match(new RegExp(`(?:租期|租|lease)\\s*(${N})\\s*(个月|月|months?|年|years?)`,'i'));if(lease)add('lease_months','eq',rentalNumber(lease[1])*(/年|year/.test(lease[2])?12:1),'month');
 const date=q.match(/(?:入住|move in)[^\d]{0,8}(\d{4}-\d{2}-\d{2})|(\d{4}-\d{2}-\d{2})\s*入住/i);if(date)add('move_in_date','eq',date[1]||date[2]);
 const amenityPatterns:[RegExp,string][]=[[/独立卫浴|独立卫生间|独立浴室|独卫|private\s+(?:bathroom|bath)|en[ -]?suite/i,'private_bathroom'],[/泳池|pool/i,'swimming_pool'],[/宠物|养猫|养狗|pets?/i,'pet_policy'],[/可以做饭|能做饭|可做饭|cooking allowed/i,'cooking_allowed'],[/空调|air[- ]?con(?:ditioning)?/i,'air_conditioning'],[/不和房东同住|没有同住屋主|no live[- ]in landlord/i,'no_live_in_landlord']];
 const canceledAmenities=new Set<string>(),remove_criterion_ids:string[]=[];
 for(const clause of clauses)for(const [r,v] of amenityPatterns)if(r.test(clause)){
  if(/取消|不要求|不需要|不必|无所谓|没有泳池也可以/i.test(clause)){canceledAmenities.add(v);continue;}
  add('amenities','eq',v,undefined,soft(clause));
 }
 if(canceledAmenities.size){
  const existing=base.criteria.filter(c=>c.field==='amenities'),keepExisting=existing.filter(c=>!canceledAmenities.has(String(c.value))),keepNew=upsert.filter(c=>c.field==='amenities'&&!canceledAmenities.has(String(c.value)));
  if(keepExisting.length||keepNew.length){const i=remove_fields.indexOf('amenities');if(i>=0)remove_fields.splice(i,1);remove_criterion_ids.push(...existing.filter(c=>canceledAmenities.has(String(c.value))).map(c=>c.criterion_id));}
  else remove('amenities');
 }
 if(/现在(?:必须)?可租|确认(?:现在)?可租|当前可租|available now|verified availability/i.test(q))add('current_availability','eq',true);
 for(const clause of clauses){
  const genericMrt=/MRT|地铁站/i.test(clause),walking=clause.match(new RegExp(`(${N})\\s*(?:分钟|minutes?|mins?)`,'i')),distance=clause.match(new RegExp(`(${N})\\s*(公里|千米|km|米|m\\b)`,'i'));
  if(genericMrt&&walking&&/步行|walk/i.test(clause))add('mrt_walking_mins','lte',rentalNumber(walking[1]),'min',soft(clause),{distance_type:'platform_reported'});
  if(genericMrt&&distance&&/平台|platform/i.test(clause))add('mrt_distance_m','lte',rentalNumber(distance[1])*(/公里|千米|km/i.test(distance[2])?1000:1),'m',soft(clause),{distance_type:'platform_reported'});
  const categories:[RegExp,string][]=[[/超市|supermarket/i,'Supermarket'],[/学校|school/i,'School'],[/公园|park/i,'Park'],[/食阁|food court/i,'FoodCourt'],[/商场|mall/i,'Mall']];for(const [r,category] of categories)if(r.test(clause)&&!remove_fields.includes('place_proximity'))add('place_proximity','lte',distance?rentalNumber(distance[1])*(/公里|千米|km/i.test(distance[2])?1000:1):DEFAULT_PROXIMITY_SCALE_M,'m',!distance||soft(clause),{target:{category},distance_type:/步行|walking/i.test(clause)?'walking':'straight_line'});
  if(genericMrt&&/步行|walk/i.test(clause)&&walking)continue;
  const commute=clause.match(/(?:到|去|离|距|\bto)\s*(NUS|NTU|BIZ2|[^，。,;；\n]{1,30}?)\s*(?:通勤|公交|公共交通|开车|驾车|步行|by transit|by bus|driving|walking)?\s*(\d+)\s*(?:分钟|minutes?|mins?)/i);if(commute)add('commute_time','lte',Number(commute[2]),'min',soft(clause),{target:{name:commute[1].replace(/(?:通勤|公交|公共交通|开车|驾车|步行).*$/,'').trim()},transport_mode:/公交|公共交通|transit|bus/i.test(clause)?'transit':/开车|驾车|driv/i.test(clause)?'driving':/步行|walking/i.test(clause)?'walking':'unspecified'});
 }
 const mode=q.match(/通勤[^，。\n]{0,18}?(公交|公共交通|公共运输|开车|驾车|步行|transit|driving|walking)/i);if(mode&&!upsert.some(c=>c.field==='commute_time'))for(const c of base.criteria.filter(c=>c.field==='commute_time'))add('commute_time',c.operator,c.value,c.unit,c.strength==='soft',{target:c.target,transport_mode:/公交|公共交通|公共运输|transit/i.test(mode[1])?'transit':/开车|驾车|driving/i.test(mode[1])?'driving':'walking'});
 const locations=parseLocationIntents(q).filter(l=>!CATEGORY_NAMES.some(([r])=>r.test(l.name))&&!/平台|步行.*分钟/i.test(l.quote));
 const final=upsert.filter(c=>!remove_fields.includes(c.field)&&!(c.field==='amenities'&&canceledAmenities.has(String(c.value))));
 if(!final.length&&!locations.length&&!remove_fields.length&&!clarifications.length&&!base.criteria.length&&!/随便看看|宽泛浏览|browse all|show all/i.test(q))clarifications.push('请提供租房月预算、卧室数量或邮区中的任意一项；也可以明确选择宽泛浏览。');
 return {upsert:final,remove_fields,remove_criterion_ids,clarifications:[...new Set(clarifications)].slice(0,3),sources,supported:true,location_intents:locations};
}
