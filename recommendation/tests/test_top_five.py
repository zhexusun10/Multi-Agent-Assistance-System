from pydantic import ValidationError
import pytest
from recommendation.contracts.request import RecommendationRequest
from recommendation.tests.test_recommendation import req, service


def test_app_explicit_top_five_uses_native_selection_without_changing_its_contract(service):
    body = req().model_dump(exclude={'result_limit'})
    default = RecommendationRequest.model_validate(body)
    assert default.result_limit is None  # native default is owned by its settings
    response = service.recommend(RecommendationRequest.model_validate({**body, 'result_limit': 5}))
    assert response.status == 'success'
    assert len(response.recommendations) == 5
    assert [r.rank for r in response.recommendations] == [1, 2, 3, 4, 5]
    assert response.selection.result_limit == 5
    assert all(all(c.outcome == 'pass' for c in r.criterion_results if c.criterion_id in {'rent', 'bedrooms'}) for r in response.recommendations)
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate({**body, 'result_limit': 11})
