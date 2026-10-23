/** Small per-conversation menu. Deleting a menu item never selects that chat. */
export class SessionHistoryUI{
 constructor({root,el,onSelect,onDelete}){Object.assign(this,{root,el,onSelect,onDelete});document.addEventListener('click',e=>{if(!this.root.contains(e.target))this.close();});document.addEventListener('keydown',e=>{if(e.key==='Escape')this.close();});}
 close(){for(const menu of this.root.querySelectorAll('.session-menu'))menu.hidden=true;for(const b of this.root.querySelectorAll('.session-toggle'))b.setAttribute('aria-expanded','false');}
 render(sessions,selected){this.root.replaceChildren();for(const s of sessions){
  const row=this.el('div','session-row'+(s.id===selected?' selected':'')),choose=this.el('button','session-select',s.title),toggle=this.el('button','session-toggle','▾'),menu=this.el('div','session-menu'),remove=this.el('button','session-delete','删除对话');
  choose.title=s.title;choose.onclick=()=>this.onSelect(s.id);toggle.type='button';toggle.setAttribute('aria-label','对话操作：'+s.title);toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-haspopup','menu');
  menu.hidden=true;menu.setAttribute('role','menu');remove.setAttribute('role','menuitem');
  toggle.onclick=e=>{e.stopPropagation();const open=menu.hidden;this.close();menu.hidden=!open;toggle.setAttribute('aria-expanded',String(open));};
  remove.onclick=async e=>{e.stopPropagation();remove.disabled=true;try{await this.onDelete(s.id);}finally{remove.disabled=false;this.close();}};
  menu.append(remove);row.append(choose,toggle,menu);this.root.append(row);
 }}
}
