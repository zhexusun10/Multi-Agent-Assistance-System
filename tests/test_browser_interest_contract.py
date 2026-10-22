"""Real-source contract extension leaves original recommendation calculations intact."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json

from recommendation.contracts.request import RecommendationRequest
from recommendation.repositories.connections import repositories
from recommendation.state.store import StateStore
from recommendation.agent.controller import ScriptedController
from recommendation.service import RecommendationService


def test_real_feedback_is_persistent_deduplicated_and_identified(tmp_path):
    listings, geo = repositories('fixture')
    store = StateStore(tmp_path / 'state.sqlite')
    service = RecommendationService(listings, geo, store, ScriptedController())
    criteria = {'listing_type': 'RENT', 'criteria': [{'criterion_id': 'rent', 'field': 'price', 'operator': 'lte', 'value': 100000, 'unit': 'SGD/month', 'strength': 'hard'}]}
    try:
        first = service.recommend(RecommendationRequest(request_id='initial', conversation_id='web-test', requirements_version=1, requirements=criteria))
        assert first.recommendations
        event = {'event_id': 'web-segment-1', 'conversation_id': 'web-test', 'recommendation_request_id': 'initial', 'listing_id': first.recommendations[0].listing.listing_id, 'source': 'web_detail', 'occurred_at': datetime.now(timezone.utc), 'payload': {'view_session_id': 'completed-1', 'duration_seconds': 31}}
        second = service.recommend(RecommendationRequest(request_id='next', conversation_id='web-test', requirements_version=1, requirements=criteria, previous_state_id=first.state.state_id, feedback=[event]))
        assert second.status == 'success' and second.state.feedback_version == 1
        assert any(r.scores.implicit_score is not None for r in second.recommendations)
        profile = store.current('web-test')['profile']
        assert profile['source'] == 'web_detail'
        assert profile['duration_totals'][event['listing_id']] == 31
        assert any('Browser detail durations' in warning for warning in second.warnings)
        third = service.recommend(RecommendationRequest(request_id='retry-event', conversation_id='web-test', requirements_version=1, requirements=criteria, previous_state_id=second.state.state_id, feedback=[event]))
        assert third.state.feedback_version == 1
        assert store.current('web-test')['profile']['duration_totals'][event['listing_id']] == 31
    finally:
        service.close()


def test_original_ranking_validation_and_tool_kernels_are_byte_preserved():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'contracts/releases/recommendation-web-feedback-extension.json').read_text())
    for name, expected in manifest['protected_kernels'].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
