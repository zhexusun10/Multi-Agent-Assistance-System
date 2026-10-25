// Local date values stay in Asia/Singapore. Selection is staged until explicitly confirmed.
const pad=n=>String(n).padStart(2,'0');
export function validDate(value){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value||''))return false;
  const [year,month,day]=value.split('-').map(Number),d=new Date(`${value}T12:00:00Z`);
  return year>=1&&d.getUTCFullYear()===year&&d.getUTCMonth()+1===month&&d.getUTCDate()===day;
}
export function monthCells(month){
  if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(month))return [];
  const [year,m]=month.split('-').map(Number),first=new Date(`${month}-01T12:00:00Z`),offset=(first.getUTCDay()+6)%7;
  return Array.from({length:42},(_,i)=>{const date=new Date(first);date.setUTCDate(1-offset+i);return {date:date.toISOString().slice(0,10),in_month:date.getUTCMonth()+1===m&&date.getUTCFullYear()===year};});
}
export function shiftDate(date,days){const d=new Date(`${date}T12:00:00Z`);d.setUTCDate(d.getUTCDate()+days);return d.toISOString().slice(0,10);}
export function singaporeLocal(now=new Date()){
  const p=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Singapore',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(now),get=t=>p.find(x=>x.type===t).value;
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`;
}
export function selectedValue(date,hour,minute,withTime){return validDate(date)?date+(withTime?`T${pad(hour)}:${pad(minute)}`:''):'';}
export function valueError(value,min='',max=''){
  if(!validDate(value?.slice(0,10))||value.length>10&&!/^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d$/.test(value))return "Choose a valid date and time first.";
  const display=s=>s.replace('T',' ').replaceAll('-','/');
  if(min&&value<min)return `Choose a date or time on or after ${display(min)} ${value.length>10?"time":"date"}.`;
  if(max&&value>max)return `Choose a date or time on or before ${display(max)} ${value.length>10?"time":"date"}.`;
  return '';
}
const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
const button=(text,handler,cls='')=>{const b=node('button',cls,text);b.type='button';b.onclick=handler;return b;};
let sequence=0;
export class DateTimePicker{
  constructor(){
    this.active=null;
    document.addEventListener('pointerdown',e=>{if(this.active&&!this.panel.contains(e.target)&&!this.active.wrapper.contains(e.target))this.close(false);},true);
    document.addEventListener('keydown',e=>{if(this.active&&e.key==='Escape'){e.preventDefault();e.stopPropagation();this.close();}},true);
    document.addEventListener('close',e=>{if(this.active&&e.target.contains?.(this.active.input))this.close(false);},true);
    window.addEventListener('resize',()=>{if(this.active)this.position();});
  }
  attach(input){
    if(input.dataset.customPicker)return;
    input.dataset.customPicker='true';const label=input.getAttribute('aria-label')||input.closest('label')?.childNodes[0]?.textContent?.trim()||"date and time";
    const wrapper=node('span','date-time-field');input.before(wrapper);wrapper.append(input);
    const trigger=button('▦',e=>{e.preventDefault();e.stopPropagation();this.open(input);},'date-time-trigger');trigger.setAttribute('aria-label',"Choose "+label);trigger.setAttribute('aria-haspopup','dialog');trigger.setAttribute('aria-expanded','false');wrapper.append(trigger);
    input.setAttribute('aria-haspopup','dialog');input.setAttribute('aria-expanded','false');
    input.addEventListener('pointerdown',e=>{e.preventDefault();this.open(input);});
    input.addEventListener('click',e=>{e.preventDefault();if(this.active?.input!==input)this.open(input);});
    input.addEventListener('keydown',e=>{if(['Enter',' ','ArrowDown'].includes(e.key)){e.preventDefault();this.open(input);}});
    input._dateTimePicker={wrapper,trigger,label};
  }
  open(input){
    if(this.active?.input===input)return;
    this.close(false);const now=singaporeLocal(),value=input.value,today=now.slice(0,10),date=validDate(value.slice(0,10))?value.slice(0,10):null;
    this.active={input,...input._dateTimePicker,withTime:input.type==='datetime-local',today,date,hour:Number(value.slice(11,13)||now.slice(11,13)),minute:Number(value.slice(14,16)||now.slice(14,16)),month:(date||today).slice(0,7)};
    const a=this.active,panel=this.panel=node('div','date-time-popover');panel.id='date-time-popover-'+(++sequence);panel.setAttribute('role','dialog');panel.setAttribute('aria-label',"Choose "+a.label);panel.tabIndex=-1;
    a.input.setAttribute('aria-expanded','true');a.trigger.setAttribute('aria-expanded','true');a.input.setAttribute('aria-controls',panel.id);a.trigger.setAttribute('aria-controls',panel.id);
    panel.append(node('h3','date-time-title',"Choose "+a.label),node('p','date-time-hint',"Singapore time · A dot marks today; dark green marks your selection"));
    this.navigation=node('div','date-time-navigation');this.grid=node('div','date-time-grid');this.grid.setAttribute('role','grid');
    panel.append(this.navigation,this.grid);this.renderMonth();
    if(a.withTime){const controls=node('div','date-time-clock');for(const [key,label,max] of [['hour',"Hour",24],['minute',"Minute",60]]){const field=node('label','',label),select=node('select');select.setAttribute('aria-label',label);for(let i=0;i<max;i++)select.append(new Option(pad(i),String(i)));select.value=String(a[key]);select.onchange=()=>{a[key]=Number(select.value);this.updateStatus();};field.append(select);controls.append(field);}panel.append(controls);}
    this.selection=node('p','date-time-selection');this.selection.setAttribute('aria-live','polite');this.error=node('p','date-time-error');this.error.setAttribute('role','alert');
    const footer=node('div','date-time-footer');this.confirm=button("Confirm",()=>this.commit(),'date-time-confirm');footer.append(button("Clear",()=>{this.apply('');},'date-time-clear'),this.confirm);panel.append(this.selection,this.error,footer);
    panel.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.tagName==='SELECT')e.preventDefault();if(e.key==='Tab')this.trapFocus(e);});
    (input.closest('dialog')||document.body).append(panel);this.updateStatus();this.position();
    const focus=this.grid.querySelector(`[data-date="${date||today}"]:not(:disabled)`)||this.grid.querySelector('button:not(:disabled)');(focus||panel).focus({preventScroll:true});
  }
  renderMonth(focusDate){
    const a=this.active;if(!a)return;const [year,month]=a.month.split('-').map(Number);this.navigation.replaceChildren();
    const previous=button('‹',()=>this.navigate(-1),'date-time-month-step');previous.setAttribute('aria-label',"Previous month");
    const next=button('›',()=>this.navigate(1),'date-time-month-step');next.setAttribute('aria-label',"Next month");
    const ys=node('select'),ms=node('select');ys.setAttribute('aria-label',"Year");ms.setAttribute('aria-label',"Month");
    for(let y=Math.max(1,year-5);y<=Math.min(9999,year+5);y++)ys.append(new Option(String(y),String(y)));for(let m=1;m<=12;m++)ms.append(new Option(new Intl.DateTimeFormat('en-SG',{month:'long'}).format(new Date(Date.UTC(2020,m-1,15))),String(m)));ys.value=String(year);ms.value=String(month);
    const change=()=>{a.month=`${pad(ys.value).padStart(4,'0')}-${pad(ms.value)}`;this.renderMonth();this.position();};ys.onchange=change;ms.onchange=change;this.navigation.append(previous,ys,ms,next);
    this.grid.replaceChildren();this.grid.setAttribute('aria-label',new Intl.DateTimeFormat('en-SG',{month:'long',year:'numeric'}).format(new Date(Date.UTC(year,month-1,15))));
    for(const name of ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]){const day=node('span','date-time-weekday',name);day.setAttribute('role','columnheader');this.grid.append(day);}
    for(const cell of monthCells(a.month)){
      const day=button(String(Number(cell.date.slice(8))),()=>{a.date=cell.date;a.month=cell.date.slice(0,7);this.renderMonth(cell.date);this.updateStatus();},'date-time-day');
      day.dataset.date=cell.date;day.setAttribute('role','gridcell');day.setAttribute('aria-label',cell.date.replace(/^(\d+)-(\d+)-(\d+)$/,"$1-$2-$3"));day.setAttribute('aria-selected',String(cell.date===a.date));
      day.classList.toggle('is-selected',cell.date===a.date);day.classList.toggle('other-month',!cell.in_month);if(cell.date===a.today){day.classList.add('is-today');day.setAttribute('aria-current','date');}
      day.disabled=!!(a.input.min&&cell.date<a.input.min.slice(0,10)||a.input.max&&cell.date>a.input.max.slice(0,10));
      day.tabIndex=cell.date===(a.date||a.today)?0:-1;day.onkeydown=e=>this.dayKey(e,cell.date);this.grid.append(day);
    }
    if(focusDate)this.grid.querySelector(`[data-date="${focusDate}"]`)?.focus({preventScroll:true});
  }
  navigate(delta){const a=this.active,[year,m]=a.month.split('-').map(Number),d=new Date(`${a.month}-01T12:00:00Z`);d.setUTCMonth(m-1+delta);if(d.getUTCFullYear()<1||d.getUTCFullYear()>9999)return;a.month=d.toISOString().slice(0,7);this.renderMonth();}
  dayKey(e,date){const shifts={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7};if(Object.hasOwn(shifts,e.key)){e.preventDefault();const next=shiftDate(date,shifts[e.key]),a=this.active;if(a.input.min&&next<a.input.min.slice(0,10)||a.input.max&&next>a.input.max.slice(0,10))return;a.month=next.slice(0,7);this.renderMonth(next);}else if(e.key==='PageUp'||e.key==='PageDown'){e.preventDefault();this.navigate(e.key==='PageUp'?-1:1);this.grid.querySelector('button:not(:disabled)')?.focus();}}
  updateStatus(){if(!this.selection)return;const a=this.active,value=selectedValue(a.date,a.hour,a.minute,a.withTime);this.selection.textContent=value?"To confirm: "+value.replace('T',' ').replaceAll('-','/'):"Choose a date, then select Confirm.";this.error.textContent='';this.confirm.disabled=!value;}
  commit(){const a=this.active,value=selectedValue(a.date,a.hour,a.minute,a.withTime),error=valueError(value,a.input.min,a.input.max);if(error){this.error.textContent=error;return;}this.apply(value);}
  apply(value){const input=this.active.input;input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));this.close();}
  trapFocus(e){const nodes=[...this.panel.querySelectorAll('button:not(:disabled),select')],first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}
  position(){if(!this.active||!this.panel.isConnected)return;const r=this.active.input.getBoundingClientRect(),p=this.panel;const width=Math.min(360,window.innerWidth-20);p.style.width=width+'px';const h=p.getBoundingClientRect().height;const top=r.bottom+7+h<window.innerHeight?r.bottom+7:Math.max(10,Math.min(r.top-h-7,window.innerHeight-h-10));p.style.top=top+'px';p.style.left=Math.max(10,Math.min(r.left,window.innerWidth-width-10))+'px';}
  close(restore=true){if(!this.active)return;const {input,trigger}=this.active;input.setAttribute('aria-expanded','false');trigger.setAttribute('aria-expanded','false');this.panel?.remove();this.active=null;this.selection=null;this.error=null;if(restore&&input.isConnected)input.focus({preventScroll:true});}
}
