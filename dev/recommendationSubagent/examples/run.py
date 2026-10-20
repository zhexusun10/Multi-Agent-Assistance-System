"""Explicit fixture/scripted integration walkthrough; no external services."""

import argparse, json, tempfile
from pathlib import Path
from dev.sharedContract.recommendationSubagentContract.request import (
    RecommendationRequest,
)
from dev.sharedContract.recommendationSubagentContract.feedback import ViewingEvent
from ..repositories.connections import repositories
from ..state.store import StateStore
from ..service import RecommendationService
from ..agent.controller import ScriptedController
from ..terminal.__main__ import feedback_example
from ..schemas import validate_response


def run(path):
    listing, geo = repositories("fixture")
    store = StateStore(path)
    service = RecommendationService(listing, geo, store, ScriptedController())
    requirements = json.loads((Path(__file__).parent / "requirements.json").read_text())
    request = RecommendationRequest(
        request_id="initial",
        conversation_id="sample",
        requirements_version=1,
        requirements=requirements,
    )
    first = service.recommend(request)
    validate_response(first)
    assert first.status == "success", first.error
    events = [
        ViewingEvent.model_validate({**e, "conversation_id": "sample"})
        for e in feedback_example(first)["events"]
    ]
    next_request = request.model_copy(
        update={
            "request_id": "feedback",
            "feedback": events,
            "previous_state_id": first.state.state_id,
        }
    )
    second = service.recommend(next_request)
    validate_response(second)
    assert second.status == "success", second.error
    repeated = service.recommend(next_request)
    assert repeated == second
    stricter = request.requirements.model_copy(deep=True)
    stricter.criteria[0].value = 2300.0
    third = service.recommend(
        request.model_copy(
            update={
                "request_id": "tightened",
                "requirements_version": 2,
                "requirements": stricter,
                "previous_state_id": second.state.state_id,
            }
        )
    )
    validate_response(third)
    assert all(r.listing.price <= 2300 for r in third.recommendations)
    print(
        json.dumps(
            {
                "data_mode": "fixture",
                "controller": "scripted",
                "initial": [r.listing.listing_id for r in first.recommendations],
                "feedback": [r.listing.listing_id for r in second.recommendations],
                "tightened": [r.listing.listing_id for r in third.recommendations],
                "trace_id": third.execution.trace_id,
            },
            indent=2,
        )
    )
    service.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        run(Path(directory) / "sample.sqlite")
