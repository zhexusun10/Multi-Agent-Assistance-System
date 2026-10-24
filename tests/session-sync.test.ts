import test from 'node:test';
import assert from 'node:assert/strict';
import {SessionSync,readSse} from '../frontend/session-sync.js';

const deferred=()=>{let resolve!:(v:any)=>void;const promise=new Promise<any>(r=>{resolve=r;});return {resolve,promise};};
test('a new presentation version refreshes a saved result even if its business revision has not changed',async()=>{
 let state:any={session_id:'a',revision:4,last_event_id:10,presentation_version:'old',recommendation:{recommendations:Array(9).fill({})}};
 const next={...state,presentation_version:'new',recommendation:{recommendations:Array(5).fill({})}};
 const sync=new SessionSync({getSession:()=>'a',getState:()=>state,fetchState:async()=>next,applyState:(s:any)=>{state=s;}});
 await sync.refresh();assert.equal(state.recommendation.recommendations.length,5);assert.equal(state.presentation_version,'new');
});
test('an event during a state fetch forces another read and old snapshots cannot overwrite final messages',async()=>{
  const reads=[deferred(),deferred()];let calls=0;
  let state:any={session_id:'a',revision:4,last_event_id:10,messages:['question']};
  const sync=new SessionSync({getSession:()=>'a',getState:()=>state,fetchState:()=>reads[calls++].promise,applyState:(s:any)=>{state=s;}});
  const first=sync.refresh(),second=sync.refresh();assert.equal(first,second);assert.equal(calls,1);
  reads[0].resolve({...state,revision:3,messages:[]});await new Promise(r=>setImmediate(r));
  assert.equal(calls,2);assert.deepEqual(state.messages,['question']);
  reads[1].resolve({...state,revision:5,last_event_id:11,messages:['question','answer']});await first;
  assert.deepEqual(state.messages,['question','answer']);
});
test('switching sessions discards the previous session response',async()=>{
  const pending=deferred();let session='a',state:any={session_id:'b',revision:1};
  const sync=new SessionSync({getSession:()=>session,getState:()=>state,fetchState:()=>pending.promise,applyState:(s:any)=>{state=s;}});
  const first=sync.refresh();session='b';pending.resolve({session_id:'a',revision:200});await first;assert.equal(state.session_id,'b');
});
test('failed state reads can be retried independently of a new query',async()=>{
  let calls=0,state:any=null;const sync=new SessionSync({getSession:()=>'a',getState:()=>state,fetchState:async()=>{if(++calls===1)throw Error('offline');return {session_id:'a',revision:2};},applyState:(s:any)=>{state=s;}});
  await assert.rejects(sync.refresh(),/offline/);await sync.refresh();assert.equal(state.revision,2);
});
test('SSE delivers UTF-8 and CRLF split across chunks before the stream closes; heartbeat counts as activity',async()=>{
  let controller!:ReadableStreamDefaultController<Uint8Array>,activity=0;
  const stream=new ReadableStream<Uint8Array>({start(c){controller=c;}}),reader=readSse(new Response(stream),{onActivity:()=>activity++});
  const next=reader.next(),bytes=new TextEncoder().encode(': heartbeat\r\n\r\nid: 9\r\ndata: {"type":"workflow_result","text":"已完成"}\r\n\r\n');
  for(let i=0;i<bytes.length;i++)controller.enqueue(bytes.slice(i,i+1));
  const value=await next;assert.equal(value.value?.id,9);assert.equal(value.value?.event.text,'已完成');assert(activity>2);await reader.return();
});
