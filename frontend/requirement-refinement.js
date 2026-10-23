// Explicit form choices are sent as structured answers, never as an LLM prompt.
export class RequirementRefinementUI{
  constructor(deps){Object.assign(this,deps);this.cards=new Map();}
  mount(id,parent){
    const plan=this.getState()?.refinement_plans?.find(p=>p.id===id);if(!plan)return;
    const root=this.el('section','requirement-refinement');root.dataset.refinementId=id;root.setAttribute('aria-label','补充找房偏好');parent.append(root);
    const key='rental.refinement.'+this.scope+'.'+plan.session_id+'.'+id;let draft={index:0,answers:{}};
    try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved&&saved.answers&&Number.isInteger(saved.index))draft=saved;}catch{}
    draft.index=Math.max(0,Math.min(plan.questions.length-1,draft.index));
    const card={root,plan,key,draft,busy:false,view:null};this.cards.set(id,card);this.paint(card);
  }
  save(card){try{localStorage.setItem(card.key,JSON.stringify(card.draft));}catch{/* Form remains usable with storage disabled. */}}
  active(card){const s=this.getState();return s?.session_id===card.plan.session_id&&s.latest_request_id===card.plan.source_request_id&&s.requirements_version===card.plan.requirements_version&&s.active_operation_id===card.plan.source_operation_id;}
  sync(){for(const [id,card] of this.cards){if(!card.root.isConnected){this.cards.delete(id);continue;}const next=this.getState()?.refinement_plans?.find(p=>p.id===id);if(next)card.plan=next;const view=card.plan.status==='OPEN'&&!this.active(card)?'STALE':card.plan.status;if(card.view!==view)this.paint(card);}}
  btn(text,fn,cls=''){const b=this.el('button',cls,text);b.type='button';b.onclick=fn;return b;}
  paint(card){
    const {root,plan}=card;root.replaceChildren();card.view=plan.status==='OPEN'&&!this.active(card)?'STALE':plan.status;
    root.append(this.el('h4','','再补充一点找房偏好'));
    if(card.view!=='OPEN'){
      root.append(this.el('p','refinement-status',card.view==='SUBMITTED'?'已记录您的选择':card.view==='DISMISSED'?'暂不补充':'找房条件已更新，这张卡片已结束'));
      const lines=plan.answer_summary?.length?plan.answer_summary:plan.questions.map(q=>this.describe(q,card.draft.answers[q.id])).filter(Boolean);
      if(lines.length){const list=this.el('ul','refinement-summary');for(const line of lines)list.append(this.el('li','',line));root.append(list);}return;
    }
    const q=plan.questions[card.draft.index],answer=card.draft.answers[q.id]||{};
    root.append(this.el('p','refinement-progress',`${card.draft.index+1} / ${plan.questions.length}`),this.el('p','refinement-question',q.title));
    if(q.help)root.append(this.el('p','refinement-help',q.help));
    const choices=this.el('div','refinement-choices');
    const choose=action=>{card.draft.answers[q.id]={...card.draft.answers[q.id],action};this.save(card);this.paint(card);};
    for(const [action,label] of [['set',q.kind==='closer'?'更近优先':q.kind==='ideal_rent'?'填写理想月租':'设置条件'],['any',q.kind==='closer'?'范围内即可':q.kind==='ideal_rent'?'目前预算即可':'不限'],['skip','暂未想好']]){
      const b=this.btn(label,()=>choose(action));b.setAttribute('aria-pressed',String(answer.action===action));choices.append(b);
    }root.append(choices);
    const update=(key,value)=>{card.draft.answers[q.id]={...card.draft.answers[q.id],action:'set',[key]:value};this.save(card);error.textContent='';updateSummary();};
    if(answer.action==='set'&&q.kind!=='closer'){
      const fields=this.el('div','refinement-fields');
      const select=(label,key,options)=>{const wrap=this.el('label','',label),input=this.el('select');input.setAttribute('aria-label',label);for(const [value,text] of options){const o=this.el('option','',text);o.value=value;input.append(o);}input.value=answer[key]??(key==='unit'?'sqm':'');input.onchange=()=>update(key,input.value);wrap.append(input);fields.append(wrap);};
      const number=(label,key,min=0)=>{const wrap=this.el('label','',label),input=this.el('input');input.type='number';input.min=String(min);input.step=q.kind==='area'?'0.1':'1';input.inputMode='decimal';input.value=answer[key]??'';input.setAttribute('aria-label',label);input.oninput=()=>update(key,input.value);wrap.append(input);fields.append(wrap);};
      if(q.kind==='budget'){number('月租下限（选填，新币）','min');number('月租上限（新币）','max',1);}
      if(q.kind==='ideal_rent')number('理想月租上限（新币）','max',1);
      if(q.kind==='bedrooms'){select('数量要求','operator',[['','请选择'],['eq','恰好'],['gte','至少'],['lte','最多']]);number('卧室数量','count',1);}
      if(q.kind==='property_type')for(const option of q.options||[]){const label=this.el('label','refinement-checkbox'),input=this.el('input');input.type='checkbox';input.checked=answer.values?.includes(option.value)||false;input.onchange=()=>update('values',[...fields.querySelectorAll('input:checked')].map(i=>i.value));input.value=option.value;label.append(input,document.createTextNode(option.label));fields.append(label);}
      if(q.kind==='area'){number('面积下限（选填）','min',.1);number('面积上限（选填）','max',.1);select('面积单位','unit',[['sqm','㎡'],['sqft','sqft']]);}
      if(['bedrooms','property_type','area'].includes(q.kind))select('重要程度','strength',[['','请选择'],['hard','必须满足'],['soft','有更好']]);
      root.append(fields);
    }
    const error=this.el('p','refinement-error');error.setAttribute('role','alert');root.append(error);
    const summary=this.el('ul','refinement-summary');const last=card.draft.index===plan.questions.length-1;
    const updateSummary=()=>{if(!last)return;summary.replaceChildren();for(const question of plan.questions)summary.append(this.el('li','',this.describe(question,card.draft.answers[question.id])));};
    if(last){root.append(this.el('p','refinement-review-title','本次补充'),summary);updateSummary();}
    const nav=this.el('div','refinement-navigation'),prev=this.btn('← 上一项',()=>{card.draft.index--;this.save(card);this.paint(card);});prev.disabled=card.draft.index===0;
    const hasChoices=plan.questions.some(q=>card.draft.answers[q.id]?.action==='set');
    const next=this.btn(last?(hasChoices?'补充好了，重新推荐':'保留当前推荐'):'下一项 →',async()=>{
      try{this.answer(q,card.draft.answers[q.id]);if(last)await this.submit(card);else{card.draft.index++;this.save(card);this.paint(card);}}catch(e){error.textContent=e.message;}
    },last?'refinement-primary':'');nav.append(prev,next);root.append(nav,this.btn('暂不补充',()=>this.dismiss(card),'refinement-dismiss'));
    if(card.busy)for(const control of root.querySelectorAll('button,input,select'))control.disabled=true;
  }
  answer(q,a={}){
    if(a.action!=='set')return {question_id:q.id,action:a.action==='any'?'any':'skip'};
    const result={question_id:q.id,action:'set'},numeric=key=>{if(a[key]===undefined||a[key]==='')return undefined;const n=Number(a[key]);if(!Number.isFinite(n)||n<0)throw Error('请输入有效的数字。');return n;};
    if(q.kind==='bedrooms'){const count=numeric('count');if(!Number.isInteger(count)||count<1||count>20||!['eq','gte','lte'].includes(a.operator))throw Error('请选择卧室数量及数量要求。');Object.assign(result,{count,operator:a.operator});}
    if(q.kind==='property_type'){if(!a.values?.length)throw Error('请至少选择一种房屋类型。');result.values=a.values;}
    if(['budget','ideal_rent','area'].includes(q.kind)){
      const min=q.kind==='ideal_rent'?undefined:numeric('min'),max=numeric('max');
      if(q.kind==='area'?(min===undefined&&max===undefined):max===undefined)throw Error(q.kind==='area'?'请至少填写面积下限或上限。':'请填写月租上限。');
      if(min!==undefined&&max!==undefined&&min>max)throw Error('下限不能高于上限。');if(max!==undefined&&max<=0||q.kind==='area'&&min!==undefined&&min<=0)throw Error('数值需大于零。');
      if(q.kind==='ideal_rent'&&max>q.upper_bound)throw Error('理想月租不能超过原预算上限。');
      if(min!==undefined)result.min=min;if(max!==undefined)result.max=max;if(q.kind==='area')result.unit=a.unit||'sqm';
    }
    if(['bedrooms','property_type','area'].includes(q.kind)){if(!['hard','soft'].includes(a.strength))throw Error('请选择必须满足或有更好。');result.strength=a.strength;}
    return result;
  }
  describe(q,a){
    if(!a?.action||a.action==='skip')return q.title+' 暂未补充';if(a.action==='any')return q.title+' '+(q.kind==='closer'?'范围内即可':q.kind==='ideal_rent'?'保留原预算':'不限');
    const level=a.strength==='hard'?'必须满足':a.strength==='soft'?'有更好':'未选择重要程度';
    if(q.kind==='bedrooms')return `卧室：${{eq:'恰好',gte:'至少',lte:'最多'}[a.operator]||''}${a.count||'待填写'}间，${level}`;
    if(q.kind==='property_type')return `房屋类型：${a.values?.map(v=>q.options.find(o=>o.value===v)?.label).join('、')||'待选择'}，${level}`;
    if(q.kind==='closer')return q.title.replace('您希望','希望').replace('吗？','，更近优先');
    if(q.kind==='area')return `挂牌面积：${a.min?`至少${a.min}`:''}${a.min&&a.max?'，':''}${a.max?`最多${a.max}`:''}${a.unit==='sqft'?' sqft':'㎡'}，${level}`;
    return `${q.kind==='ideal_rent'?'理想月租':'月租'}：${a.min?`${a.min} 至 `:'最多'}${a.max||'待填写'}新币${q.kind==='ideal_rent'?'，有更好':'，必须满足'}`;
  }
  async submit(card){
    if(card.busy||!this.active(card))throw Error('条件已更新，请使用最新卡片。');
    const answers=card.plan.questions.map(q=>this.answer(q,card.draft.answers[q.id]));
    card.busy=true;this.paint(card);card.draft.submission_id||=crypto.randomUUID();this.save(card);
    try{await this.beforeSubmit();const result=await this.api(`refinements/${card.plan.id}/submit`,{session_id:card.plan.session_id,submission_id:card.draft.submission_id,answers});await this.onSubmitted(result);}
    catch(e){card.busy=false;this.paint(card);card.root.querySelector('.refinement-error').textContent=e.message;return;}
    card.busy=false;this.sync();
  }
  async dismiss(card){
    if(card.busy)return;card.busy=true;this.paint(card);
    try{await this.api(`refinements/${card.plan.id}/dismiss`,{session_id:card.plan.session_id});await this.onSubmitted({status:'dismissed'});}catch(e){card.busy=false;this.paint(card);card.root.querySelector('.refinement-error').textContent=e.message;return;}
    card.busy=false;this.sync();
  }
}
