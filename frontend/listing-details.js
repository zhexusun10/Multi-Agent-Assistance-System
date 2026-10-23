const typeNames={Condominium:'公寓（Condominium）',Apartment:'公寓（Apartment）',HDB:'组屋（HDB）','HDB Apartment':'组屋（HDB）','hdb flat':'组屋（HDB）','HDB Flat':'组屋（HDB）',condominium:'公寓',apartment:'公寓','Terraced House':'排屋','Detached House':'独立式住宅','Semi-Detached House':'半独立式住宅','Walk-up':'步梯公寓'};
const categories={MRT:'地铁站',Supermarket:'超市',Park:'公园',FoodCourt:'食阁',ShoppingMall:'商场',Mall:'商场',Market:'市场',Library:'图书馆',Clinic:'诊所',Hospital:'医院',School:'学校',University:'大学',Campus:'校园',BusStop:'巴士站',HawkerCentre:'熟食中心'};
const present=v=>v!==null&&v!==undefined&&v!=='';
const numeric=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0;
const imageUrl=value=>{try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&!u.port&&(u.hostname==='pgimgs.com'||u.hostname.endsWith('.pgimgs.com'))?u.href:null;}catch{return null;}};
const date=value=>{try{return value?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Singapore',year:'numeric',month:'long',day:'numeric'}).format(new Date(value)):null;}catch{return null;}};

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
  attach(card,c){const root=this.el('section','listing-details-panel');root.setAttribute('aria-label','房源资料与照片');root.append(this.el('p','muted','正在读取房源资料、照片和周边地点…'));card.append(root);this.fill(root,c);}

  async fill(root,c,force=false){const key=this.key(c.listing.listing_id);try{const value=await this.load(c.listing.listing_id,key,force);if(root.isConnected&&key===this.key(c.listing.listing_id))this.render(root,value,c);}catch(e){if(!root.isConnected||key!==this.key(c.listing.listing_id))return;root.replaceChildren(this.el('p','listing-warning','补充资料暂时未加载，需求匹配结果仍保留。'),this.button('重试加载房源资料',()=>this.fill(root,c,true)));}}
  photo(photo,title){const wrap=this.el('div','property-photo-wrap'),img=this.el('img','property-photo');img.src=photo.url;img.alt=photo.kind==='floor_plan'?`${title} 户型图`:`${title} 房源照片`;img.loading='lazy';img.decoding='async';img.referrerPolicy='no-referrer';img.onerror=()=>{img.remove();wrap.classList.add('photo-unavailable');wrap.append(this.el('span','','这张照片暂时无法加载，请切换其他照片。'));};wrap.append(img);return wrap;}
  gallery(photos,title){
    const gallery=this.el('div','property-gallery');if(!photos.length){gallery.append(this.el('div','photo-empty','当前没有可展示的已收录照片'));return gallery;}
    let index=0;const frame=this.el('div','gallery-frame'),controls=this.el('div','gallery-controls'),count=this.el('span','gallery-count');
    const paint=()=>{frame.replaceChildren(this.photo(photos[index],title));count.textContent=`${index+1} / ${photos.length}${photos[index].kind==='floor_plan'?' · 户型图':''}`;};
    controls.append(this.button('上一张',()=>{index=(index-1+photos.length)%photos.length;paint();}),count,this.button('下一张',()=>{index=(index+1)%photos.length;paint();}));
    if(photos.length===1)for(const b of controls.querySelectorAll('button'))b.disabled=true;
    gallery.append(frame,controls,this.button(`查看全部 ${photos.length} 张照片 / 户型图`,()=>{const content=this.el('div','property-photo-grid');for(const [i,p] of photos.entries()){const figure=this.el('figure');figure.append(this.photo(p,title),this.el('figcaption','',`${i+1}. ${p.kind==='floor_plan'?'户型图':'房源照片'}`));content.append(figure);}this.showDetail(title+' · 已收录照片',content);}));paint();return gallery;
  }
  fact(dl,label,value){if(!present(value)||/^(unknown|n\/?a|未收录|未知|待确认|-|null)$/i.test(String(value).trim()))return;const item=this.el('div','property-fact');item.append(this.el('dt','',label),this.el('dd','',String(value)));dl.append(item);}
  releaseLayout(){this.layoutObserver?.disconnect();}
  render(root,data,c){
    this.releaseLayout();root.replaceChildren();root.dataset.loaded='true';const p=data.details||{},base=c.listing,photos=[...data.photos];
    if(!photos.length){const url=imageUrl(p.thumbnail_url||base.thumbnail_url);if(url)photos.push({url,kind:'photo',caption:''});}
    const title=p.title||base.title,grid=this.el('div','property-content-grid'),facts=this.el('dl','property-facts');
    const beds=present(p.bedrooms)?p.bedrooms:base.bedrooms,area=present(p.floor_area_sqft)?p.floor_area_sqft:base.floor_area_sqft;
    const address=p.full_address||p.short_address||[p.street_number,p.street_name].filter(present).join(' ');
    const mrt=[p.nearest_mrt,numeric(p.mrt_distance_m)?`${Math.round(p.mrt_distance_m)} 米`:null,numeric(p.mrt_walking_mins)?`步行 ${p.mrt_walking_mins} 分钟`:null].filter(present).join(' · ');
    const items=[
      ['房屋类型',typeNames[p.property_type||base.property_type]||p.property_type||base.property_type],
      ['卧室数量',numeric(beds)&&beds>0?`${beds} 间`:null],
      ['卫浴数量',numeric(p.bathrooms)&&p.bathrooms>0?`${p.bathrooms} 间`:null],
      ['室内面积',numeric(area)&&area>0?`${area.toLocaleString()} sqft · 约 ${(area*0.09290304).toFixed(1)}㎡`:null],
      ['地址',address],['邮区 / 邮编',[p.district_code||base.district,p.postal_code].filter(present).join(' · ')],
      ['楼层',p.floor_level],['附近地铁',mrt],['建成年份',numeric(p.build_year)&&p.build_year>0?p.build_year:null],
      ['房屋产权',p.tenure||p.tenure_category],['土地面积',numeric(p.land_area_sqft)&&p.land_area_sqft>0?p.land_area_sqft+' sqft':null],
      ['每平方英尺租金',numeric(p.psf)&&p.psf>0?'$'+p.psf:null],['区域',p.region_text||p.region_code],
      ['经纪人',p.agent_name],['经纪公司',p.agency_name],['挂牌日期',date(p.posted_at)],['资料更新日期',date(p.updated_at)]
    ];
    for(const [label,value] of items)this.fact(facts,label,value);
    const gallery=this.gallery(photos,title);
    grid.style.setProperty('--photo-height',Math.min(560,Math.max(300,Math.ceil(facts.childElementCount/2)*65-86))+'px');
    if(photos.length)grid.append(gallery);
    else grid.classList.add('without-photos');
    if(facts.childElementCount)grid.append(facts);else grid.classList.add('without-facts');
    root.append(this.el('h4','property-section-title','房源详细资料'),grid);
    if(photos.length&&facts.childElementCount){this.layoutObserver=new ResizeObserver(()=>{
      const frame=gallery.querySelector('.gallery-frame');if(!root.isConnected||!frame)return;
      const controls=gallery.getBoundingClientRect().height-frame.getBoundingClientRect().height;
      grid.style.setProperty('--photo-height',Math.min(650,Math.max(240,facts.getBoundingClientRect().height-controls))+'px');
    });this.layoutObserver.observe(facts);}
    this.nearby(root,data);
    if(data.warnings.length)root.append(this.button('重新加载资料',()=>{const key=this.key(base.listing_id);this.cache.delete(key);this.fill(root,c,true);}));
  }
  nearby(root,data){
    if(!data.nearby.length)return;
    const section=this.el('div','property-nearby');section.append(this.el('h4','property-section-title','周边地点'));
    const display=[],seen=new Set();for(const n of data.nearby)if(!seen.has(n.category)){seen.add(n.category);display.push(n);}for(const n of data.nearby)if(display.length<8&&!display.includes(n))display.push(n);
    const rows=this.el('div','nearby-grid');for(const n of display.slice(0,8))rows.append(this.nearbyRow(n));section.append(rows);
    if(data.nearby.length>8){const all=this.el('details','nearby-more'),list=this.el('div','nearby-grid');all.append(this.el('summary','',`查看全部周边地点（${data.nearby.length}）`));for(const n of data.nearby)list.append(this.nearbyRow(n));all.append(list);section.append(all);}
    root.append(section);
  }
  nearbyRow(n){const row=this.el('div','nearby-place');row.append(this.el('span','nearby-category',categories[n.category]||n.category),this.el('span','nearby-name',n.name),this.el('span','nearby-distance',`${n.metric==='straight_line'?'直线约':'挂牌标注'} ${Math.round(n.distance_m)} 米${numeric(n.walking_mins)?` · 标注步行${n.walking_mins}分钟`:''}`));if(n.source_url){const link=this.el('a','nearby-source-link','地点来源 ↗');link.href=n.source_url;link.target='_blank';link.rel='noopener noreferrer';row.append(link);}return row;}
}
