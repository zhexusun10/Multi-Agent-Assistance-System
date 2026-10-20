import type {Criterion} from '../contracts/recommendation-v1.js';
import {amenityLabels} from './field-capabilities.js';
import {criterionText} from './explanation.js';

/** Turn internal filter/evidence labels into questions a renter would actually ask. */
export function customerQuestion(c:Criterion):string{
  if(c.field==='amenities')return ({private_bathroom:'房间有独立卫浴吗，还是需要和其他人共用卫生间？',master_room:'出租的是主卧吗？',common_room:'出租的是普通房吗？',pet_policy:'可以养宠物吗？',cooking_allowed:'平时可以做饭吗？',air_conditioning:'房间有空调吗？',no_live_in_landlord:'房东会一起住吗？',swimming_pool:'这里可以使用游泳池吗？'} as Record<string,string>)[String(c.value)]||`想问一下，有${amenityLabels[String(c.value)]||String(c.value)}吗？`;
  if(c.field==='rental_scope')return c.value==='whole_unit'?'这是整套出租，还是只租其中一间房？':'这是单间出租吗？';
  if(c.field==='furnishing')return c.value==='fully_furnished'?'家具都配齐了吗？具体带哪些家具？':c.value==='unfurnished'?'房子是不带家具的吗？':'现在带哪些家具，还有哪些需要自己准备？';
  if(c.field==='lease_months')return c.operator==='eq'?`可以租${c.value}个月吗？`:`我希望租期${c.operator==='gte'?'至少':c.operator==='lte'?'不超过':'在'}${Array.isArray(c.value)?c.value.join('到'):c.value}个月，这样可以吗？`;
  if(c.field==='move_in_date')return `我希望${String(c.value).replace(/^(\d{4})-(\d{2})-(\d{2})$/,'$1年$2月$3日')}${c.operator==='lte'?'之前':c.operator==='gte'?'之后':''}入住，时间上合适吗？`;
  if(c.field==='current_availability')return '这套现在还可以租吗？';
  if(c.field==='commute_time')return `从这里去${c.target?.name||c.target?.category||'我上班或上学的地方'}，${c.transport_mode==='transit'?'坐公交和地铁':c.transport_mode==='walking'?'步行':'通勤'}一般要多久？我希望${c.operator==='between'&&Array.isArray(c.value)?c.value.join('到'):c.value}分钟${c.operator==='lte'?'以内':''}能到。`;
  if(c.field==='price')return `我的月租预算是${Array.isArray(c.value)?c.value.join('到'):c.value}新币${c.operator==='lte'?'以内':''}，请问租金可以商量吗？`;
  const friendly=criterionText(c).replace(/，必须满足|，软偏好|，偏好权重 [\d.]+/g,'').replace(/等于\s*/g,'是').replace(/ SGD\/month/g,'新币/月').replace(/ sqft/g,'平方英尺').replace(/ count/g,'间').replace(/\s+m\b/g,'米');
  return `我想了解一下${friendly}，具体情况是怎样的？`;
}
