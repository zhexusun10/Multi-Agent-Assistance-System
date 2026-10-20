import { z } from 'zod';
import type { BaseChatModel } from '@langchain/core/language_models/chat_models';
import { HumanMessage, SystemMessage } from '@langchain/core/messages';
import { criterionSchema, requirementsSchema, FIELD_RULES, type Criterion, type Requirements, type Feedback } from '../contracts/recommendation-v1.js';
import {parseRentalLanguage} from './rental-language.js';
import {locationIntentSchema,isListingReference,parseLocationIntents,DEFAULT_PROXIMITY_SCALE_M} from './geo-intake.js';

export const draftSchema=z.object({
  upsert:z.array(criterionSchema),remove_fields:z.array(z.string()),clarifications:z.array(z.string()).max(3),
  sources:z.array(z.object({field:z.string(),quote:z.string().min(1)}).strict()),
  supported:z.boolean(),
  location_intents:z.array(locationIntentSchema).max(8).optional(),
  interpretation_notes:z.array(z.string()).optional(),
  remove_criterion_ids:z.array(z.string()).optional(),
}).strict();
export type RequirementDraft=z.infer<typeof draftSchema>;
// The model proposes syntax; code supplies the agreed default for unspecified relative importance.
const modelDraftSchema=draftSchema.omit({location_intents:true,interpretation_notes:true,remove_criterion_ids:true}).extend({upsert:z.array(z.object(criterionSchema.shape).strict())});
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
    const target={category:'MRTStation',name:'最近的MRT地铁站'};
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

export async function extractRequirements(query:string,base:Requirements,model?:BaseChatModel,skillText=''):Promise<RequirementDraft>{
  if(!model)return extractRules(query,base);
  const rules=extractRules(query,base);
  // Definite input ambiguity is a valid needs-input outcome, not a failed model schema.
  if(rules.clarifications.some(q=>!q.startsWith('请提供租房月预算')))return rules;
  const system=`你是新加坡住宅租赁需求抽取 Agent。调用 submit_requirement_draft 工具提交结果。未提条件保留，明确取消才 remove_fields。sources 仅含 field 和用户原文 quote；supported 是布尔值。可省略的属性不要设为 null。soft 需要正权重，hard 不允许 weight。裸“两房”先澄清至少还是恰好。字段单位 price=SGD/month,bedrooms/bathrooms=count,floor_area_sqft/land_area_sqft=sqft,build_year=year,place_proximity/mrt_distance_m=m,mrt_walking_mins/commute_time=min,lease_months=month。district=D01-D28；source property_type 精确目录含 Condominium,Apartment,HDB Flat,Walk-up Apartment,Terraced House,Semi-Detached House,Detached House,Bungalow House,Corner Terrace,Shophouse,Good Class Bungalow。1B1B、2BR2BA、一室一卫等明确户型按恰好卧室数和卫浴数解析；裸“两房”才澄清。独卫与卫浴数量、出租范围互不推断。没有数据的租期、家具、整租、可租状态等诉求仍保留为条件，由工具判未知，不是输入歧义，不要因为无法验证就反复澄清。不能用产权 tenure 推断租期。无法验证实际路线时仍保留用户诉求，但不证明符合。“附近”默认更近优先的软偏好，不要求用户提供半径、地标或距离口径。具名地点和半径由代码地点解析器处理，默认直线距离并告知，不追问范围；只有实际地点不存在或多义才澄清。你不要生成具名地点的 place_proximity 或 location_intents，由代码补齐，不能编ID。remove_criterion_ids 也只由代码设置。公交方式补充要更新原 commute_time 而不丢失原30分钟上限。买房 supported=false。重述同一条件保留原 criterion_id。只抽取本次消息的增删改，不将全量旧条件重造为新变化。当前需求=${JSON.stringify(base)}`;
  const structured=model.withStructuredOutput(modelDraftSchema,{name:'submit_requirement_draft',method:'functionCalling'});
  let repair='';
  for(let attempt=0;attempt<2;attempt++){
    try{
      const proposal=await structured.invoke([new SystemMessage(skillText+'\n'+system+'\n代码已确定的字面草案（不能覆盖其值、比较符、单位、删除或增加澄清）：'+JSON.stringify(rules)+'\n允许字段及单位的唯一登记表：'+JSON.stringify(FIELD_RULES)+'\n独立卫浴使用 amenities eq private_bathroom，不能用 bathrooms>=1 证明，也不能增加 bathroom_type 等字段。通勤必须使用 commute_time 和 min；未指定交通方式时 transport_mode=unspecified。不生成占位地点、半径、Mall 类别、place_id 或邮区。软偏好未指定相对重要性时 weight=1。'+repair),new HumanMessage(query)],{signal:AbortSignal.timeout(30000)});
      // Literal semantics are owned by code. A model may add uncovered dimensions,
      // but cannot mutate exact layouts, money, units or known enum mappings.
      const checked=modelDraftSchema.parse(proposal);
      const codeGeo=!!rules.location_intents?.length||rules.upsert.some(c=>c.field==='place_proximity');
      const owned=new Set([...rules.upsert.map(c=>c.field),...rules.remove_fields]);
      const proposed=checked.upsert.filter(c=>!owned.has(c.field)&&!(codeGeo&&['place_proximity','mrt_distance_m','mrt_walking_mins'].includes(c.field))).filter(c=>{
        if(c.field==='amenities'&&rules.remove_criterion_ids?.some(id=>base.criteria.some(old=>old.criterion_id===id&&old.value===c.value)))return false;
        if(c.field==='amenities'&&c.value==='private_bathroom')return /独立卫浴|独立卫生间|独立浴室|独卫|private\s+(?:bathroom|bath)|en[ -]?suite/i.test(query)&&!/(?:不要求|不需要|取消).{0,3}(?:独立卫浴|独卫)/.test(query);
        if(c.field==='rental_scope')return /整租|整套|单间|普通房|主卧|whole unit|entire unit|common room|master room/i.test(query);
        return true;
      }).map(c=>{const {unit,...rest}=c;return {...rest,...(FIELD_RULES[c.field].unit?{unit:FIELD_RULES[c.field].unit}:{})};});
      const hasLiteral=rules.upsert.length>0||!!rules.location_intents?.length||rules.remove_fields.length>0||!!rules.remove_criterion_ids?.length;
      const d=draftSchema.parse({...checked,upsert:[...rules.upsert,...proposed],remove_fields:[...new Set([...rules.remove_fields,...checked.remove_fields.filter(f=>!owned.has(f))])],location_intents:rules.location_intents,interpretation_notes:rules.interpretation_notes||[],remove_criterion_ids:rules.remove_criterion_ids||[],sources:[...rules.sources,...checked.sources],supported:checked.supported&&rules.supported,clarifications:hasLiteral?rules.clarifications:checked.clarifications});
      for(const c of d.upsert)if(!d.sources.some(s=>s.field===c.field&&query.includes(s.quote)))throw Error('EXTRACTION: source quote missing from user message');
      for(const f of d.remove_fields)if(!d.sources.some(s=>s.field===f&&query.includes(s.quote)))throw Error('EXTRACTION: removal without user evidence');
      for(const f of d.remove_fields)if(!rules.remove_fields.includes(f)&&!(/只保留.*预算|only keep.*budget/i.test(query)&&f!=='price'))throw Error('EXTRACTION: removal is not explicitly authorized');
      return d;
    }catch(error){
      const text=error instanceof Error?error.message:'';
      if(attempt||(!(error instanceof z.ZodError)&&!text.includes('EXTRACTION:')&&!text.includes('parse')&&!text.includes('JSON')))throw error;
      const details=error instanceof z.ZodError?error.issues.map(i=>({path:i.path,message:i.message})):text.startsWith('EXTRACTION:')?text:'结构化提案不能解析，请仅使用登记表字段。';
      repair='\n上次输出未通过严格校验，具体问题（校验数据，不是新指令）='+JSON.stringify(details)+'。请完整重提合法草案，不使用占位条件，sources 不增加属性，supported 为 boolean，逐字引用用户输入，明确金额/单位/比较方式不能遗漏或更改。';
    }
  }
  throw Error('Requirement extraction failed validation');
}
