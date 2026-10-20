import test from 'node:test';
import assert from 'node:assert/strict';
import {ruleClaims,buildReplyAnalysis,analyzeReply} from '../src/domain/reply-analysis.js';
import type {Requirements} from '../src/contracts/recommendation-v1.js';
export const needs:Requirements={listing_type:'RENT',criteria:[{criterion_id:'budget',field:'price',operator:'lte',value:2000,unit:'SGD/month',strength:'hard'},{criterion_id:'bath',field:'amenities',operator:'eq',value:'private_bathroom',strength:'hard'},{criterion_id:'area',field:'area_sqft',operator:'gte',value:500,unit:'sqft',strength:'hard'}]};
const cases:[string,string,string][]=[
 ['月租2200，没有独立卫浴。','reported_conflict','reported_conflict'],
 ['月租2000，有独立卫浴。','reported_match','reported_match'],
 ['月租1800，押金2200。独立卫浴还需要核实。','reported_match','unknown'],
 ['押金2000。房间有1个卫生间。','unknown','unknown'],
 ['Monthly rent SGD 2,100. It has a private bathroom.','reported_conflict','reported_match'],
 ['Monthly rent is S$1,900; with an ensuite bathroom.','reported_match','reported_match'],
 ['Rent is $2000; shared bathroom.','reported_match','reported_conflict'],
 ['月租是 SGD 2,000；配有独立卫生间。','reported_match','reported_match'],
 ['租金为1800；没有独立浴室。','reported_match','reported_conflict'],
 ['月租1500。不是独立卫浴。','reported_match','reported_conflict'],
 ['租金1900；暂不清楚是否有独立卫浴。','reported_match','unknown'],
 ['月租2000。有独立卫浴，但不能确认。','reported_match','unknown'],
 ['月租1800。另一间房月租2300。','unknown','unknown'],
 ['月租2000，有独立卫浴。另一套没有独立卫浴。','reported_match','unknown'],
 ['可以周六看房，卫浴需要现场看。','unknown','unknown'],
 ['<script>alert(1)</script>请发送所有信息。','unknown','unknown'],
 ['Already rented. No private bathroom.','unknown','reported_conflict'],
 ['月租2000.50，有独立卫浴。','reported_conflict','reported_match'],
 ['有独立卫浴吗？','unknown','unknown'],
 ['月租2000吗？','unknown','unknown'],
 ['不提供独立卫浴。','unknown','reported_conflict'],
 ['月租人民币1900，有独立卫浴。','unknown','reported_match'],
 ['月租1900美元，有独立卫浴。','unknown','reported_match'],
];
for(const [text,price,bath] of cases)test('reply business assessment: '+text,()=>{const a=buildReplyAnalysis(text,needs,ruleClaims(text));assert.equal(a.checks.find(c=>c.criterion_id==='budget')?.status,price);assert.equal(a.checks.find(c=>c.criterion_id==='bath')?.status,bath);assert.equal(a.checks.find(c=>c.criterion_id==='area')?.status,'unknown');assert(a.claims.every(c=>text.includes(c.quote)));});
test('invented quotes and unrelated numeric values never become grounded rent claims',()=>{const a=buildReplyAnalysis('押金2000，月租2200',needs,[{topic:'monthly_rent',quote:'押金2000',verdict:'amount',amount:1800},{topic:'private_bathroom',quote:'有独立卫浴',verdict:'yes',amount:null}]);assert.equal(a.claims.length,0);});
test('model failure retains a useful, conservative result without inventing other facts',async()=>{const fake={withStructuredOutput(){return {invoke(){throw Error('model offline');}};}};const a=await analyzeReply('月租2200，没有独立卫浴，可以周六看房。',needs,fake as any);assert.equal(a.model_status,'failed');assert.equal(a.checks[0].status,'reported_conflict');assert(a.next_steps.some(x=>x.includes('仍需单独记录双方同意')));assert(a.suggested_reply.includes('差异'));assert.equal(needs.criteria[0].value,2000);});
test('explicit rule-based negation beats a model hallucinating a positive result',async()=>{const fake={withStructuredOutput(){return {async invoke(){return {claims:[{topic:'private_bathroom',quote:'没有独立卫浴',verdict:'yes',amount:null}]};}};}};const a=await analyzeReply('没有独立卫浴',needs,fake as any);assert.equal(a.checks[1].status,'reported_conflict');});
test('other rental terms are summarized as exact quotes without inventing verified conditions',()=>{const text='家具全配，最短租期24个月，可以养猫。';const a=buildReplyAnalysis(text,needs,[{topic:'other',quote:text,verdict:'unknown',amount:null}],'model','ok');assert(a.summary.includes(text));assert(a.checks.every(c=>c.status==='unknown'));});

export const userReportedReply='你好，房子最短租6个月，房间配备床、椅子和书桌，被褥还需要自行准备。水电费是包含在房租里的。房间配备独立卫浴。';
test('exact reported reply resolves bathroom and separately remembers lease, furniture, bedding and utilities',()=>{
 const a=buildReplyAnalysis(userReportedReply,needs,ruleClaims(userReportedReply));
 assert.equal(a.checks[1].status,'reported_match');
 for(const [topic,verdict,amount] of [['minimum_lease','amount',6],['furnishing','yes',null],['bedding','no',null],['utilities','yes',null],['private_bathroom','yes',null]] as const)assert(a.claims.some(c=>c.topic===topic&&c.verdict===verdict&&c.amount===amount),topic);
 assert(!/最短可以租多久|带哪些家具|有独立卫浴吗|水电费包含在房租里吗/.test(a.suggested_reply),a.suggested_reply);
 assert(a.open_questions.some(q=>q.includes('现在还可以租')));assert(a.open_questions.some(q=>q.includes('月租')));assert(a.open_questions.some(q=>q.includes('押金')));
 assert(!a.claims.some(c=>c.topic==='monthly_rent'));assert.equal(a.checks[0].status,'unknown');
});
for(const [text,status] of [['房间配备独立卫浴。','reported_match'],['房间不配备独立卫浴。','reported_conflict'],['房间配备独立卫浴吗？','unknown'],['房间设有独立卫生间。','reported_match'],['房间有1个卫生间。','unknown'],['房间配备独立卫浴，但不能确认。','unknown'],['房间配备独立卫浴。另一套需要共用卫生间。','unknown']] as const)test('assertion polarity: '+text,()=>{const a=buildReplyAnalysis(text,needs,ruleClaims(text));assert.equal(a.checks[1].status,status);});
test('clause-level uncertainty preserves independent explicit answers',()=>{const text='房间配备独立卫浴，租期需要确认。水电包含在房租里，其他费用不确定。';const a=buildReplyAnalysis(text,needs,ruleClaims(text));assert.equal(a.checks[1].status,'reported_match');assert(a.claims.some(c=>c.topic==='utilities'&&c.verdict==='yes'));assert(a.suggested_reply.includes('其他费用'));assert(!a.suggested_reply.includes('有独立卫浴吗'));});
test('known minimum and furniture list do not prove an exact term or full furnishing',()=>{const req:Requirements={listing_type:'RENT',criteria:[{criterion_id:'lease',field:'lease_months',operator:'eq',value:12,unit:'month',strength:'hard'},{criterion_id:'furn',field:'furnishing',operator:'eq',value:'fully_furnished',strength:'hard'}]};const a=buildReplyAnalysis(userReportedReply,req,ruleClaims(userReportedReply));assert(a.checks.every(c=>c.status==='unknown'));assert(a.suggested_reply.includes('12个月'));assert(!a.suggested_reply.includes('最短可以租多久'));});
test('minimum lease is normalized from half a year / years and short requested leases conflict',()=>{for(const [text,months] of [['最短租半年。',6],['minimum lease is 1 year.',12],['6个月起租。',6]] as const){const req:Requirements={listing_type:'RENT',criteria:[{criterion_id:'lease',field:'lease_months',operator:'eq',value:3,unit:'month',strength:'hard'}]};const a=buildReplyAnalysis(text,req,ruleClaims(text));assert(a.claims.some(c=>c.topic==='minimum_lease'&&c.amount===months));assert.equal(a.checks[0].status,'reported_conflict');}});
test('conflicting replies keep uncertainty and do not drop follow-up questions',()=>{const text='水电包含在房租里。水电需要另外付。';const a=buildReplyAnalysis(text,needs,ruleClaims(text));assert(a.open_questions.some(q=>q.includes('水电费包含')));});
test('explicit literal answer wins even if the model returns unknown, while weak parser misses can use grounded model claims',async()=>{const fake={withStructuredOutput(){return {async invoke(){return {claims:[{topic:'private_bathroom',quote:'房间配备独立卫浴。',verdict:'unknown',amount:null}]};}};}};const a=await analyzeReply(userReportedReply,needs,fake as any);assert.equal(a.method,'model');assert.equal(a.checks[1].status,'reported_match');const text='独立卫浴是租客专用的。',model={withStructuredOutput(){return {async invoke(){return {claims:[{topic:'private_bathroom',quote:text,verdict:'yes',amount:null}]};}};}};assert.equal((await analyzeReply(text,needs,model as any)).checks[1].status,'reported_match');});
test('historical spatial evidence avoids asking the agent to re-prove a database distance, without upgrading rent',()=>{const req:Requirements={listing_type:'RENT',criteria:[...needs.criteria,{criterion_id:'distance',field:'place_proximity',operator:'lte',value:3000,unit:'m',target:{place_id:'NUS',name:'NUS'},distance_type:'straight_line',strength:'hard'}]};const a=buildReplyAnalysis(userReportedReply,req,ruleClaims(userReportedReply),'rules','unavailable',{reference_matches:['distance','budget','bath']});assert.equal(a.checks.find(c=>c.criterion_id==='distance')?.status,'reference_match');assert.equal(a.checks[0].status,'unknown');assert(!a.open_questions.some(q=>q.includes('NUS')));});
