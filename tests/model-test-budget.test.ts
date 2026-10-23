import test from 'node:test';
import assert from 'node:assert/strict';
import type {BaseChatModel} from '@langchain/core/language_models/chat_models';
import {modelTestPlan,budgetTestModel} from '../evaluation/model-test-budget.js';

test('model evaluation defaults to one dry-run case and no independent model judge',()=>{
  const p=modelTestPlan(['a','b'],[]);assert.equal(p.live,false);assert.equal(p.modelJudge,false);assert.deepEqual(p.cases,['a']);
  assert.equal(p.limits.requests,3);assert.equal(p.limits.concurrency,1);
  assert.throws(()=>modelTestPlan(['a','b'],['--limit','2']),/MODEL_TEST_CASE_LIMIT/);
  assert.equal(modelTestPlan(['a'],['--live']).live,true);
});

test('all structured writers and reviewers share one hard request cap, including failed calls',async()=>{
  let sent=0;
  const fake={withStructuredOutput(){return {invoke:async()=>{sent++;if(sent===2)throw Error('mock provider failure');return 'ok';}}}} as unknown as BaseChatModel;
  const b=budgetTestModel(fake),writer=b.model.withStructuredOutput({}),audit=b.model.withStructuredOutput({});
  assert.equal(await writer.invoke([]),'ok');await assert.rejects(audit.invoke([]),/mock provider failure/);assert.equal(await writer.invoke([]),'ok');
  await assert.rejects(audit.invoke([]),/MODEL_TEST_REQUEST_LIMIT/);assert.equal(sent,3);assert.equal(b.requests(),3);
});

test('parallel invocation cannot overspend the request cap',async()=>{
  let sent=0;
  const fake={invoke:async()=>{sent++;return 'ok';}} as unknown as BaseChatModel;
  const b=budgetTestModel(fake),r=await Promise.allSettled(Array.from({length:8},()=>b.model.invoke([])));
  assert.equal(r.filter(x=>x.status==='fulfilled').length,3);assert.equal(sent,3);assert.equal(b.requests(),3);
});

test('oversized test context is rejected before sending or consuming a request',async()=>{
  let sent=0;
  const b=budgetTestModel({invoke:async()=>{sent++;return 'ok';}} as unknown as BaseChatModel);
  await assert.rejects(b.model.invoke('x'.repeat(24001)),/MODEL_TEST_CONTEXT_LIMIT/);
  assert.equal(sent,0);assert.equal(b.requests(),0);
});
