import test from 'node:test';
import assert from 'node:assert/strict';
import {publicRecommendationCandidates} from '../frontend/public-recommendations.js';
import {withPresentation,recommendationForDisplay} from '../src/shared/recommendation-display.js';
import {moreFixture} from './helpers/recommendation-more.js';

test('the browser renders all server-approved homes: five first, ten after append, with the original five intact',()=>{
  const {rec}=moreFixture(),first=recommendationForDisplay(withPresentation(rec)),expanded=recommendationForDisplay(withPresentation(rec,10));
  const old=publicRecommendationCandidates(first),all=publicRecommendationCandidates(expanded);
  assert.equal(old.length,5);assert.equal(all.length,10);
  assert.deepEqual(all.slice(0,5),old);assert.equal(new Set(all.map(c=>c.listing.listing_id)).size,10);
  // Presentation metadata belongs to the server. The browser must not silently
  // clip an already approved ten-row response when optional metadata is absent.
  delete expanded!.presentation;assert.equal(publicRecommendationCandidates(expanded).length,10);
  assert.deepEqual(publicRecommendationCandidates(null),[]);
});
