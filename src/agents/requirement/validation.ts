import {FIELD_RULES,criterionSchema,type Requirements} from '../../contracts/recommendation-v1.js';
import {DEFAULT_PROXIMITY_SCALE_M,PLACE_CATEGORIES} from '../../domain/geo-intake.js';
import {INTAKE_ENUMS,modelIntakeSchema,requirementDraftSchema,type ModelIntake,type RequirementDraft} from './contracts.js';

const ids:Record<string,string>={price:'monthly-rent',bedrooms:'minimum-bedrooms',district:'preferred-district'};
const categoryNames:Record<string,string>={MRTStation:'最近的MRT地铁站',BusStop:'最近的公交站',PublicTransport:'最近的MRT或公交站',Supermarket:'超市',School:'学校',Park:'公园',FoodCourt:'食阁',Mall:'商场',Market:'市场',Hospital:'医院',Clinic:'诊所',Library:'图书馆'};
const SQM_TO_SQFT=10.763910416709722;
function numericConvert(value:ModelIntake['upsert'][number]['value'],factor:number){const f=(v:unknown)=>{if(typeof v!=='number')throw Error('UNIT: numeric conversion requires a number');return Math.round(v*factor*1000000)/1000000;};return Array.isArray(value)?value.map(f):f(value);}

/** Only structural checks, controlled values, arithmetic and provenance. No NLP extraction. */
export function compileIntake(input:unknown,query:string,base:Requirements,meta:{attempts:number;example_ids:string[]}):RequirementDraft{
  const raw=modelIntakeSchema.parse(input),source=(quote:string)=>{if(!query.includes(quote))throw Error('SOURCE_QUOTE: quote must be an exact substring of the supplied user query: '+quote);};
  for(const item of [...raw.upsert,...raw.locations,...raw.removals,...raw.clarifications,...raw.coverage])source(item.quote);
  if(!raw.supported&&!raw.clarifications.length)throw Error('UNSUPPORTED: explain unsupported request in clarifications');
  if(!raw.upsert.length&&!raw.locations.length&&!raw.removals.length&&!raw.clarifications.length&&raw.coverage.some(c=>c.disposition!=='context'))throw Error('COVERAGE: extracted/removed/clarification coverage requires a corresponding output');
  for(const item of [...raw.upsert,...raw.locations,...raw.removals])if(!raw.coverage.some(c=>c.quote.includes(item.quote)))throw Error('COVERAGE: every condition or removal must belong to an accounted user span');
  const draft:RequirementDraft={upsert:[],remove_fields:[],sources:[],clarifications:raw.clarifications.map(c=>c.question),supported:raw.supported,location_intents:[],interpretation_notes:[],remove_criterion_ids:[],extraction:{version:'2.0',mode:'model',...meta,coverage:raw.coverage}};
  for(const r of raw.removals){
    if(r.criterion_id){const old=base.criteria.find(c=>c.criterion_id===r.criterion_id);if(!old||old.field!==r.field)throw Error('REMOVAL: criterion_id must reference an existing criterion of the same field');draft.remove_criterion_ids!.push(r.criterion_id);}
    else if(base.criteria.some(c=>c.field===r.field))draft.remove_fields.push(r.field);
    draft.sources.push({field:r.field,quote:r.quote});
  }
  for(const a of raw.upsert){
    const rule=FIELD_RULES[a.field];let value=a.value,unit=a.unit;
    if(a.field==='floor_area_sqft'||a.field==='land_area_sqft'){if(unit==='sqm'){value=numericConvert(value,SQM_TO_SQFT);unit='sqft';}}
    if(a.field==='mrt_distance_m'&&unit==='km'){value=numericConvert(value,1000);unit='m';}
    if((unit??undefined)!==rule.unit)throw Error(`UNIT: ${a.field} requires ${rule.unit??'null'}, received ${unit}`);
    if(a.strength==='hard'&&a.weight!==null)throw Error('WEIGHT: a hard requirement cannot carry a preference weight');
    const enumeration=INTAKE_ENUMS[a.field as keyof typeof INTAKE_ENUMS] as readonly string[]|undefined;
    if(enumeration)for(const v of Array.isArray(value)?value:[value])if(typeof v!=='string'||!enumeration.includes(v))throw Error(`ENUM: ${a.field} must use a registered value: ${enumeration.join(', ')}`);
    if(a.target_name!==null&&a.field!=='commute_time')throw Error('TARGET: named destinations belong in locations, except commute_time');
    if(a.transport_mode!==null&&a.field!=='commute_time')throw Error('TRANSPORT: transport_mode only belongs to commute_time');
    if(a.field==='commute_time'&&(!a.target_name||!a.transport_mode))throw Error('COMMUTE: preserve destination and explicit/unspecified transport mode');
    const c=criterionSchema.parse({criterion_id:ids[a.field]||a.field,field:a.field,operator:a.operator,value,...(unit?{unit}:{}),strength:a.strength,...(a.strength==='soft'?{weight:a.weight??1}:{}),...(a.target_name?{target:{name:a.target_name}}:{}),...(a.transport_mode?{transport_mode:a.transport_mode}:{}),...(a.field.startsWith('mrt_')?{distance_type:'platform_reported'}:{})});
    draft.upsert.push(c);draft.sources.push({field:a.field,quote:a.quote});
  }
  for(const l of raw.locations){
    if(!!l.name===!!l.categories.length)throw Error('LOCATION: supply either a named destination or categories, never both/neither');
    if(l.strength==='hard'&&l.weight!==null)throw Error('WEIGHT: hard location cannot carry a preference weight');
    if(l.operator==='near'&&(l.strength!=='soft'||l.value!==null||l.unit!==null))throw Error('NEAR: vague nearby is a soft preference with no invented radius');
    if(l.operator!=='near'&&(l.value===null||l.unit===null))throw Error('LOCATION: explicit comparison requires numeric distance and unit');
    if(l.operator==='between'&&(!Array.isArray(l.value)||l.value[0]>l.value[1]))throw Error('LOCATION: between requires an ordered pair');
    if(l.operator!=='between'&&Array.isArray(l.value))throw Error('LOCATION: scalar distance required');
    const factor=l.unit==='km'?1000:1,range=Array.isArray(l.value)?l.value.map(v=>v*factor) as [number,number]:undefined;
    const radius=typeof l.value==='number'?l.value*factor:undefined;
    const categories=[...new Set(l.categories)];let targets:(typeof PLACE_CATEGORIES[number]|null)[];
    if(l.name)targets=[null];
    else if(categories.length<=1||l.relation==='all_of')targets=categories;
    else if(categories.every(c=>['MRTStation','BusStop','PublicTransport'].includes(c)))targets=['PublicTransport'];
    else throw Error('LOCATION_GROUP: this category OR cannot yet be represented; ask for clarification instead of changing OR into AND');
    for(const category of targets)draft.location_intents!.push({name:l.name||categoryNames[category!],quote:l.quote,...(radius!==undefined?{radius_m:radius}:{}),...(range?{range_m:range}:{}),...(l.operator!=='near'?{operator:l.operator}:{}),strength:l.strength,...(l.strength==='soft'?{weight:l.weight??1}:{}),distance_type:l.distance_type,explicit_station:l.explicit_station,prefer_closer:l.prefer_closer,...(category?{category}:{})});
  }
  if(draft.location_intents!.length>12)throw Error('LOCATION_LIMIT: at most 12 separate location relationships');
  // Duplicate semantics are a model error, not permission to silently pick one.
  const seen=new Set<string>();for(const a of draft.upsert){const key=JSON.stringify([a.field,a.operator,a.value,a.strength,a.target,a.transport_mode]);if(seen.has(key))throw Error('DUPLICATE: return each requirement once');seen.add(key);}
  return requirementDraftSchema.parse(draft);
}
