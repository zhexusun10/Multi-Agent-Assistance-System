/** Monotonic intervals; a suspended browser does not accrue its missing timer gap. */
export class ReadingClock{
  constructor(now,idleMs=30000,maxGap=5000){this.last=now;this.active=false;this.total=0;this.idleMs=idleMs;this.maxGap=maxGap;}
  sample(now,eligible,lastActivity){const delta=now-this.last;if(this.active&&delta>=0&&delta<=this.maxGap)this.total+=Math.max(0,Math.min(now,lastActivity+this.idleMs)-this.last);this.last=now;this.active=eligible;return Math.floor(this.total);}
}

export class ViewTracker{
  constructor({send,outbox,onStatus=()=>{}}){
    Object.assign(this,{send,outbox,onStatus});this.tabId=crypto.randomUUID();this.view=null;this.segment=null;this.starting=false;this.region=false;this.windowActive=true;this.lastActivity=performance.now();this.persisting=Promise.resolve();
    const activity=e=>{const surface=this.surface();this.region=!!surface&&(surface.contains(e.target)||e.target===surface);if(this.region){this.windowActive=true;this.lastActivity=performance.now();}this.tick();};
    // Programmatic scrolling (including the chat log's auto-scroll) is not user
    // attention. Wheel/touch/key input identifies intentional reading activity.
    document.addEventListener('pointerdown',activity,true);document.addEventListener('keydown',activity,true);document.addEventListener('wheel',activity,{capture:true,passive:true});document.addEventListener('touchstart',activity,{capture:true,passive:true});document.addEventListener('touchmove',activity,{capture:true,passive:true});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden){this.windowActive=true;this.lastActivity=performance.now();}this.tick();if(document.hidden)void outbox.flush();});
    window.addEventListener('blur',()=>{this.windowActive=false;this.tick();});window.addEventListener('focus',()=>{this.windowActive=true;this.lastActivity=performance.now();this.tick();});
    window.addEventListener('pagehide',()=>{this.finish('pagehide');void this.flush();});
    this.timer=setInterval(()=>this.tick(),1000);
  }
  surface(){const dialog=document.querySelector('dialog[open]');if(dialog)return dialog.dataset.readingListingId===this.view?.listing_id?dialog:null;return this.view?.root||null;}
  focused(){return matchMedia('(pointer:coarse)').matches?this.windowActive:document.hasFocus();}
  eligible(){return !!this.view?.loaded&&this.view.root.isConnected&&!this.view.root.hidden&&!!this.surface()&&!document.hidden&&this.focused()&&this.region&&performance.now()-this.lastActivity<30000;}
  open(view){this.close('session');this.view={...view,view_session_id:crypto.randomUUID()};this.region=true;this.lastActivity=performance.now();this.tick();}
  loaded(){if(this.view){this.view.loaded=true;this.lastActivity=performance.now();this.tick();}}
  close(reason='return'){this.finish(reason);this.view=null;this.region=false;this.onStatus('浏览偏好只在下次重新推荐时使用。');}
  base(view){return {session_id:view.session_id,source_request_id:view.source_request_id,requirements_version:view.requirements_version,listing_id:view.listing_id,tab_id:this.tabId};}
  packet(segment,kind,reason){return {...this.base(segment.view),kind,event_id:crypto.randomUUID(),view_session_id:segment.view.view_session_id,segment_id:segment.id,sequence:++segment.sequence,active_duration_ms:Math.floor(segment.clock.total),...(reason?{end_reason:reason}:{})};}
  finish(reason='other_area'){
    const s=this.segment;if(!s)return;this.segment=null;s.clock.sample(performance.now(),false,this.lastActivity);
    const packet=this.packet(s,'complete',reason);this.persisting=this.persisting.then(()=>this.outbox.enqueue(packet)).catch(()=>{});
  }
  async tick(){
    const eligible=this.eligible(),now=performance.now(),s=this.segment;
    if(s){const gap=now-s.clock.last>5000;s.clock.sample(now,eligible&&!gap,this.lastActivity);if(!eligible||gap){this.finish(gap?'gap':document.hidden?'hidden':!this.focused()?'blur':now-this.lastActivity>=30000?'idle':'other_area');this.onStatus('浏览记录已暂停；当前推荐顺序保持不变。');return;}
      if(s.clock.total>=120000){this.finish('segment_limit');return;}
      if(now-s.lastSent>=10000){s.lastSent=now;const packet=this.packet(s,'heartbeat','heartbeat');this.persisting=this.persisting.then(()=>this.outbox.enqueue(packet)).catch(()=>{});}this.onStatus('正在记录有效浏览 · 本轮推荐保持固定');return;
    }
    if(!eligible||this.starting)return;this.starting=true;const view=this.view,id=crypto.randomUUID();
    try{const result=await this.send({...this.base(view),kind:'begin',event_id:crypto.randomUUID(),view_session_id:view.view_session_id,segment_id:id,sequence:0,active_duration_ms:0});
      if(result.granted){const segment={id,view,sequence:0,clock:new ReadingClock(performance.now()),lastSent:performance.now()};segment.clock.active=true;
        if(this.view===view&&this.eligible())this.segment=segment;
        else{segment.clock.active=false;await this.outbox.enqueue(this.packet(segment,'complete','other_area'));}
      }else this.onStatus('另一页面正在记录浏览；此页暂不计时。');
    }catch{this.onStatus('浏览采集暂未连接，已显示的房源仍可正常查看。');}finally{this.starting=false;}
  }
  async flush(){await this.persisting;await this.outbox.flush();}
  async beforeQuery(){this.finish('query');await Promise.race([this.flush(),new Promise(resolve=>setTimeout(resolve,1800))]);}
}
