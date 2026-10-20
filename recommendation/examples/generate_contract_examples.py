"""Generate validated examples from real fixture/service execution."""

import json, tempfile
from pathlib import Path
from recommendation.contracts.request import (
    RecommendationRequest,
)
from recommendation.contracts.feedback import ViewingEvent
from ..repositories.connections import repositories
from ..service import RecommendationService
from ..state.store import StateStore
from ..agent.controller import ScriptedController
from ..terminal.__main__ import feedback_example
from ..schemas import validate_response


def main():
    directory = Path(__file__).parent / "contracts"
    directory.mkdir(exist_ok=True)

    def save(name, model):
        (directory / f"{name}.json").write_text(model.model_dump_json(indent=2) + "\n")

    with tempfile.TemporaryDirectory() as tmp:
        a, b = repositories("fixture")
        service = RecommendationService(
            a, b, StateStore(Path(tmp) / "state.sqlite"), ScriptedController()
        )
        requirements = json.loads(
            (Path(__file__).parent / "requirements.json").read_text()
        )
        request = RecommendationRequest(
            request_id="sample-initial",
            conversation_id="contract-example",
            requirements_version=1,
            requirements=requirements,
        )
        first = service.recommend(request)
        save("initial-search", request)
        validate_response(first)
        save("success", first)
        events = [
            ViewingEvent.model_validate(
                {**e, "conversation_id": request.conversation_id}
            )
            for e in feedback_example(first)["events"]
        ]
        feedback = request.model_copy(
            update={
                "request_id": "sample-feedback",
                "feedback": events,
                "previous_state_id": first.state.state_id,
            }
        )
        save("feedback-only-update", feedback)
        second = service.recommend(feedback)
        changed = request.requirements.model_copy(deep=True)
        changed.criteria[-1].value = ["D21"]
        update = request.model_copy(
            update={
                "request_id": "sample-preference",
                "requirements_version": 2,
                "requirements": changed,
                "previous_state_id": second.state.state_id,
            }
        )
        save("preference-update", update)
        invalid = request.model_copy(update={"request_id": "stale"})
        error = service.recommend(invalid)
        validate_response(error)
        save("error", error)
        for name, criteria in [
            (
                "clarification",
                [
                    {
                        "criterion_id": "furnished",
                        "field": "furnishing",
                        "operator": "eq",
                        "value": "furnished",
                        "strength": "hard",
                    }
                ],
            ),
            (
                "no-match",
                [
                    {
                        "criterion_id": "budget",
                        "field": "price",
                        "operator": "lte",
                        "value": 1,
                        "unit": "SGD/month",
                        "strength": "hard",
                    }
                ],
            ),
        ]:
            r = RecommendationRequest(
                request_id=name,
                conversation_id=name,
                requirements_version=1,
                requirements={"criteria": criteria},
            )
            response = service.recommend(r)
            validate_response(response)
            save(name, response)
        service.close()


if __name__ == "__main__":
    main()
