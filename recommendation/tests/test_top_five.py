from pydantic import ValidationError
import pytest
from recommendation.contracts.request import RecommendationRequest
from recommendation.tests.test_recommendation import req, service


def test_default_and_explicit_top_five_keep_the_original_ranked_prefix(service):
    body = req().model_dump(exclude={'result_limit'})
    default = RecommendationRequest.model_validate(body)
    assert default.result_limit == 5
    response = service.recommend(default)
    assert response.status == 'success'
    assert len(response.recommendations) == 5
    assert [r.rank for r in response.recommendations] == [1, 2, 3, 4, 5]
    original = service.recommend(req(request_id='four-reference', conversation_id='independent', result_limit=4))
    assert [r.listing.listing_id for r in response.recommendations[:4]] == [r.listing.listing_id for r in original.recommendations]
    assert [r.scores for r in response.recommendations[:4]] == [r.scores for r in original.recommendations]
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate({**body, 'result_limit': 6})
