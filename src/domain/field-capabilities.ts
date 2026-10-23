import type {Criterion} from '../contracts/recommendation-v1.js';

export const amenityLabels:Record<string,string>={private_bathroom:'独立卫浴（专用，不与他人共用）',master_room:'主卧房型',common_room:'普通房房型',swimming_pool:'游泳池',gym:'健身房',balcony:'阳台',parking:'停车位',elevator:'电梯',wifi:'网络',washer:'洗衣机',refrigerator:'冰箱',security:'保安',pet_policy:'允许养宠物',cooking_allowed:'允许做饭',air_conditioning:'空调',no_live_in_landlord:'不与房东同住'};
export const valueLabels:Record<string,string>={fully_furnished:'家具齐全',partially_furnished:'部分家具',unfurnished:'无家具',whole_unit:'整租',room:'单间'};
export function unknownReason(c:Criterion):string{
 switch(c.field){
  case 'amenities':return c.value==='private_bathroom'?'数据未收录：当前快照没有独立卫浴或专用使用权信息；卫浴数量不能证明是否共用。':`数据未收录：当前快照没有可核验的“${amenityLabels[String(c.value)]||c.value}”记录。`;
  case 'furnishing':return '数据未收录：当前快照没有家具配置；不能根据照片或房型推断。';
  case 'rental_scope':return '数据未收录：当前快照没有整租/单间标签；卧室数量不代表出租范围。';
  case 'lease_months':return '数据未收录：当前快照没有租客租期；房屋产权年限不是租期。';
  case 'move_in_date':return '数据未收录：当前快照没有可入住日期，需向经纪人确认。';
  case 'commute_time':return '路线工具未接入：没有指定目的地、交通方式和时段的实际路线耗时；直线距离或到地铁站的步行分钟不能代替。';
  case 'current_availability':return '实时状态未核实：历史挂牌、平台认证或抓取成功均不能证明今天仍可租。';
  case 'place_proximity':return c.distance_type!=='straight_line'?'路线工具未接入：当前只能核验直线距离，不能证明步行或驾车路线距离。':'计算依据缺失：房源坐标或目标地点几何信息不可用。';
  default:return '记录值缺失或不可用：当前房源没有该字段的有效记录。';
 }
}
