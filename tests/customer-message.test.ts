import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {ActionService,wireBody} from '../src/domain/actions.js';
import {composeCustomerMessage,formatViewingTime,customerNotes,notesFromFollowupQuery,legacyDraftNotes} from '../src/shared/followup-message.js';
import {customerQuestion} from '../src/domain/customer-questions.js';
import type {DomainState} from '../src/stores/domain.js';
import type {Criterion} from '../src/contracts/recommendation-v1.js';

const slot={starts_at:'2026-10-21T06:00:00.000Z',ends_at:'2026-10-21T07:00:00.000Z',time_zone:'Asia/Singapore'};
test('the exact user-reported Gold Coast example becomes a first-person inquiry with readable Singapore time',async()=>{
  const rec=JSON.parse(await readFile('tests/fixtures/recommendation-response.json','utf8'));rec.recommendations[0].listing={...rec.recommendations[0].listing,listing_id:'23583402',title:'Gold Coast Condo',price:1600};
  const s={session_id:rec.conversation_id,latest_request_id:rec.request_id,requirements_version:rec.requirements_version,recommendation:rec,requirements:{listing_type:'RENT',criteria:[]}} as unknown as DomainState;
  const query='请为房源ID 23583402 生成看房询问草稿，逐项询问尚未核实的条件。';
  const a=new ActionService(null as never).draft(s,['23583402'],'VIEWING',notesFromFollowupQuery(query));a.slots=slot;
  const body=wireBody(a);assert(body.includes('Gold Coast Condo (listing 23583402)'));assert(body.includes('still available to rent'));assert(body.includes('1,600 SGD. Is that still the current price'));assert(body.includes('Wed, 21 October 2026 2:00 PM–3:00 PM (Singapore time)'));assert(body.includes('Would that work for you'));assert((body).toLowerCase().endsWith("thank you!"));
  for(const bad of ['生成看房询问草稿','逐项询问尚未核实','需双方','不是已经预约成功','不代表预约','快照','数据来自','待双方协商','必须满足','2026-10-21T','尚无证据'])assert(!body.includes(bad),bad);
  assert.equal(a.negotiation_notes,undefined);assert.equal(a.status,'DRAFT');assert.equal(a.confirmation,undefined);assert.equal(a.execution,null);
});
const commands:[string,string][]=[
 ['请为房源ID 23583402 生成看房询问草稿，逐项询问尚未核实的条件。',''],
 ['请为房源ID 23583402 生成联系核实草稿，逐项询问尚未核实的条件。',''],
 ['请为房源ID 23583402 生成联系核实草稿。此前已经咨询，现希望跟进回复和未核实事项。',''],
 ['我想看第一套',''],['我要看这个房子',''],['请为第2套生成看房询问草稿',''],
 ['我想看第一套，希望月租协商到1500','希望月租协商到1500'],
 ['请为房源ID 23583402 生成看房询问草稿，顺便问下租金能否降到1500','租金能否降到1500'],
 ['请联系第1套，问一下可以养猫吗','可以养猫吗'],
];
for(const [query,expected] of commands)test('router instructions stay internal: '+query,()=>assert.equal(notesFromFollowupQuery(query),expected));
test('real negotiation notes survive once without internal qualification or duplicate signatures',()=>{
 const body=composeCustomerMessage('你好，我想了解这套房。\n\n谢谢！','希望月租协商至1500新币，另外我会带一只猫。',slot);assert.equal(body.match(/1500/g)?.length,1);assert((body).toLowerCase().includes("requests for discussion: 希望月租协商至1500新币"));assert(!body.includes('待双方'));assert.equal(body.match(/Thank you/gi)?.length,1);assert((body).toLowerCase().endsWith("thank you!"));
 assert.equal(composeCustomerMessage('你好，月租能协商到1500吗？','月租能协商到1500吗？'), '你好，月租能协商到1500吗？');assert.equal(customerNotes('想先发文字消息，请不要打电话。'),'想先发文字消息，请不要打电话。');
});
test('UTC instants are converted to Singapore local dates, including midnight, noon, and date rollover',()=>{
 assert.equal(formatViewingTime(slot),'Wed, 21 October 2026 2:00 PM–3:00 PM (Singapore time)');
 assert.equal(formatViewingTime({...slot,starts_at:'2026-10-20T16:00:00Z',ends_at:'2026-10-20T17:00:00Z'}),'Wed, 21 October 2026 12:00 AM–1:00 AM (Singapore time)');
 assert.equal(formatViewingTime({...slot,starts_at:'2026-10-21T04:00:00Z',ends_at:'2026-10-21T05:00:00Z'}),'Wed, 21 October 2026 12:00 PM–1:00 PM (Singapore time)');
 assert.equal(formatViewingTime({...slot,starts_at:'2026-10-21T15:30:00Z',ends_at:'2026-10-21T16:30:00Z'}),'Wed, 21 October 2026 11:30 PM to Thu, 22 October 2026 12:30 AM (Singapore time)');
 assert.equal(formatViewingTime({...slot,starts_at:'2026-10-21T14:00:00+08:00',ends_at:'2026-10-21T15:00:00+08:00'}),formatViewingTime(slot));
});
test('pending facts become conversational questions without upgrading their evidence status',()=>{
 const c=(field:string,value:unknown,operator:Criterion['operator']='eq')=>({criterion_id:field,field,value,operator,strength:'hard'} as Criterion);
 assert.equal(customerQuestion(c('amenities','private_bathroom')),"Does the room have a private bathroom, or is it shared with other people?");assert.equal(customerQuestion(c('amenities','pet_policy')),"Are pets allowed?");assert.equal(customerQuestion(c('rental_scope','whole_unit')),"Is this the whole unit, or only one room?");assert.equal(customerQuestion(c('furnishing','fully_furnished')),"Is it fully furnished? What furniture is included?");assert.equal(customerQuestion(c('lease_months',12)),'Could I rent it for 12 months?');assert(customerQuestion(c('move_in_date','2026-11-01','lte')).includes('on or before 2026-11-01'));
});
test('old generated command text can be recovered for rewriting while keeping real negotiation terms',()=>{
 const old='您好，我对 Gold Coast Condo（快照月租 SGD 1600）感兴趣。\n我的咨询与协商诉求（待双方协商）：请为房源ID 23583402 生成看房询问草稿，逐项询问尚未核实的条件。\n数据来自历史快照，尚未核实当前可租状态。谢谢。';assert.equal(legacyDraftNotes(old),'');assert.equal(legacyDraftNotes(old.replace('逐项询问尚未核实的条件。','希望月租协商到1500。')),'希望月租协商到1500。');
});
test('approved platform template body stays byte-for-byte identical to its reviewed text',()=>assert.equal(composeCustomerMessage('Hello, approved template.','希望1500',slot,true),'Hello, approved template.'));
