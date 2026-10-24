const typeNames={Condominium:"Condominium",Apartment:"Apartment",HDB:"HDB flat",'HDB Apartment':"HDB flat",'hdb flat':"HDB flat",'HDB Flat':"HDB flat",condominium:"Condominium",apartment:"Apartment",'Terraced House':"Terraced house",'Detached House':"Detached house",'Semi-Detached House':"Semi-detached house",'Walk-up':"Walk-up apartment"};
const categories={MRT:"MRT station",Supermarket:"Supermarket",Park:"Park",FoodCourt:"Food court",ShoppingMall:"Shopping mall",Mall:"Shopping mall",Market:"Market",Library:"Library",Clinic:"Clinic",Hospital:"Hospital",School:"School",University:"University",Campus:"Campus",BusStop:"Bus stop",HawkerCentre:"Hawker centre"};
const present=v=>v!==null&&v!==undefined&&v!=='';
const numeric=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0;
const imageUrl=value=>{try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&!u.port&&(u.hostname==='pgimgs.com'||u.hostname.endsWith('.pgimgs.com'))?u.href:null;}catch{return null;}};
const date=value=>{try{return value?new Intl.DateTimeFormat('en-SG',{timeZone:'Asia/Singapore',year:'numeric',month:'long',day:'numeric'}).format(new Date(value)):null;}catch{return null;}};

export class ListingDetailsUI{
  constructor(deps){Object.assign(this,deps);this.cache=new Map();this.pending=new Map();this.jobs=[];this.running=0;}
  key(id){return `${this.getSession()}:${this.getRequest()}:${id}`;}
  async load(id,key,force=false){
    const cached=this.cache.get(key);if(!force&&cached&&cached.until>Date.now())return cached.value;
    if(this.pending.has(key))return this.pending.get(key);
    const session=this.getSession(),request=this.getRequest();
    const promise=new Promise((resolve,reject)=>{this.jobs.push(async()=>{try{const value=await this.api(`listings/${id}/presentation?session_id=${encodeURIComponent(session)}&source_request_id=${encodeURIComponent(request)}`);if(this.cache.size>=100)this.cache.delete(this.cache.keys().next().value);this.cache.set(key,{value,until:Date.now()+(value.warnings.length?5000:300000)});resolve(value);}catch(e){reject(e);}});this.pump();}).finally(()=>this.pending.delete(key));this.pending.set(key,promise);return promise;
  }
  pump(){while(this.running<3&&this.jobs.length){const job=this.jobs.shift();this.running++;job().finally(()=>{this.running--;this.pump();});}}
  attach(card,c){const root=this.el('section','listing-details-panel');root.setAttribute('aria-label',"Listing details and photos");root.append(this.el('p','muted',"Loading listing details, photos and nearby places…"));card.append(root);this.fill(root,c);}

  async fill(root,c,force=false){const key=this.key(c.listing.listing_id);try{const value=await this.load(c.listing.listing_id,key,force);if(root.isConnected&&key===this.key(c.listing.listing_id))this.render(root,value,c);}catch(e){if(!root.isConnected||key!==this.key(c.listing.listing_id))return;root.replaceChildren(this.el('p','listing-warning',"Additional details could not be loaded. Your requirement matches are still available."),this.button("Retry loading listing details",()=>this.fill(root,c,true)));}}
  photo(photo,title){const wrap=this.el('div','property-photo-wrap'),img=this.el('img','property-photo');img.src=photo.url;img.alt=photo.kind==='floor_plan'?`${title} floor plan`:`${title} property photo`;img.loading='lazy';img.decoding='async';img.referrerPolicy='no-referrer';img.onerror=()=>{img.remove();wrap.classList.add('photo-unavailable');wrap.append(this.el('span','',"This photo could not be loaded. Try another photo."));};wrap.append(img);return wrap;}
  gallery(photos,title){
    const gallery=this.el('div','property-gallery');if(!photos.length){gallery.append(this.el('div','photo-empty',"No photos are currently available"));return gallery;}
    let index=0;const frame=this.el('div','gallery-frame'),controls=this.el('div','gallery-controls'),count=this.el('span','gallery-count');
    const paint=()=>{frame.replaceChildren(this.photo(photos[index],title));count.textContent=`${index+1} / ${photos.length}${photos[index].kind==='floor_plan'?" · Floor plan":''}`;};
    controls.append(this.button("Previous",()=>{index=(index-1+photos.length)%photos.length;paint();}),count,this.button("Next",()=>{index=(index+1)%photos.length;paint();}));
    if(photos.length===1)for(const b of controls.querySelectorAll('button'))b.disabled=true;
    gallery.append(frame,controls,this.button(`View all ${photos.length} photos / floor plans`,()=>{const content=this.el('div','property-photo-grid');for(const [i,p] of photos.entries()){const figure=this.el('figure');figure.append(this.photo(p,title),this.el('figcaption','',`${i+1}. ${p.kind==='floor_plan'?"Floor plan":"Property photo"}`));content.append(figure);}this.showDetail(title+" · Available photos",content);}));paint();return gallery;
  }
  fact(dl,label,value){if(!present(value)||/^(unknown|n\/?a|未收录|未知|待确认|-|null)$/i.test(String(value).trim()))return;const item=this.el('div','property-fact');item.append(this.el('dt','',label),this.el('dd','',String(value)));dl.append(item);}
  releaseLayout(){this.layoutObserver?.disconnect();}
  render(root,data,c){
    this.releaseLayout();root.replaceChildren();root.dataset.loaded='true';const p=data.details||{},base=c.listing,photos=[...data.photos];
    if(!photos.length){const url=imageUrl(p.thumbnail_url||base.thumbnail_url);if(url)photos.push({url,kind:'photo',caption:''});}
    const title=p.title||base.title,grid=this.el('div','property-content-grid'),facts=this.el('dl','property-facts');
    const beds=present(p.bedrooms)?p.bedrooms:base.bedrooms,area=present(p.floor_area_sqft)?p.floor_area_sqft:base.floor_area_sqft;
    const address=p.full_address||p.short_address||[p.street_number,p.street_name].filter(present).join(' ');
    const mrt=[p.nearest_mrt,numeric(p.mrt_distance_m)?`${Math.round(p.mrt_distance_m)} m`:null,numeric(p.mrt_walking_mins)?`Walking: ${p.mrt_walking_mins} min`:null].filter(present).join(' · ');
    const items=[
      ["Property type",typeNames[p.property_type||base.property_type]||p.property_type||base.property_type],
      ["Number of bedrooms",numeric(beds)&&beds>0?`${beds} bedroom${beds===1?"":"s"}`:null],
      ["Number of bathrooms",numeric(p.bathrooms)&&p.bathrooms>0?`${p.bathrooms} bathroom${p.bathrooms===1?"":"s"}`:null],
      ["Floor area",numeric(area)&&area>0?`${area.toLocaleString()} sq ft · approx. ${(area*0.09290304).toFixed(1)}㎡`:null],
      ["Address",address],["District / postal code",[p.district_code||base.district,p.postal_code].filter(present).join(' · ')],
      ["Floor level",p.floor_level],["Nearby MRT",mrt],["Year built",numeric(p.build_year)&&p.build_year>0?p.build_year:null],
      ["Tenure",p.tenure||p.tenure_category],["Land area",numeric(p.land_area_sqft)&&p.land_area_sqft>0?p.land_area_sqft+' sqft':null],
      ["Monthly rent per sq ft",numeric(p.psf)&&p.psf>0?'$'+p.psf:null],["Region",p.region_text||p.region_code],
      ["Agent",p.agent_name],["Agency",p.agency_name],["Listing date",date(p.posted_at)],["Last updated",date(p.updated_at)]
    ];
    for(const [label,value] of items)this.fact(facts,label,value);
    const gallery=this.gallery(photos,title);
    grid.style.setProperty('--photo-height',Math.min(560,Math.max(300,Math.ceil(facts.childElementCount/2)*65-86))+'px');
    if(photos.length)grid.append(gallery);
    else grid.classList.add('without-photos');
    if(facts.childElementCount)grid.append(facts);else grid.classList.add('without-facts');
    root.append(this.el('h4','property-section-title',"Property details"),grid);
    if(photos.length&&facts.childElementCount){this.layoutObserver=new ResizeObserver(()=>{
      const frame=gallery.querySelector('.gallery-frame');if(!root.isConnected||!frame)return;
      const controls=gallery.getBoundingClientRect().height-frame.getBoundingClientRect().height;
      grid.style.setProperty('--photo-height',Math.min(650,Math.max(240,facts.getBoundingClientRect().height-controls))+'px');
    });this.layoutObserver.observe(facts);}
    this.nearby(root,data);
    if(data.warnings.length)root.append(this.button("Reload details",()=>{const key=this.key(base.listing_id);this.cache.delete(key);this.fill(root,c,true);}));
  }
  nearby(root,data){
    if(!data.nearby.length)return;
    const section=this.el('div','property-nearby');section.append(this.el('h4','property-section-title',"Nearby places"));
    const display=[],seen=new Set();for(const n of data.nearby)if(!seen.has(n.category)){seen.add(n.category);display.push(n);}for(const n of data.nearby)if(display.length<8&&!display.includes(n))display.push(n);
    const rows=this.el('div','nearby-grid');for(const n of display.slice(0,8))rows.append(this.nearbyRow(n));section.append(rows);
    if(data.nearby.length>8){const all=this.el('details','nearby-more'),list=this.el('div','nearby-grid');all.append(this.el('summary','',`View all nearby places (${data.nearby.length})`));for(const n of data.nearby)list.append(this.nearbyRow(n));all.append(list);section.append(all);}
    root.append(section);
  }
  nearbyRow(n){const row=this.el('div','nearby-place');row.append(this.el('span','nearby-category',categories[n.category]||n.category),this.el('span','nearby-name',n.name),this.el('span','nearby-distance',`${n.metric==='straight_line'?"approx. straight-line distance: ":"Listing information"} ${Math.round(n.distance_m)} m${numeric(n.walking_mins)?` · Listed walking time: ${n.walking_mins} min`:''}`));if(n.source_url){const link=this.el('a','nearby-source-link',"Place source ↗");link.href=n.source_url;link.target='_blank';link.rel='noopener noreferrer';row.append(link);}return row;}
}
