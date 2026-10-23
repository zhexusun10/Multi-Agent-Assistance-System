// Persist cumulative packets before sending. A retry always retains its event ID.
export class BehaviorOutbox{
  constructor({send,scope,onError=()=>{}}){
    Object.assign(this,{send,onError});this.memory=new Map();this.sending=null;
    this.db=(async()=>{if(!globalThis.indexedDB)return null;const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(scope));const name=[...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('');return new Promise(resolve=>{const r=indexedDB.open('rental-behavior-'+name,1);r.onupgradeneeded=()=>r.result.createObjectStore('events',{keyPath:'id'});r.onsuccess=()=>resolve(r.result);r.onerror=()=>resolve(null);});})().catch(()=>null);
    this.online=()=>void this.flush();window.addEventListener('online',this.online);
    this.timer=setInterval(()=>void this.flush(),10000);
    void this.flush();
  }
  async storage(mode,work){const db=await this.db;if(!db)return work(null);return new Promise((resolve,reject)=>{const tx=db.transaction('events',mode);let value;try{const req=work(tx.objectStore('events'));req.onsuccess=()=>{value=req.result;};tx.oncomplete=()=>resolve(value);tx.onerror=()=>reject(tx.error);}catch(e){reject(e);}});}
  async enqueue(body){const item={id:body.event_id,body,at:Date.now()};this.memory.set(item.id,item);try{await this.storage('readwrite',s=>s?s.put(item):undefined);}catch{this.onError('浏览记录暂存在当前页面，持久化暂不可用。');}void this.flush();}
  async pending(){try{const rows=await this.storage('readonly',s=>s?s.getAll():undefined);for(const r of rows||[])this.memory.set(r.id,r);}catch{}return [...this.memory.values()].sort((a,b)=>a.at-b.at||a.body.sequence-b.body.sequence);}
  async remove(id){this.memory.delete(id);try{await this.storage('readwrite',s=>s?s.delete(id):undefined);}catch{}}
  async removeSession(session){for(const item of await this.pending())if(item.body.session_id===session)await this.remove(item.id);}
  async flush(){if(this.sending)return this.sending;this.sending=(async()=>{for(let batch=0;batch<10;batch++){const items=(await this.pending()).slice(0,100);if(!items.length)break;for(const item of items){try{const result=await this.send(item.body,true);this.onResult?.(item.body,result);await this.remove(item.id);}catch(e){if(e.status>=400&&e.status<500&&e.status!==429){await this.remove(item.id);this.onError('部分浏览记录未通过来源或时长校验，未用于推荐。');continue;}return;}}}})();try{await this.sending;}finally{this.sending=null;}}
}
