// Explicit form choices are sent as structured answers, never as an LLM prompt.
export class RequirementRefinementUI{
  constructor(deps){Object.assign(this,deps);this.cards=new Map();}
  mount(id,parent){
    const plan=this.getState()?.refinement_plans?.find(p=>p.id===id);if(!plan)return;
    const root=this.el('section','requirement-refinement');root.dataset.refinementId=id;root.setAttribute('aria-label',"Refine your rental preferences");parent.append(root);
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
    root.append(this.el('h4','',"Refine your search"));
    if(card.view!=='OPEN'){
      root.append(this.el('p','refinement-status',card.view==='SUBMITTED'?"Your choices have been saved":card.view==='DISMISSED'?"Not now":"Your requirements have changed. This card is now closed."));
      const lines=plan.answer_summary?.length?plan.answer_summary:plan.questions.map(q=>this.describe(q,card.draft.answers[q.id])).filter(Boolean);
      if(lines.length){const list=this.el('ul','refinement-summary');for(const line of lines)list.append(this.el('li','',line));root.append(list);}return;
    }
    const q=plan.questions[card.draft.index],answer=card.draft.answers[q.id]||{};
    root.append(this.el('p','refinement-progress',`${card.draft.index+1} / ${plan.questions.length}`),this.el('p','refinement-question',q.title));
    if(q.help)root.append(this.el('p','refinement-help',q.help));
    const choices=this.el('div','refinement-choices');
    const choose=action=>{card.draft.answers[q.id]={...card.draft.answers[q.id],action};this.save(card);this.paint(card);};
    for(const [action,label] of [['set',q.kind==='closer'?"Prefer closer":q.kind==='ideal_rent'?"Set a preferred rent":"Set a requirement"],['any',q.kind==='closer'?"Anywhere within the range":q.kind==='ideal_rent'?"Keep my current budget":"No preference"],['skip',"Not sure yet"]]){
      const b=this.btn(label,()=>choose(action));b.setAttribute('aria-pressed',String(answer.action===action));choices.append(b);
    }root.append(choices);
    const update=(key,value)=>{card.draft.answers[q.id]={...card.draft.answers[q.id],action:'set',[key]:value};this.save(card);error.textContent='';updateSummary();};
    if(answer.action==='set'&&q.kind!=='closer'){
      const fields=this.el('div','refinement-fields');
      const select=(label,key,options)=>{const wrap=this.el('label','',label),input=this.el('select');input.setAttribute('aria-label',label);for(const [value,text] of options){const o=this.el('option','',text);o.value=value;input.append(o);}input.value=answer[key]??(key==='unit'?'sqm':'');input.onchange=()=>update(key,input.value);wrap.append(input);fields.append(wrap);};
      const number=(label,key,min=0)=>{const wrap=this.el('label','',label),input=this.el('input');input.type='number';input.min=String(min);input.step=q.kind==='area'?'0.1':'1';input.inputMode='decimal';input.value=answer[key]??'';input.setAttribute('aria-label',label);input.oninput=()=>update(key,input.value);wrap.append(input);fields.append(wrap);};
      if(q.kind==='budget'){number("Minimum monthly rent (optional, SGD)",'min');number("Maximum monthly rent (SGD)",'max',1);}
      if(q.kind==='ideal_rent')number("Preferred maximum rent (SGD)",'max',1);
      if(q.kind==='bedrooms'){select("Number requirement",'operator',[['',"Please select"],['eq',"Exactly"],['gte',"At least"],['lte',"At most"]]);number("Number of bedrooms",'count',1);}
      if(q.kind==='property_type')for(const option of q.options||[]){const label=this.el('label','refinement-checkbox'),input=this.el('input');input.type='checkbox';input.checked=answer.values?.includes(option.value)||false;input.onchange=()=>update('values',[...fields.querySelectorAll('input:checked')].map(i=>i.value));input.value=option.value;label.append(input,document.createTextNode(option.label));fields.append(label);}
      if(q.kind==='area'){number("Minimum area (optional)",'min',.1);number("Maximum area (optional)",'max',.1);select("Area unit",'unit',[['sqm','㎡'],['sqft','sqft']]);}
      if(['bedrooms','property_type','area'].includes(q.kind))select("Importance",'strength',[['',"Please select"],['hard',"Must have"],['soft',"Nice to have"]]);
      root.append(fields);
    }
    const error=this.el('p','refinement-error');error.setAttribute('role','alert');root.append(error);
    const summary=this.el('ul','refinement-summary');const last=card.draft.index===plan.questions.length-1;
    const updateSummary=()=>{if(!last)return;summary.replaceChildren();for(const question of plan.questions)summary.append(this.el('li','',this.describe(question,card.draft.answers[question.id])));};
    if(last){root.append(this.el('p','refinement-review-title',"Your choices"),summary);updateSummary();}
    const nav=this.el('div','refinement-navigation'),prev=this.btn("← Previous",()=>{card.draft.index--;this.save(card);this.paint(card);});prev.disabled=card.draft.index===0;
    const hasChoices=plan.questions.some(q=>card.draft.answers[q.id]?.action==='set');
    const next=this.btn(last?(hasChoices?"Apply choices and recommend again":"Keep current recommendations"):"Next →",async()=>{
      try{this.answer(q,card.draft.answers[q.id]);if(last)await this.submit(card);else{card.draft.index++;this.save(card);this.paint(card);}}catch(e){error.textContent=e.message;}
    },last?'refinement-primary':'');nav.append(prev,next);root.append(nav,this.btn("Not now",()=>this.dismiss(card),'refinement-dismiss'));
    if(card.busy)for(const control of root.querySelectorAll('button,input,select'))control.disabled=true;
  }
  answer(q,a={}){
    if(a.action!=='set')return {question_id:q.id,action:a.action==='any'?'any':'skip'};
    const result={question_id:q.id,action:'set'},numeric=key=>{if(a[key]===undefined||a[key]==='')return undefined;const n=Number(a[key]);if(!Number.isFinite(n)||n<0)throw Error("Enter a valid number.");return n;};
    if(q.kind==='bedrooms'){const count=numeric('count');if(!Number.isInteger(count)||count<1||count>20||!['eq','gte','lte'].includes(a.operator))throw Error("Choose a bedroom count and whether it is exact, minimum or maximum.");Object.assign(result,{count,operator:a.operator});}
    if(q.kind==='property_type'){if(!a.values?.length)throw Error("Select at least one property type.");result.values=a.values;}
    if(['budget','ideal_rent','area'].includes(q.kind)){
      const min=q.kind==='ideal_rent'?undefined:numeric('min'),max=numeric('max');
      if(q.kind==='area'?(min===undefined&&max===undefined):max===undefined)throw Error(q.kind==='area'?"Enter a minimum or maximum area.":"Enter a maximum monthly rent.");
      if(min!==undefined&&max!==undefined&&min>max)throw Error("The minimum cannot exceed the maximum.");if(max!==undefined&&max<=0||q.kind==='area'&&min!==undefined&&min<=0)throw Error("The value must be greater than zero.");
      if(q.kind==='ideal_rent'&&max>q.upper_bound)throw Error("The preferred rent cannot exceed your existing budget.");
      if(min!==undefined)result.min=min;if(max!==undefined)result.max=max;if(q.kind==='area')result.unit=a.unit||'sqm';
    }
    if(['bedrooms','property_type','area'].includes(q.kind)){if(!['hard','soft'].includes(a.strength))throw Error("Choose Must have or Nice to have.");result.strength=a.strength;}
    return result;
  }
  describe(q,a){
    if(!a?.action||a.action==='skip')return q.title+" Not provided yet";if(a.action==='any')return q.kind==='ideal_rent'?`Keep the existing budget${q.upper_bound?` limit of ${q.upper_bound.toLocaleString('en-SG')} SGD`:''}; no additional preferred-rent condition`:q.title+' '+(q.kind==='closer'?"Anywhere within the range":"No preference");
    const level=a.strength==='hard'?"Must have":a.strength==='soft'?"Nice to have":"Importance not selected";
    if(q.kind==='bedrooms')return `Bedrooms: ${{eq:"Exactly",gte:"At least",lte:"At most"}[a.operator]||''} ${a.count||"Not entered"} ${Number(a.count)===1?"bedroom":"bedrooms"}, ${level}`;
    if(q.kind==='property_type')return `Property type: ${a.values?.map(v=>q.options.find(o=>o.value===v)?.label).join(", ")||"Not selected"}, ${level}`;
    if(q.kind==='closer')return q.title.replace(/^Within the range, would you prefer to be closer to /,'Prefer to be closer to ').replace(/\?$/,'; closer is preferred');
    if(q.kind==='area')return `Listed area: ${a.min?`At least${a.min}`:''}${a.min&&a.max?", ":''}${a.max?`At most${a.max}`:''}${a.unit==='sqft'?' sqft':'㎡'}, ${level}`;
    return `${q.kind==='ideal_rent'?"Preferred rent":"Monthly rent"}: ${a.min?`${a.min} to `:"At most"}${a.max||"Not entered"} SGD${q.kind==='ideal_rent'?", nice to have":", must have"}`;
  }
  async submit(card){
    if(card.busy||!this.active(card))throw Error("Your requirements have changed. Please use the latest card.");
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
