// A response started before an agent finishes can arrive after the final state.
// Serialize refreshes and reconcile again if an event arrives during a fetch.
export class SessionSync {
  constructor({getSession,getState,fetchState,applyState}){Object.assign(this,{getSession,getState,fetchState,applyState});this.runs=new Map();}
  refresh(){
    const session=this.getSession();if(!session)return Promise.resolve();
    const existing=this.runs.get(session);if(existing){existing.dirty=true;return existing.promise;}
    const run={dirty:false,promise:null};this.runs.set(session,run);
    run.promise=(async()=>{try{do{
      run.dirty=false;const next=await this.fetchState(session);
      if(this.getSession()!==session)continue;
      const current=this.getState();
      if(current?.session_id===session&&Number(next.revision)<Number(current.revision))continue;
      if(current?.session_id===session&&next.revision===current.revision&&next.last_event_id<=current.last_event_id)continue;
      this.applyState(next);
    }while(run.dirty);}finally{if(this.runs.get(session)===run)this.runs.delete(session);}})();
    return run.promise;
  }
}

export async function* readSse(response,{onActivity=()=>{}}={}){
  if(!response.ok){let error;try{error=await response.json();}catch{error={detail:'连接失败'};}throw Error(error.detail||'连接失败');}
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
  try{while(true){
    const {done,value}=await reader.read();if(done)break;onActivity();
    buffer+=decoder.decode(value,{stream:true});
    // Keep trailing CR until the next chunk, including split CRLF separators.
    let match;while((match=/\r?\n\r?\n/.exec(buffer))){
      const block=buffer.slice(0,match.index).replace(/\r\n/g,'\n');buffer=buffer.slice(match.index+match[0].length);
      let id=null;const data=[];
      for(const line of block.split('\n')){if(line.startsWith('id:')){const n=Number(line.slice(3).trim());if(Number.isSafeInteger(n)&&n>=0)id=n;}if(line.startsWith('data:'))data.push(line.slice(5).replace(/^ /,''));}
      if(data.length)yield {id,event:JSON.parse(data.join('\n'))};
    }
  }}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
}
