"""Real-source contract extension (main) after the recommendation v2 merge: web_detail events are
validated and labelled, behavior_context carries the cumulative browsing that is scored, and the
recommendation kernels change only through declared downstream patches."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json

from recommendation.contracts.request import RecommendationRequest
from recommendation.contracts.behavior import request_uuid
from recommendation.repositories.connections import repositories
from recommendation.state.store import StateStore
from recommendation.agent.controller import ScriptedController
from recommendation.service import RecommendationService


def test_real_feedback_is_validated_labelled_and_behavior_context_is_scored(tmp_path):
    listings, geo = repositories('fixture')
    store = StateStore(tmp_path / 'state.sqlite')
    service = RecommendationService(listings, geo, store, ScriptedController())
    criteria = {'listing_type': 'RENT', 'criteria': [{'criterion_id': 'rent', 'field': 'price', 'operator': 'lte', 'value': 100000, 'unit': 'SGD/month', 'strength': 'hard'}]}
    try:
        first = service.recommend(RecommendationRequest(request_id='initial', conversation_id='web-test', requirements_version=1, requirements=criteria))
        assert first.recommendations
        listing = first.recommendations[0].listing.listing_id
        now = datetime.now(timezone.utc)
        event = {'event_id': 'web-segment-1', 'conversation_id': 'web-test', 'recommendation_request_id': 'initial', 'listing_id': listing, 'source': 'web_detail', 'occurred_at': now, 'payload': {'view_session_id': 'completed-1', 'duration_seconds': 31}}
        # The event alone is validated and labelled but never scored (no double counting).
        events_only = service.recommend(RecommendationRequest(request_id='events-only', conversation_id='web-test', requirements_version=1, requirements=criteria, previous_state_id=first.state.state_id, feedback=[event]))
        assert events_only.status == 'success' and all(r.scores.browsing_score is None for r in events_only.recommendations)
        profile = store.current('web-test')['profile']
        assert profile['feedback_events'] == {'count': 1, 'sources': ['web_detail'], 'scored': False}
        assert any('Browser detail durations' in warning for warning in events_only.warnings)
        # The cumulative summary in behavior_context is what is scored.
        context = {'version': '1.0', 'as_of': now.isoformat(), 'book_viewing_listings': [],
                   'listing_view_durations': [{'listing_id': listing, 'duration_seconds': 31, 'view_count': 1, 'last_viewed_at': now.isoformat(), 'source_request_ids': [request_uuid('initial')], 'interest_eligible': True}]}
        scored = service.recommend(RecommendationRequest(request_id='next', conversation_id='web-test', requirements_version=1, requirements=criteria, previous_state_id=events_only.state.state_id, feedback=[event], behavior_context=context))
        assert scored.status == 'success' and any(r.scores.browsing_score is not None for r in scored.recommendations)
        assert store.current('web-test')['profile']['browsing'][listing]['duration_seconds'] == 31
        # Re-sending the same snapshot under a new request ID adds nothing (no native accumulation).
        again = service.recommend(RecommendationRequest(request_id='retry-event', conversation_id='web-test', requirements_version=1, requirements=criteria, previous_state_id=scored.state.state_id, feedback=[event], behavior_context=context))
        previous_scores = {r.listing.listing_id: r.scores for r in scored.recommendations}
        next_scores = {r.listing.listing_id: r.scores for r in again.recommendations}
        common = previous_scores.keys() & next_scores.keys()
        assert common and all(previous_scores[i] == next_scores[i] for i in common)
        # A new request ID can change cold-start/tie exploration selection, not
        # accumulate the same browsing snapshot a second time.
        assert store.current('web-test')['profile']['browsing'][listing]['duration_seconds'] == 31
    finally:
        service.close()


def test_recommendation_kernels_change_only_through_declared_patches():
    """The extension's protected kernels either keep main's bytes or match a declared downstream patch
    (contracts/releases/recommendation-downstream-patches.json); nothing changes undeclared."""
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'contracts/releases/recommendation-web-feedback-extension.json').read_text())
    patches = {p['target']: p['patched_sha256'] for p in json.loads((root / 'contracts/releases/recommendation-downstream-patches.json').read_text())['files']}
    for name, expected in manifest['protected_kernels'].items():
        actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        assert actual in (expected, patches.get(name)), name
