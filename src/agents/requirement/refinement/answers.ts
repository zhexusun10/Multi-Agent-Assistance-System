import type {RefinementQuestion,RefinementAnswer} from '../../../contracts/requirement-refinement.js';
import {refinementSubmitSchema} from '../../../contracts/requirement-refinement.js';
import type {Requirements} from '../../../contracts/recommendation-v1.js';
import {criterion,mergeRequirements} from '../../../domain/requirements.js';
import {DEFAULT_PROXIMITY_SCALE_M} from '../../../domain/geo-intake.js';
import {requirementDraftSchema} from '../contracts.js';

export function compileRefinement(questions:RefinementQuestion[],input:unknown,base:Requirements){
  const submitted=refinementSubmitSchema.parse(input),seen=new Set<string>(),summary:string[]=[];
  const draft=requirementDraftSchema.parse({upsert:[],remove_fields:[],clarifications:[],sources:[],supported:true});
  const error=(text:string):never=>{throw Error(text);};
  for(const answer of submitted.answers){if(seen.has(answer.question_id)||!questions.some(q=>q.id===answer.question_id))error('补充项不属于这张卡片，或存在重复答案。');seen.add(answer.question_id);}
  const answers:RefinementAnswer[]=questions.map(q=>submitted.answers.find(a=>a.question_id===q.id)||{question_id:q.id,action:'skip'});
  for(const q of questions){
    const a=answers.find(a=>a.question_id===q.id)!;
    if(a.action!=='set'){
      if(Object.keys(a).some(k=>!['question_id','action'].includes(k)))error('不限或跳过时不能同时提交条件值。');
      summary.push(q.title+' '+(a.action==='any'?'不限':'暂未补充'));continue;
    }
    const allowed=new Set(['question_id','action',...(q.kind==='bedrooms'?['count','operator','strength']:q.kind==='property_type'?['values','strength']:q.kind==='closer'?[]:q.kind==='ideal_rent'?['max']:q.kind==='budget'?['min','max']:['min','max','unit','strength'])]);
    if(Object.keys(a).some(k=>!allowed.has(k)))error('这项回答包含不适用的字段。');
    if(['bedrooms','property_type','area'].includes(q.kind)&&!a.strength)error('请选择必须满足或有更好。');
    const soft=a.strength==='soft',importance=soft?'有更好':'必须满足';let text='';
    if(q.kind==='budget'||q.kind==='ideal_rent'){
      if(a.max===undefined||a.max>1000000||a.min!==undefined&&a.min>a.max)error('请填写有效的月租范围，下限不能高于上限。');
      if(q.kind==='ideal_rent'&&(q.upper_bound===undefined||(a.max??Infinity)>q.upper_bound))error('理想月租不能超过已有预算上限。');
      const ideal=q.kind==='ideal_rent';draft.upsert.push(criterion('price',a.min!==undefined?'between':'lte',a.min!==undefined?[a.min,a.max!]:a.max!,'SGD/month',ideal));
      text=ideal?`希望月租尽量在 ${a.max} 新币以内`:`月租${a.min!==undefined?`${a.min} 至 ${a.max}`:`不超过 ${a.max}`}新币，必须满足`;
    }else if(q.kind==='bedrooms'){
      if(!a.count||!a.operator)error('请选择卧室数量及恰好、至少或最多。');
      draft.upsert.push(criterion('bedrooms',a.operator!,a.count!,'count',soft));text=`${{eq:'恰好',gte:'至少',lte:'最多'}[a.operator!]} ${a.count} 间卧室，${importance}`;
    }else if(q.kind==='property_type'){
      if(!a.values?.length||new Set(a.values).size!==a.values.length||a.values.some(v=>!q.options?.some(o=>o.value===v)))error('请选择卡片提供的房屋类型。');
      draft.upsert.push(criterion('property_type',a.values!.length===1?'eq':'in',a.values!.length===1?a.values![0]:a.values!,undefined,soft));text=`房屋类型：${a.values!.map(v=>q.options!.find(o=>o.value===v)!.label).join('、')}，${importance}`;
    }else if(q.kind==='area'){
      if(!a.unit||a.min===undefined&&a.max===undefined||a.min!==undefined&&a.max!==undefined&&a.min>a.max)error('请填写有效面积和单位，下限不能高于上限。');
      const factor=a.unit==='sqm'?10.763910416709722:1,convert=(v:number)=>Math.round(v*factor*1000000)/1000000;
      const value=a.min!==undefined&&a.max!==undefined?[convert(a.min),convert(a.max)]:convert((a.min??a.max)!);
      if((Array.isArray(value)?value:[value]).some(n=>n<=0||n>1000000))error('面积需大于零且在合理范围内。');
      draft.upsert.push(criterion('floor_area_sqft',Array.isArray(value)?'between':a.min!==undefined?'gte':'lte',value,'sqft',soft));text=`挂牌面积${a.min!==undefined&&a.max!==undefined?`${a.min} 至 ${a.max}`:a.min!==undefined?`至少 ${a.min}`:`最多 ${a.max}`} ${a.unit==='sqm'?'㎡':'sqft'}，${importance}`;
    }else if(q.kind==='closer'){
      if(!q.target)error('地点信息已失效。');
      draft.upsert.push(criterion('place_proximity','lte',DEFAULT_PROXIMITY_SCALE_M,'m',true,{target:q.target,distance_type:q.distance_type,distance_preference:'nearer'}));text=q.title.replace('在范围内，您希望','希望').replace('吗？','，更近优先');
    }
    summary.push(text);draft.sources.push({field:draft.upsert.at(-1)!.field,quote:'需求卡片选择：'+text});
  }
  return {submission_id:submitted.submission_id,answers,summary,draft,requirements:mergeRequirements(base,draft)};
}
