import test from 'node:test';
import assert from 'node:assert/strict';
import {recommendationFailureMessage} from '../src/domain/errors.js';

test('recommendation failures use user-facing messages and keep diagnostics out of chat',()=>{
  for(const code of ['LOOP_EXHAUSTED','INVALID_SELECTION','STALE_EVIDENCE','HARD_CONSTRAINT_FAILED','REPAIR_NO_PROGRESS',
    'RANKING_INCOMPLETE','MODEL_NOT_CONFIGURED','MODEL_UNAVAILABLE','DATA_UNAVAILABLE','INTERNAL_ERROR']){
    const message=recommendationFailureMessage(code);assert(message.includes('条件已保留'));
    assert(!/Model turn|time budget|exhausted|LOOP_|INVALID_|INTERNAL_|STALE_|REPAIR_|RECOMMENDATION_LLM/.test(message));
  }
  assert(!recommendationFailureMessage('MODEL_NOT_CONFIGURED').includes('稍后重试'));// a configuration problem, not a retry
});
