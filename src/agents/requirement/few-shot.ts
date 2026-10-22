import type {ModelIntake} from './contracts.js';
import type {Requirements} from '../../contracts/recommendation-v1.js';
export type IntakeExample={id:string;query:string;base:Requirements;output:ModelIntake};
const empty:Requirements={listing_type:'RENT',criteria:[]};
const multiTurnBase:Requirements={listing_type:'RENT',criteria:[
  {criterion_id:'minimum-bedrooms',field:'bedrooms',operator:'eq',value:1,unit:'count',strength:'hard'},
  {criterion_id:'bathrooms',field:'bathrooms',operator:'eq',value:1,unit:'count',strength:'hard'},
  {criterion_id:'monthly-rent',field:'price',operator:'lte',value:2400,unit:'SGD/month',strength:'hard'},
  {criterion_id:'place_proximity:URA:AREA:CLEMENTI',field:'place_proximity',operator:'lte',value:4000,unit:'m',strength:'hard',target:{place_id:'URA:AREA:CLEMENTI',name:'CLEMENTI'},distance_type:'straight_line',distance_preference:'bounded'},
  {criterion_id:'place_proximity:URA:AREA:CLEMENTI-preference',field:'place_proximity',operator:'lte',value:1000,unit:'m',strength:'soft',weight:1,target:{place_id:'URA:AREA:CLEMENTI',name:'CLEMENTI'},distance_type:'straight_line',distance_preference:'nearer'},
  {criterion_id:'place_proximity:PublicTransport-preference',field:'place_proximity',operator:'lte',value:800,unit:'m',strength:'soft',weight:1,target:{category:'PublicTransport',name:'最近的MRT或公交站'},distance_type:'straight_line',distance_preference:'bounded'},
]};
export function attribute(field:string,operator:ModelIntake['upsert'][number]['operator'],value:ModelIntake['upsert'][number]['value'],quote:string,options:Partial<ModelIntake['upsert'][number]>={}):ModelIntake['upsert'][number]{
  const units:Record<string,ModelIntake['upsert'][number]['unit']>={price:'SGD/month',bedrooms:'count',bathrooms:'count',floor_area_sqft:'sqft',land_area_sqft:'sqft',build_year:'year',lease_months:'month',commute_time:'min',mrt_distance_m:'m',mrt_walking_mins:'min'};
  return {field,operator,value,unit:units[field]??null,strength:'hard',weight:null,target_name:null,transport_mode:null,quote,...options};
}
export function location(name:string|null,categories:ModelIntake['locations'][number]['categories'],quote:string,options:Partial<ModelIntake['locations'][number]>={}):ModelIntake['locations'][number]{return {name,categories,relation:'any_of',operator:'near',value:null,unit:null,strength:'soft',weight:null,distance_type:'straight_line',explicit_station:false,prefer_closer:!!name&&!['gte','between'].includes(options.operator||'near'),quote,...options};}
export function output(query:string,patch:Partial<ModelIntake>={}):ModelIntake{return {schema_version:'2.0',supported:true,upsert:[],locations:[],removals:[],clarifications:[],coverage:[{quote:query,disposition:'extracted'}],...patch};}
const ex=(id:string,query:string,patch:Partial<ModelIntake>,base=empty):IntakeExample=>({id,query,base,output:output(query,patch)});
export const INTAKE_EXAMPLES:IntakeExample[]=[
  ex('rent-ceiling','每个月最多付两千新币，别再贵了。',{upsert:[attribute('price','lte',2000,'每个月最多付两千新币')]}),
  ex('rent-interval','希望租金在1800到2400新币之间。',{upsert:[attribute('price','between',[1800,2400],'租金在1800到2400新币之间')]}),
  ex('rent-soft','3000新币是上限，如果能低于2500就更好了。',{upsert:[attribute('price','lte',3000,'3000新币是上限'),attribute('price','lte',2500,'如果能低于2500就更好了',{strength:'soft'})]}),
  ex('exact-layout','我要一室一卫，1B1B就行。',{upsert:[attribute('bedrooms','eq',1,'一室一卫'),attribute('bathrooms','eq',1,'一室一卫')]}),
  ex('bedroom-minimum','两间卧室起，三个也行，卫生间最少两个。',{upsert:[attribute('bedrooms','gte',2,'两间卧室起'),attribute('bathrooms','gte',2,'卫生间最少两个')]}),
  ex('ambiguous-bedroom','给我找个两房的。',{clarifications:[{quote:'两房',question:'你需要至少两间卧室，还是恰好两间？'}],coverage:[{quote:'给我找个两房的。',disposition:'clarification'}]}),
  ex('hdb-model','找HDB的3-room flat。',{upsert:[attribute('property_type','eq','HDB Flat','HDB')],clarifications:[{quote:'3-room flat',question:'HDB的3-room型号不等于三间卧室，你需要几间卧室？'}]}),
  ex('area-original-unit','屋子不要小于四十五平方米，别超过70平方米。',{upsert:[attribute('floor_area_sqft','between',[45,70],'不要小于四十五平方米，别超过70平方米',{unit:'sqm'})]}),
  ex('area-imperial','500sqft是底线，能到700sqft会更好。',{upsert:[attribute('floor_area_sqft','gte',500,'500sqft是底线'),attribute('floor_area_sqft','gte',700,'能到700sqft会更好',{strength:'soft'})]}),
  ex('private-bath','不想跟其他人共用洗澡和上厕所的地方。',{upsert:[attribute('amenities','eq','private_bathroom','不想跟其他人共用洗澡和上厕所的地方')]}),
  ex('bath-count-is-not-private','一个卫生间就够，独卫不强求。',{upsert:[attribute('bathrooms','eq',1,'一个卫生间就够')],removals:[{field:'amenities',criterion_id:'amenities:private_bathroom',quote:'独卫不强求'}]}, {listing_type:'RENT',criteria:[{criterion_id:'amenities:private_bathroom',field:'amenities',operator:'eq',value:'private_bathroom',strength:'hard'}]}),
  ex('room-no-landlord','找普通房，能做饭，不跟屋主一起住。',{upsert:[attribute('rental_scope','eq','room','普通房'),attribute('amenities','eq','common_room','普通房'),attribute('amenities','eq','cooking_allowed','能做饭'),attribute('amenities','eq','no_live_in_landlord','不跟屋主一起住')]}),
  ex('amenity-preferences','空调必须有，能养猫就更好，泳池有没有都无所谓。',{upsert:[attribute('amenities','eq','air_conditioning','空调必须有'),attribute('amenities','eq','pet_policy','能养猫就更好',{strength:'soft'})]}),
  ex('furniture-lease','家具要配齐，租期至少半年，明年1月10日以前入住。',{upsert:[attribute('furnishing','eq','fully_furnished','家具要配齐'),attribute('lease_months','gte',6,'租期至少半年'),attribute('move_in_date','lte','2027-01-10','明年1月10日以前入住')]}),
  ex('property-district','只看D05或D21的公寓Condominium，CCR不是必须的，只是偏好。',{upsert:[attribute('district','in',['D05','D21'],'只看D05或D21'),attribute('property_type','eq','Condominium','公寓Condominium'),attribute('region_code','eq','CCR','CCR不是必须的，只是偏好',{strength:'soft'})]}),
  ex('building-land','地至少1000平方英尺，房子2000年以后建，邮编是059817。',{upsert:[attribute('land_area_sqft','gte',1000,'地至少1000平方英尺'),attribute('build_year','gte',2000,'房子2000年以后建'),attribute('postal_code','eq','059817','邮编是059817')]}),
  ex('university-geo','预算2000，南洋理工大学五公里之内；房子周围一公里内，最好有地铁或者巴士站。',{upsert:[attribute('price','lte',2000,'预算2000')],locations:[location('南洋理工大学',[],'南洋理工大学五公里之内',{operator:'lte',value:5,unit:'km',strength:'hard'}),location(null,['MRTStation','BusStop'],'房子周围一公里内，最好有地铁或者巴士站',{operator:'lte',value:1,unit:'km'})]}),
  ex('transport-and','地铁站和公交站都得在800米内。',{locations:[location(null,['MRTStation','BusStop'],'地铁站和公交站都得在800米内',{relation:'all_of',operator:'lte',value:800,unit:'m',strength:'hard'})]}),
  ex('bus-only','巴士站不要超过500米，近一点更方便。',{locations:[location(null,['BusStop'],'巴士站不要超过500米',{operator:'lte',value:500,unit:'m',strength:'hard'})]}),
  ex('near-no-radius','房子在金文泰附近就好，超市也尽量近些。',{locations:[location('金文泰',[],'房子在金文泰附近就好'),location(null,['Supermarket'],'超市也尽量近些')]}),
  ex('named-station','离Clementi MRT最多一公里，按步行距离算。',{locations:[location('Clementi MRT',[],'离Clementi MRT最多一公里，按步行距离算',{operator:'lte',value:1,unit:'km',strength:'hard',distance_type:'walking',explicit_station:true})]}),
  ex('distance-interval','离NUS别太近也别太远，一到三公里之间。',{locations:[location('NUS',[],'离NUS别太近也别太远，一到三公里之间',{operator:'between',value:[1,3],unit:'km',strength:'hard'})]}),
  ex('route-not-proximity','公共交通去NUS不要超过30分钟；到地铁站按挂牌标注，步行不超过8分钟。',{upsert:[attribute('commute_time','lte',30,'公共交通去NUS不要超过30分钟',{target_name:'NUS',transport_mode:'transit'}),attribute('mrt_walking_mins','lte',8,'到地铁站按挂牌标注，步行不超过8分钟')]}),
  ex('reported-distance','平台写的最近MRT距离不超过600米，不是让我直线算。',{upsert:[attribute('mrt_distance_m','lte',600,'平台写的最近MRT距离不超过600米')]}),
  ex('plain-mrt-distance','住宅与最近MRT的距离上限是650米。',{locations:[location(null,['MRTStation'],'住宅与最近MRT的距离上限是650米',{operator:'lte',value:650,unit:'m',strength:'hard'})]}),
  ex('change-single','预算提到2600，独卫改成最好有，其他保留。',{upsert:[attribute('price','lte',2600,'预算提到2600'),attribute('amenities','eq','private_bathroom','独卫改成最好有',{strength:'soft'})],removals:[{field:'amenities',criterion_id:'amenities:private_bathroom',quote:'独卫改成最好有'}]}, {listing_type:'RENT',criteria:[{criterion_id:'monthly-rent',field:'price',operator:'lte',value:2000,unit:'SGD/month',strength:'hard'},{criterion_id:'amenities:private_bathroom',field:'amenities',operator:'eq',value:'private_bathroom',strength:'hard'},{criterion_id:'minimum-bedrooms',field:'bedrooms',operator:'gte',value:2,unit:'count',strength:'hard'}]}),
  ex('remove-only','不用独卫了，但猫还是要能养。',{upsert:[attribute('amenities','eq','pet_policy','猫还是要能养')],removals:[{field:'amenities',criterion_id:'amenities:private_bathroom',quote:'不用独卫了'}]}, {listing_type:'RENT',criteria:[{criterion_id:'amenities:private_bathroom',field:'amenities',operator:'eq',value:'private_bathroom',strength:'hard'},{criterion_id:'amenities:pet_policy',field:'amenities',operator:'eq',value:'pet_policy',strength:'hard'}]}),
  ex('transport-mode-update','刚才说的30分钟，是坐公共交通，不是开车。',{upsert:[attribute('commute_time','lte',30,'刚才说的30分钟，是坐公共交通，不是开车',{target_name:'NUS',transport_mode:'transit'})]}, {listing_type:'RENT',criteria:[{criterion_id:'commute_time',field:'commute_time',operator:'lte',value:30,unit:'min',strength:'hard',target:{name:'NUS'},transport_mode:'unspecified'}]}),
  ex('refresh-has-change','重新推荐一下，最好周边700米内有公园。',{locations:[location(null,['Park'],'最好周边700米内有公园',{operator:'lte',value:700,unit:'m'})]}),
  ex('restate-geographic-update','那我修改要求，租金还是2400新币以内，金文泰附近4公里内，最好800米内有MRT或公交站。',{
    upsert:[attribute('price','lte',2400,'租金还是2400新币以内')],
    locations:[location('金文泰',[],'金文泰附近4公里内',{operator:'lte',value:4,unit:'km',strength:'hard'}),location(null,['MRTStation','BusStop'],'最好800米内有MRT或公交站',{operator:'lte',value:800,unit:'m'})],
    // Same targets/strengths update in place after resolution. Unmentioned layout stays.
  },multiTurnBase),
  ex('cancel-layout-only','卧室和卫生间数量不限制了，金文泰4公里、2400预算和交通偏好都照旧。',{
    removals:[{field:'bedrooms',criterion_id:'minimum-bedrooms',quote:'卧室和卫生间数量不限制了'},{field:'bathrooms',criterion_id:'bathrooms',quote:'卧室和卫生间数量不限制了'}],
    coverage:[{quote:'卧室和卫生间数量不限制了',disposition:'removed'},{quote:'金文泰4公里、2400预算和交通偏好都照旧',disposition:'context'}],
  },multiTurnBase),
  ex('replace-location-only','不找金文泰了，改成裕廊东3公里以内，其他不变。',{
    removals:[{field:'place_proximity',criterion_id:'place_proximity:URA:AREA:CLEMENTI',quote:'不找金文泰了'},{field:'place_proximity',criterion_id:'place_proximity:URA:AREA:CLEMENTI-preference',quote:'不找金文泰了'}],
    locations:[location('裕廊东',[],'改成裕廊东3公里以内',{operator:'lte',value:3,unit:'km',strength:'hard'})],
  },multiTurnBase),
  ex('unsupported-buy','我要买一套房。',{supported:false,clarifications:[{quote:'买一套房',question:'目前支持住宅租赁，是否改为找租房？'}],coverage:[{quote:'我要买一套房。',disposition:'clarification'}]}),
  ex('availability','现在必须还租得到，别给我已经租出去的。',{upsert:[attribute('current_availability','eq',true,'现在必须还租得到')]}),
  ex('market-region','市场分区选OCR，不限具体邮区。',{upsert:[attribute('region_code','eq','OCR','市场分区选OCR')]}),
  ex('market-region-restrict-only','市场分区我只看RCR，其他分区都不要。',{upsert:[attribute('region_code','eq','RCR','市场分区我只看RCR')],coverage:[{quote:'市场分区我只看RCR，其他分区都不要',disposition:'extracted'}]}),
  ex('two-digit-district','新加坡邮区只选08或19，其他邮区先不看。',{upsert:[attribute('district','in',['D08','D19'],'新加坡邮区只选08或19')]}),
];
// Example retrieval only; these tokens never create or override a user criterion.
function tokens(text:string){const ts=[...text.toLowerCase().matchAll(/[a-z]+|\d+/g)].map(x=>x[0]);for(const m of text.matchAll(/[\u4e00-\u9fff]+/g)){const chars=[...m[0]];ts.push(...chars);for(let i=0;i<chars.length-1;i++)ts.push(chars[i]+chars[i+1]);}return ts;}
export function selectIntakeExamples(query:string,count=2){const q=tokens(query),df=new Map<string,number>();for(const e of INTAKE_EXAMPLES)for(const t of new Set(tokens(e.query)))df.set(t,(df.get(t)||0)+1);
  return INTAKE_EXAMPLES.map((e,index)=>{const ts=tokens(e.query),score=q.reduce((n,t)=>{const tf=ts.filter(x=>x===t).length;return n+(tf?Math.log(1+(INTAKE_EXAMPLES.length-(df.get(t)||0)+.5)/((df.get(t)||0)+.5))*tf/(tf+1.2*(.25+.75*ts.length/20)):0);},0);return {e,index,score};}).sort((a,b)=>b.score-a.score||a.index-b.index).slice(0,count).map(x=>x.e);
}
