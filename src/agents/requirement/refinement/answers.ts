import type {RefinementQuestion,RefinementAnswer} from '../../../contracts/requirement-refinement.js';
import {refinementSubmitSchema,unchangedRefinementSummary} from '../../../contracts/requirement-refinement.js';
import type {Requirements} from '../../../contracts/recommendation-v1.js';
import {criterion,mergeRequirements} from '../../../domain/requirements.js';
import {DEFAULT_PROXIMITY_SCALE_M} from '../../../domain/geo-intake.js';
import {requirementDraftSchema} from '../contracts.js';

export function compileRefinement(questions:RefinementQuestion[],input:unknown,base:Requirements){
  const submitted=refinementSubmitSchema.parse(input),seen=new Set<string>(),summary:string[]=[];
  const draft=requirementDraftSchema.parse({upsert:[],remove_fields:[],clarifications:[],sources:[],supported:true});
  const error=(text:string):never=>{throw Error(text);};
  for(const answer of submitted.answers){if(seen.has(answer.question_id)||!questions.some(q=>q.id===answer.question_id))error("An answer is duplicated or does not belong to this card.");seen.add(answer.question_id);}
  const answers:RefinementAnswer[]=questions.map(q=>submitted.answers.find(a=>a.question_id===q.id)||{question_id:q.id,action:'skip'});
  for(const q of questions){
    const a=answers.find(a=>a.question_id===q.id)!;
    if(a.action!=='set'){
      if(Object.keys(a).some(k=>!['question_id','action'].includes(k)))error("No preference or skipped answers cannot include condition values.");
      summary.push(unchangedRefinementSummary(q,a.action));continue;
    }
    const allowed=new Set(['question_id','action',...(q.kind==='bedrooms'?['count','operator','strength']:q.kind==='property_type'?['values','strength']:q.kind==='closer'?[]:q.kind==='ideal_rent'?['max']:q.kind==='budget'?['min','max']:['min','max','unit','strength'])]);
    if(Object.keys(a).some(k=>!allowed.has(k)))error("This answer contains inapplicable fields.");
    if(['bedrooms','property_type','area'].includes(q.kind)&&!a.strength)error("Choose Must have or Nice to have.");
    const soft=a.strength==='soft',importance=soft?"Nice to have":"Must have";let text='';
    if(q.kind==='budget'||q.kind==='ideal_rent'){
      if(a.max===undefined||a.max>1000000||a.min!==undefined&&a.min>a.max)error("Enter a valid monthly rent range; the minimum cannot exceed the maximum.");
      if(q.kind==='ideal_rent'&&(q.upper_bound===undefined||(a.max??Infinity)>q.upper_bound))error("Preferred rent cannot exceed your existing budget cap.");
      const ideal=q.kind==='ideal_rent';draft.upsert.push(criterion('price',a.min!==undefined?'between':'lte',a.min!==undefined?[a.min,a.max!]:a.max!,'SGD/month',ideal));
      text=ideal?`Prefer monthly rent of at most ${a.max} SGD`:`Monthly rent${a.min!==undefined?`${a.min} to ${a.max}`:`at most ${a.max}`} SGD, must have`;
    }else if(q.kind==='bedrooms'){
      if(!a.count||!a.operator)error("Select a bedroom count and Exact, At least or At most.");
      draft.upsert.push(criterion('bedrooms',a.operator!,a.count!,'count',soft));text=`${{eq:"Exactly",gte:"At least",lte:"At most"}[a.operator!]} ${a.count} ${a.count===1?"bedroom":"bedrooms"}, ${importance}`;
    }else if(q.kind==='property_type'){
      if(!a.values?.length||new Set(a.values).size!==a.values.length||a.values.some(v=>!q.options?.some(o=>o.value===v)))error("Select a property type offered by the card.");
      draft.upsert.push(criterion('property_type',a.values!.length===1?'eq':'in',a.values!.length===1?a.values![0]:a.values!,undefined,soft));text=`Property type: ${a.values!.map(v=>q.options!.find(o=>o.value===v)!.label).join(", ")}, ${importance}`;
    }else if(q.kind==='area'){
      if(!a.unit||a.min===undefined&&a.max===undefined||a.min!==undefined&&a.max!==undefined&&a.min>a.max)error("Enter a valid area and unit; the minimum cannot exceed the maximum.");
      const factor=a.unit==='sqm'?10.763910416709722:1,convert=(v:number)=>Math.round(v*factor*1000000)/1000000;
      const value=a.min!==undefined&&a.max!==undefined?[convert(a.min),convert(a.max)]:convert((a.min??a.max)!);
      if((Array.isArray(value)?value:[value]).some(n=>n<=0||n>1000000))error("Area must be positive and within a reasonable range.");
      draft.upsert.push(criterion('floor_area_sqft',Array.isArray(value)?'between':a.min!==undefined?'gte':'lte',value,'sqft',soft));text=`Listed floor area ${a.min!==undefined&&a.max!==undefined?`${a.min} to ${a.max}`:a.min!==undefined?`at least ${a.min}`:`at most ${a.max}`} ${a.unit==='sqm'?'㎡':'sqft'}, ${importance}`;
    }else if(q.kind==='closer'){
      if(!q.target)error("The location information is no longer valid.");
      draft.upsert.push(criterion('place_proximity','lte',DEFAULT_PROXIMITY_SCALE_M,'m',true,{target:q.target,distance_type:q.distance_type,distance_preference:'nearer'}));text=q.title.replace(/^Within the range, would you prefer to be closer to /,'Prefer to be closer to ').replace(/\?$/,'; closer is preferred');
    }
    summary.push(text);draft.sources.push({field:draft.upsert.at(-1)!.field,quote:"Requirement card choice: "+text});
  }
  return {submission_id:submitted.submission_id,answers,summary,draft,requirements:mergeRequirements(base,draft)};
}
