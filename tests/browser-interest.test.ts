import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {ReadingClock} from '../frontend/view-tracker.js';
import {behaviorEventSchema,browserInterestNotice} from '../src/domain/behavior.js';
import {nativeRequest} from '../src/adapters/native-recommendation.js';

test('reading clock excludes hidden time, idle time and a suspended timer gap',()=>{
  const c=new ReadingClock(0);c.sample(0,true,0);
  for(let t=1000;t<=20000;t+=1000)c.sample(t,true,0);
  c.sample(20000,false,0);for(let t=21000;t<=80000;t+=1000)c.sample(t,false,0);
  c.sample(80000,true,80000);for(let t=81000;t<=95000;t+=1000)c.sample(t,true,80000);
  c.sample(95000,false,80000);for(let t=96000;t<=105000;t+=1000)c.sample(t,false,80000);
  assert.equal(c.total,35000);
  const idle=new ReadingClock(0);idle.sample(0,true,0);for(let t=1000;t<=60000;t+=1000)idle.sample(t,true,0);assert.equal(idle.total,30000);
  const gap=new ReadingClock(0);gap.sample(0,true,0);gap.sample(60000,true,60000);assert.equal(gap.total,0);gap.sample(61000,true,60000);assert.equal(gap.total,1000);
});

test('behavior contract rejects identity, fabricated attributes and duration errors',()=>{
  const base={event_id:randomUUID(),session_id:randomUUID(),source_request_id:randomUUID(),listing_id:'123',tab_id:randomUUID(),requirements_version:1,kind:'complete',view_session_id:randomUUID(),segment_id:randomUUID(),sequence:2,active_duration_ms:20000};
  assert(behaviorEventSchema.safeParse(base).success);
  for(const patch of [{active_duration_ms:-1},{active_duration_ms:Infinity},{listing_id:'not-a-listing'},{bedrooms:4},{sequence:1.5}])assert(!behaviorEventSchema.safeParse({...base,...patch}).success);
});

test('native adaptation preserves real source and duration; saves and untrusted telemetry stay separate',()=>{
  const feedback=['web_detail','sample','telemetry'].map((source,i)=>({event_id:String(i),listing_id:'123',occurred_at:new Date().toISOString(),event_type:'active_view' as const,payload:{source,recommendation_request_id:'previous',view_session_id:'segment'+i,active_duration_ms:31000}}));
  const result=nativeRequest({contract_version:'1.0',request_id:'req',conversation_id:'session',requirements_version:1,requirements:{listing_type:'RENT',criteria:[]},result_limit:5,feedback});
  assert.deepEqual(result.request.feedback.map(f=>f.source),['web_detail','sample']);assert.equal(result.request.feedback[0].payload.duration_seconds,31);assert.deepEqual(result.ignored_feedback,['2']);
});

test('explicitly covered features do not falsely claim that implicit preference changed ranking',()=>{
  const rec={native:{scores:{'123':{implicit_score:null}}}} as any;
  assert.equal(browserInterestNotice(true,rec)?.code,'WEB_INTEREST_NOT_APPLIED');
  rec.native.scores['123'].implicit_score=.6;assert.equal(browserInterestNotice(true,rec)?.code,'WEB_INTEREST');
  assert.equal(browserInterestNotice(false,rec),null);
});
