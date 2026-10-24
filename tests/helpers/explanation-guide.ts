import type {RecommendationResponse,Requirements} from '../../src/contracts/recommendation-v1.js';
import type {ExplanationOutput} from '../../src/agents/explanation/contracts.js';
import {makeExplanationBundle} from '../../src/agents/explanation/evidence.js';
import {addDecisionSupport} from '../../src/agents/explanation/decision-support.js';
/** Offline model stub only. Production text is always written by the model. */
export function guideResponse(req:Requirements,rec:RecommendationResponse,question='解释',english=false):ExplanationOutput{
 const b=addDecisionSupport(makeExplanationBundle(req,rec,question),req,rec),g=b.decision_support!;
 return {version:'2.0',focus_listing_ids:g.target_listing_ids,paragraphs:[
  {listing_id:null,text:english?'These details may help you choose:':'以下信息可能帮助您比较这几套房子：',support_refs:['guide:purpose']},
  ...g.profiles.filter(p=>g.target_listing_ids.includes(p.listing_id)).map(p=>{
   const row=[...rec.recommendations,...rec.unverified_candidates].find(r=>r.listing.listing_id===p.listing_id)!;
   const extra=p.additional_refs[0]||p.extra_refs[0],area=(p.core_refs||[]).find(ref=>ref.startsWith('area:')||ref.endsWith(':floor_area_sqft'));
   const sqft=b.facts.find(f=>f.ref===area)?.numbers[0];
   const detail=sqft?(english?` The listed floor area is ${sqft} sq ft.`:`房源面积${sqft}平方英尺。`):'';
   const supplementary=extra&&!p.core_refs?.includes(extra)?(english?' The recorded property type is a condominium.':'记录房屋类型为公寓。'):'';
   const unknown=p.caution_refs.length?(english?' Some required conditions still need confirmation.':'部分必须要求尚需确认。'):'';
   return {listing_id:p.listing_id,text:(english?`${p.position}. ${p.title}: Recorded monthly rent is S$${row.listing.price}.`:`第${p.position}套 ${p.title}：记录月租${row.listing.price}新币。`)+detail+supplementary+unknown,support_refs:[...p.known_refs.slice(0,1),...p.core_refs||[],...(extra?[extra]:[]),...p.caution_refs]};
  }),
  {listing_id:null,text:english?'Please confirm current rent and availability.':'当前租金和是否仍可租，请联系确认。',support_refs:b.required_refs},
 ]};
}
