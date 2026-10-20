const typeNames={Condominium:'公寓（Condominium）',Apartment:'公寓（Apartment）',HDB:'组屋（HDB）','HDB Apartment':'组屋（HDB）','Terraced House':'排屋','Detached House':'独立式住宅','Semi-Detached House':'半独立式住宅','Walk-up':'步梯公寓'};
const categories={MRT:'地铁站',Supermarket:'超市',Park:'公园',FoodCourt:'食阁',ShoppingMall:'商场',Mall:'商场',Market:'市场',Library:'图书馆',Clinic:'诊所',Hospital:'医院',School:'学校',University:'大学',Campus:'校园',BusStop:'巴士站',HawkerCentre:'熟食中心'};
const present=v=>v!==null&&v!==undefined&&v!=='';
const numeric=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0;
const imageUrl=value=>{try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&!u.port&&(u.hostname==='pgimgs.com'||u.hostname.endsWith('.pgimgs.com'))?u.href:null;}catch{return null;}};
const date=value=>{try{return value?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Singapore',year:'numeric',month:'long',day:'numeric'}).format(new Date(value)):'未收录';}catch{return '未收录';}};

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
  fact(dl,label,value){const item=this.el('div','property-fact');item.append(this.el('dt','',label),this.el('dd','',present(value)?String(value):'未收录'));dl.append(item);}
  render(root,data,c){
    root.replaceChildren();root.dataset.loaded='true';const p=data.details||{},base=c.listing,photos=[...data.photos];
    if(!photos.length){const url=imageUrl(p.thumbnail_url||base.thumbnail_url);if(url)photos.push({url,kind:'photo',caption:''});}
    const title=p.title||base.title,grid=this.el('div','property-content-grid'),facts=this.el('dl','property-facts');
    const beds=present(p.bedrooms)?p.bedrooms:base.bedrooms,area=present(p.floor_area_sqft)?p.floor_area_sqft:base.floor_area_sqft;
    this.fact(facts,'房屋类型',typeNames[p.property_type||base.property_type]||p.property_type||base.property_type);
    this.fact(facts,'卧室数量',numeric(beds)?beds===0?'原始记录为0，户型待确认':`${beds} 间`:'未收录');
    this.fact(facts,'卫浴数量',numeric(p.bathrooms)?`${p.bathrooms} 间（不代表独立使用）`:null);
    this.fact(facts,'室内面积',numeric(area)&&area>0?`${area.toLocaleString()} sqft · 约 ${(area*0.09290304).toFixed(1)}㎡`:null);
    this.fact(facts,'邮区 / 邮编',[p.district_code||base.district,p.postal_code].filter(present).join(' · '));
    this.fact(facts,'楼层',p.floor_level);grid.append(this.gallery(photos,title),facts);root.append(this.el('h4','property-section-title','房源本身的资料'),grid);
    const address=p.full_address||p.short_address||[p.street_number,p.street_name].filter(present).join(' ');root.append(this.el('p','property-address',address?'地址：'+address:'详细地址：当前记录未收录，可向经纪人询问。'));
    if(p.nearest_mrt||numeric(p.mrt_distance_m)||numeric(p.mrt_walking_mins)){const mrt=[p.nearest_mrt,numeric(p.mrt_distance_m)?`${p.mrt_distance_m}米`:null,numeric(p.mrt_walking_mins)?`步行${p.mrt_walking_mins}分钟`:null].filter(present).join(' · ');root.append(this.el('p','property-mrt','挂牌交通标注：'+mrt+'（尚未验证实际路线）'));}
    root.append(this.el('p','property-facilities',data.unknown_facilities));
    const extra=this.el('details','property-extra'),table=this.el('dl','property-facts');extra.append(this.el('summary','','更多房源资料'));
    for(const [label,value] of [['建成年份',p.build_year],['产权（非租客租期）',p.tenure||p.tenure_category],['土地面积',numeric(p.land_area_sqft)&&p.land_area_sqft>0?p.land_area_sqft+' sqft':null],['挂牌PSF',p.psf],['区域',p.region_text||p.region_code],['挂牌经纪人',p.agent_name],['经纪公司',p.agency_name],['挂牌记录日期',date(p.posted_at)],['数据更新时间',date(p.updated_at)]])this.fact(table,label,value);
    extra.append(table,this.el('p','muted',p.detail_fetched===false?'这套房仅收录了搜索卡片资料；未收录的信息需向经纪人询问。':'以上为历史挂牌记录，当前可租、设施及实际状况仍需向经纪人确认。'));root.append(extra);
    this.nearby(root,data);
    if(data.photos_has_more)root.append(this.el('p','muted','照片目录超过当前读取上限，可到原始挂牌查看其余照片。'));
    for(const warning of data.warnings)root.append(this.el('p','listing-warning',warning));
    if(data.warnings.length)root.append(this.button('重试补充资料',()=>{const key=this.key(base.listing_id);this.cache.delete(key);this.fill(root,c,true);}));
  }
  nearby(root,data){
    const section=this.el('div','property-nearby');section.append(this.el('h4','property-section-title','周边地点 · 已收录'));
    if(!data.nearby.length)section.append(this.el('p','muted',data.sections.nearby==='unavailable'?'周边资料读取暂时不可用。':'当前记录没有可展示的周边地点，不表示附近没有设施。'));
    const display=[],seen=new Set();for(const n of data.nearby)if(!seen.has(n.category)){seen.add(n.category);display.push(n);}for(const n of data.nearby)if(display.length<8&&!display.includes(n))display.push(n);
    const rows=this.el('div','nearby-grid');for(const n of display.slice(0,8))rows.append(this.nearbyRow(n));section.append(rows);
    if(data.nearby.length>8){const all=this.el('details','nearby-more'),list=this.el('div','nearby-grid');all.append(this.el('summary','',`查看全部已收录周边地点（${data.nearby.length}）`));for(const n of data.nearby)list.append(this.nearbyRow(n));all.append(list);section.append(all);}
    const radii=[...new Set(data.nearby.filter(n=>n.metric==='straight_line'&&numeric(n.radius_m)).map(n=>n.radius_m))];const scope=radii.length?'OSM 周边记录的采集半径为 '+radii.join(' / ')+' 米。':'';section.append(this.el('p','muted nearby-source',scope+data.nearby_note+' 数据：© OpenStreetMap contributors / PropertyGuru。'));root.append(section);
  }
  nearbyRow(n){const row=this.el('div','nearby-place');row.append(this.el('span','nearby-category',categories[n.category]||n.category),this.el('span','nearby-name',n.name),this.el('span','nearby-distance',`${n.metric==='straight_line'?'直线约':'挂牌标注'} ${Math.round(n.distance_m)} 米${numeric(n.walking_mins)?` · 标注步行${n.walking_mins}分钟`:''}`));if(n.source_url){const link=this.el('a','nearby-source-link','地点来源 ↗');link.href=n.source_url;link.target='_blank';link.rel='noopener noreferrer';row.append(link);}return row;}
}
