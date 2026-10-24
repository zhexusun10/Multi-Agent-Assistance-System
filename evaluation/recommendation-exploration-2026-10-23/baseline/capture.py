"""Capture the P1 baseline fixtures (fixture data mode, ScriptedController).

Run once at commit 4d36507 BEFORE any recommendation v2 change:

    .venv/bin/python evaluation/recommendation-exploration-2026-10-23/baseline/capture.py

Writes <round>.request.json / .response.json / .trace.json for four rounds, copies the
existing recommendation/examples/contracts/*.json and records MANIFEST.json (sha256 per
file, git HEAD, python version). Volatile values (state_id, trace_id, durations) differ per
run; listing ids, ranks, criterion results and score breakdowns are deterministic.
"""

import hashlib, json, platform, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from recommendation.contracts.request import RecommendationRequest  # noqa: E402
from recommendation.contracts.requirements import Requirements  # noqa: E402
from recommendation.contracts.feedback import ViewingEvent  # noqa: E402
from recommendation.repositories.connections import repositories  # noqa: E402
from recommendation.service import RecommendationService  # noqa: E402
from recommendation.state.store import StateStore  # noqa: E402
from recommendation.agent.controller import ScriptedController  # noqa: E402

OUT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "recommendation/examples"


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def main():
    requirements = json.loads((EXAMPLES / "requirements.json").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        listing, geographic = repositories("fixture")
        service = RecommendationService(
            listing, geographic, StateStore(Path(tmp) / "state.sqlite"), ScriptedController()
        )
        try:
            rounds = []
            initial = RecommendationRequest(
                request_id="baseline-initial", conversation_id="baseline",
                requirements_version=1, requirements=requirements,
            )
            first = service.recommend(initial)
            rounds.append(("initial", initial, first))
            events = [
                ViewingEvent.model_validate({
                    "event_id": f"baseline-event-{i}",
                    "conversation_id": "baseline",
                    "recommendation_request_id": first.request_id,
                    "listing_id": item.listing.listing_id,
                    "event_type": "listing_view_duration",
                    "source": "sample",
                    "occurred_at": "2026-10-23T00:00:00+00:00",
                    "payload": {"view_session_id": f"baseline-view-{i}", "duration_seconds": 90 if i == 0 else 30},
                })
                for i, item in enumerate(first.recommendations)
            ]
            feedback = initial.model_copy(update={
                "request_id": "baseline-feedback", "feedback": events,
                "previous_state_id": first.state.state_id,
            })
            second = service.recommend(feedback)
            rounds.append(("feedback", feedback, second))
            changed = initial.requirements.model_copy(deep=True)
            changed.criteria[-1].value = ["D21"]
            update = initial.model_copy(update={
                "request_id": "baseline-preference-update", "requirements": changed,
                "requirements_version": 2, "previous_state_id": second.state.state_id,
            })
            third = service.recommend(update)
            rounds.append(("preference-update", update, third))
            geography = RecommendationRequest(
                request_id="baseline-geography", conversation_id="baseline-geography",
                requirements_version=1,
                requirements=Requirements.model_validate_json((EXAMPLES / "geography-fixture.json").read_text()),
            )
            fourth = service.recommend(geography)
            rounds.append(("geography", geography, fourth))
            for name, request, response in rounds:
                assert response.status != "error", (name, response.error)
                write(f"{name}.request.json", request.model_dump(mode="json"))
                write(f"{name}.response.json", response.model_dump(mode="json"))
                write(f"{name}.trace.json", service.store.trace(request.conversation_id, request.request_id))
        finally:
            service.close()
    contracts = OUT / "examples-contracts"
    contracts.mkdir(exist_ok=True)
    for path in sorted((EXAMPLES / "contracts").glob("*.json")):
        shutil.copyfile(path, contracts / path.name)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "recommendation"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    files = {
        str(p.relative_to(OUT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(OUT.rglob("*.json")) if p.name != "MANIFEST.json"
    }
    write("MANIFEST.json", {
        "captured_at_commit": head,
        "recommendation_tree_clean": not dirty,
        "python": platform.python_version(),
        "data_mode": "fixture",
        "controller_mode": "scripted",
        "rounds": [name for name, _, _ in rounds],
        "volatile_fields": ["state.state_id", "state.candidate_pool_ref", "execution.trace_id", "execution.trace_ref", "execution.duration_ms", "trace step duration_ms / call_id / snapshot_ref"],
        "files": files,
    })


if __name__ == "__main__":
    main()
