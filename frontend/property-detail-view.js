import {renderMatches,rentElement} from './property-presentation.js?v=20261024-official-geo';

export class PropertyDetailView{
  constructor(deps){Object.assign(this,deps);this.root=document.getElementById('property-detail');this.resultRoot=document.querySelector('.results');this.current=null;}
  close(){if(!this.current)return;this.tracker.close('return');this.details.releaseLayout();this.root.hidden=true;this.root.replaceChildren();this.resultRoot.classList.remove('detail-mode');const {scroll,button,windowScroll}=this.current;this.current=null;document.getElementById('recommendations').scrollTop=scroll;button?.focus({preventScroll:true});if(innerWidth<=850)window.scrollTo({top:windowScroll});}
  sync(){if(this.current){const s=this.getState();if(s.latest_request_id!==this.current.request||s.active_operation_id!==this.current.operation||this.getSession()!==this.current.session)this.close();}}
  async open(candidate,verified,originButton){
    this.close();const state=this.getState(),id=candidate.listing.listing_id,request=state.latest_request_id,session=this.getSession();
    const current={id,request,session,operation:state.active_operation_id,scroll:document.getElementById('recommendations').scrollTop,windowScroll:window.scrollY,button:originButton};this.current=current;
    this.root.hidden=false;this.resultRoot.classList.add('detail-mode');this.root.replaceChildren();
    const header=this.el('header','property-detail-header'),back=this.button("← Back to recommendations",()=>this.close()),heading=this.el('h2','',candidate.listing.title);heading.tabIndex=-1;
    header.append(back);this.root.append(header);
    const body=this.el('div','property-detail-body');body.append(heading,rentElement(this.el,candidate.listing.price,'detail-rent'));
    body.append(renderMatches({el:this.el,candidate,criteria:state.requirements.criteria,formatCriterion:this.formatCriterion,detail:true}));
    const facts=this.el('section','detail-complete-facts');facts.append(this.el('p','muted',"Loading photos, floor plans and full details…"));body.append(facts);
    const footer=this.el('div','property-detail-actions');footer.append(this.button("Contact agent",()=>this.followup(candidate,true)),this.button("Back to recommendations",()=>this.close()));
    body.append(footer);this.root.append(body);this.root.scrollTop=0;heading.focus({preventScroll:true});
    if(innerWidth<=850)this.root.scrollIntoView({block:'start'});
    this.tracker.open({root:this.root,loaded:false,session_id:session,source_request_id:request,requirements_version:state.requirements_version,listing_id:id});
    try{const data=await this.details.load(id,this.details.key(id));if(this.current!==current)return;this.details.render(facts,data,candidate);this.tracker.loaded();}
    catch{if(this.current===current){facts.replaceChildren(this.el('p','listing-warning',"Property details could not be loaded. Please try again."),this.button("Reload details",()=>this.open(candidate,verified,originButton)));}}
  }
}
