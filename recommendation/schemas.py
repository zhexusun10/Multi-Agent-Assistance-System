import json, argparse
from pathlib import Path
from recommendation.contracts.request import (
    RecommendationRequest,
)
from recommendation.contracts.requirements import Requirements
from recommendation.contracts.feedback import ViewingEvent
from recommendation.contracts.behavior import BehaviorContext
from .models.candidate import Response

DIRECTORY = (
    Path(__file__).parent / "contracts/schemas"
)


def generate(check=False):
    DIRECTORY.mkdir(exist_ok=True)
    for name, model in [
        ("request", RecommendationRequest),
        ("response", Response),
        ("requirements", Requirements),
        ("feedback", ViewingEvent),
        ("behavior-context", BehaviorContext),
    ]:
        expected = (
            json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        )
        path = DIRECTORY / f"{name}.schema.json"
        if check:
            if not path.exists() or path.read_text() != expected:
                raise ValueError(f"Stale generated schema: {path}")
        else:
            path.write_text(expected)


def validate_response(response):
    import jsonschema

    jsonschema.validate(
        response.model_dump(mode="json"),
        json.loads((DIRECTORY / "response.schema.json").read_text()),
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    generate(p.parse_args().check)
