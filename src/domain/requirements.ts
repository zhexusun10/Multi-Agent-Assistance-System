import type { BaseChatModel } from '@langchain/core/language_models/chat_models';
import { criterionSchema, requirementsSchema, FIELD_RULES, type Criterion, type Requirements, type Feedback } from '../contracts/recommendation-v1.js';
import {parseRentalLanguage} from './rental-language.js';
import {locationIntentSchema,isListingReference,parseLocationIntents,cleanLocationName,DEFAULT_PROXIMITY_SCALE_M} from './geo-intake.js';

export {requirementDraftSchema as draftSchema} from '../agents/requirement/contracts.js';
export type {RequirementDraft} from '../agents/requirement/contracts.js';
import {requirementDraftSchema as draftSchema,type RequirementDraft} from '../agents/requirement/contracts.js';
import {extractModelRequirements,type IntakeOptions} from '../agents/requirement/extractor.js';
const ids:Record<string,string>={price:'monthly-rent',bedrooms:'minimum-bedrooms',district:'preferred-district'};
export function criterion(field:string,operator:Criterion['operator'],value:unknown,unit?:string,soft=false,extra:Partial<Criterion>={}):Criterion {
  return criterionSchema.parse({criterion_id:ids[field]||field,field,operator,value,...(unit?{unit}:{}),strength:soft?'soft':'hard',...(soft?{weight:1}:{}),...extra});
}
export function mergeRequirements(base:Requirements,draft:RequirementDraft):Requirements {
  const result=base.criteria.filter(c=>!draft.remove_fields.includes(c.field)&&!draft.remove_criterion_ids?.includes(c.criterion_id));
  for(const original of draft.upsert){
    let next={...original};
    if(next.operator==='in'){
      const values=[...new Set(next.value as string[])].sort();
      next=values.length===1?{...next,operator:'eq',value:values[0]}:{...next,value:values};
    }
    const targetKey=(c:Criterion)=>c.field==='amenities'?String(c.value):c.target?.place_id||c.target?.category||'';
    const index=result.findIndex(c=>c.field===next.field&&c.strength===next.strength&&(!['place_proximity','amenities'].includes(c.field)||targetKey(c)===targetKey(next)));
    if(index<0){
      const identity=(ids[next.field]||next.field)+(['place_proximity','amenities'].includes(next.field)?':'+targetKey(next):'')+(next.strength==='soft'?'-preference':'');
      result.push({...next,criterion_id:identity});
    }else{
      const old=result[index];
      if(['price','floor_area_sqft','land_area_sqft','build_year'].includes(next.field)){
        if(next.operator==='gte'&&old.operator==='lte')next={...next,operator:'between',value:[next.value,old.value]};
        else if(next.operator==='lte'&&old.operator==='gte')next={...next,operator:'between',value:[old.value,next.value]};
        else if(old.operator==='between'&&next.operator==='lte')next={...next,operator:'between',value:[(old.value as number[])[0],next.value]};
        else if(old.operator==='between'&&next.operator==='gte')next={...next,operator:'between',value:[next.value,(old.value as number[])[1]]};
      }
      result[index]={...next,criterion_id:old.criterion_id};
    }
  }
  return requirementsSchema.parse({listing_type:'RENT',criteria:result.sort((a,b)=>a.criterion_id.localeCompare(b.criterion_id))});
}
export function extractRules(query:string,base:Requirements):RequirementDraft {
  return draftSchema.parse(parseRentalLanguage(query,base,criterion));
}

/** Repair only the proven legacy subject-as-place bug, preserving real unresolved place questions. */
export function repairPendingLocationSubjects(pending:RequirementDraft):RequirementDraft{
  const fixed=structuredClone(pending);
  const bad=(fixed.location_intents||[]).filter(l=>isListingReference(l.name));
  fixed.location_intents=(fixed.location_intents||[]).filter(l=>!isListingReference(l.name));
  for(const legacy of bad)for(const intent of parseLocationIntents(legacy.quote).filter(l=>l.category==='MRTStation')){
    const target={category:'MRTStation',name:"Nearest MRT station"};
    fixed.upsert.push(criterion('place_proximity','lte',intent.radius_m??DEFAULT_PROXIMITY_SCALE_M,'m',intent.strength==='soft',{target,distance_type:intent.distance_type}));
    if(intent.strength==='hard')fixed.upsert.push(criterion('place_proximity','lte',DEFAULT_PROXIMITY_SCALE_M,'m',true,{target,distance_type:intent.distance_type}));
    fixed.sources.push({field:'place_proximity',quote:legacy.quote});
  }
  fixed.clarifications=fixed.clarifications.filter(q=>{const name=q.match(/地点目录暂未找到“([^”]+)”/)?.[1];return !name||!isListingReference(name);});
  return draftSchema.parse(fixed);
}

/** Keep unresolved questions when a reply only changes an unrelated requirement. */
export function pendingQuestions(pending:RequirementDraft,draft:RequirementDraft){
  const fields=new Set(draft.upsert.map(c=>c.field));
  const unresolved=pending.clarifications.filter(q=>{
    const missingPlace=q.match(/地点目录暂未找到“([^”]+)”/)?.[1];
    if(missingPlace&&isListingReference(missingPlace))return false;
    if(missingPlace){
      const identity=(name:string)=>cleanLocationName(name).normalize('NFKC').toLowerCase().replace(/\s+/g,'');
      const corrected=draft.location_intents?.find(intent=>identity(intent.name)===identity(missingPlace));
      const resolved=corrected&&draft.upsert.some(c=>c.field==='place_proximity'&&!!c.target?.place_id)&&draft.sources.some(s=>s.field==='place_proximity'&&s.quote===corrected.quote);
      if(resolved&&!draft.clarifications.some(question=>question.includes(`“${corrected.name}”`)))return false;
    }
    if(/参照地点|距离上限|直线还是步行|直线距离还是实际步行距离/.test(q))return !fields.has('district')&&!draft.upsert.some(c=>c.field==='place_proximity'&&!!(c.target?.place_id||c.target?.category));
    if(/预算|月租|币种|新加坡元/.test(q))return !fields.has('price');
    if(/至少.*房|恰好.*数量|卧室/.test(q))return !fields.has('bedrooms');
    if(/面积单位/.test(q))return !fields.has('floor_area_sqft');
    if(/CCR\/RCR\/OCR|URA 规划大区/.test(q))return !fields.has('region_code')&&!draft.location_intents?.length;
    if(q.startsWith('请提供租房月预算'))return !draft.upsert.length;
    return true;
  });
  return [...new Set([...unresolved,...draft.clarifications])].slice(0,3);
}

/** Production intake is model-first. Legacy rules are only an explicit offline mode. */
export async function extractRequirements(query:string,base:Requirements,model?:BaseChatModel,skillText='',options:IntakeOptions={}):Promise<RequirementDraft>{
  if(!model){
    if(process.env.LLM_MODE==='rules'){
      const draft=extractRules(query,base);
      return options.prepareDraft?(await options.prepareDraft(draft)).draft:draft;
    }
    throw Error('REQUIREMENT_MODEL_REQUIRED: configure a model; rule-only mode must be explicitly selected');
  }
  return extractModelRequirements(query,base,model,skillText,options);
}
