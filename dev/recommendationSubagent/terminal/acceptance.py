"""Repeatable acceptance companion to the interactive terminal.

Database/live is the default. Offline modes are explicitly labelled and never count
as live acceptance. No ingestion or database writes are performed.
"""

import argparse
import json
import uuid
from pydantic import ValidationError
from pathlib import Path
from dev.sharedContract.recommendationSubagentContract.request import (
    RecommendationRequest,
)
from dev.sharedContract.recommendationSubagentContract.requirements import Requirements
from dev.sharedContract.recommendationSubagentContract.feedback import ViewingEvent
from ..config import RecommendationConfig
from ..repositories.connections import ROOT, load_environment, repositories
from ..agent.controller import LiveController, ScriptedController
from ..state.store import StateStore
from ..service import RecommendationService
from ..schemas import validate_response
from ..tracing.trace import now
from .__main__ import check_connections, feedback_example

STEPS = (
    "connections",
    "initial_search",
    "geographic_lookup",
    "feedback",
    "duplicate_feedback",
    "tighter_requirements",
    "trace_inspection",
    "restart",
)


def run_acceptance(
    output,
    requirements,
    *,
    data_mode="database",
    controller_mode="live",
    geography=None,
    config=None,
):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    conversation = str(uuid.uuid4())
    report = {
        "started_at": now(),
        "conversation_id": conversation,
        "data_mode": data_mode,
        "controller_mode": controller_mode,
        "live_modes": data_mode == "database" and controller_mode == "live",
        "steps": {},
        "note": "Automated companion; does not replace the interactive manual walkthrough.",
    }

    def record(step, status, **details):
        report["steps"][step] = {"status": status, **details}

    def save():
        states = [step["status"] for step in report["steps"].values()]
        report["status"] = (
            "failed"
            if "failed" in states or "execution_error_type" in report
            else ("blocked" if "blocked" in states else "passed")
        )
        report["database_acceptance"] = (
            report["status"] if data_mode == "database" else "not_run_fixture"
        )
        report["live_acceptance"] = (
            report["status"]
            if report["live_modes"]
            else (
                "not_run_scripted_controller"
                if data_mode == "database"
                else "not_run_offline"
            )
        )
        path = output / "acceptance.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        return report

    connections = check_connections(data_mode, controller_mode)
    connected = data_mode == "fixture" or all(
        connections[k].get("contacted") for k in ("listing", "neo4j")
    )
    configured = controller_mode == "scripted" or connections["llm"]["configured"]
    if not connected or not configured:
        record("connections", "blocked", details=connections)
        for step in STEPS[1:]:
            record(
                step,
                "blocked",
                reason="Required service connection or model configuration unavailable",
            )
        return save()
    record("connections", "passed", details=connections)
    state_path = output / f"{conversation}.sqlite"
    service = None
    latest = None
    version = 0
    accepted_requirements = None

    def create_service():
        listing, geo = repositories(data_mode)
        controller = (
            LiveController() if controller_mode == "live" else ScriptedController()
        )
        return RecommendationService(
            listing, geo, StateStore(state_path), controller, config=config
        )

    def recommend(step, snapshot, feedback=()):
        nonlocal latest, version, accepted_requirements
        if snapshot != accepted_requirements:
            version += 1
            accepted_requirements = snapshot.model_copy(deep=True)
        request = RecommendationRequest(
            request_id=str(uuid.uuid4()),
            conversation_id=conversation,
            requirements_version=version,
            requirements=snapshot,
            feedback=list(feedback),
            previous_state_id=latest.state.state_id if latest else None,
        )
        response = service.recommend(request)
        validate_response(response)
        (output / f"{step}.request.json").write_text(request.model_dump_json(indent=2))
        (output / f"{step}.response.json").write_text(
            response.model_dump_json(indent=2)
        )
        if response.status == "error":
            record(
                step,
                "failed",
                error=response.error.model_dump(),
                request_id=request.request_id,
            )
            return None
        latest = response
        record(
            step,
            "passed",
            request_id=request.request_id,
            response_status=response.status,
            verified_count=len(response.recommendations),
            assessed_count=response.state.assessed_count,
            model_identifier=response.execution.model_identifier,
            model_calls=response.execution.model_calls,
            tool_calls=response.execution.tool_calls,
        )
        return response

    try:
        service = create_service()
        initial = recommend("initial_search", requirements)
        if geography is None:
            if data_mode == "fixture":
                geography = Requirements.model_validate_json(
                    (
                        Path(__file__).parents[1] / "examples/geography-fixture.json"
                    ).read_text()
                )
            else:
                places = service.geographic.query(
                    "MATCH (p:Place {active:true,category:'School'}) RETURN p.place_id AS id ORDER BY p.place_id LIMIT 1"
                )
                if places:
                    geography = Requirements.model_validate(
                        {
                            "criteria": [
                                {
                                    "criterion_id": "school-radius",
                                    "field": "distance",
                                    "operator": "lte",
                                    "value": 1500,
                                    "unit": "m",
                                    "strength": "hard",
                                    "destination_id": places[0]["id"],
                                    "distance_type": "straight_line",
                                }
                            ]
                        }
                    )
        if geography:
            geographic_response = recommend("geographic_lookup", geography)
            if geographic_response and not geographic_response.evidence:
                record(
                    "geographic_lookup",
                    "blocked",
                    reason="No candidates/evidence available to verify geographic lookup",
                )
        else:
            record(
                "geographic_lookup",
                "blocked",
                reason="No active School destination resolved; criteria were not relaxed",
            )
        if initial and initial.recommendations:
            events = [
                ViewingEvent.model_validate({**e, "conversation_id": conversation})
                for e in feedback_example(initial)["events"]
            ]
            feedback_response = recommend("feedback", requirements, events)
            if feedback_response:
                duplicate = recommend("duplicate_feedback", requirements, events)
                if duplicate and (
                    duplicate.state.feedback_version
                    != feedback_response.state.feedback_version
                    or [r.scores for r in duplicate.recommendations]
                    != [r.scores for r in feedback_response.recommendations]
                ):
                    record(
                        "duplicate_feedback",
                        "failed",
                        reason="Duplicate feedback changed profile or scores; source changes also require inspection",
                    )
            else:
                record(
                    "duplicate_feedback",
                    "blocked",
                    reason="Feedback request did not commit",
                )
            prices = [
                r.listing.price
                for r in initial.recommendations
                if r.listing.price is not None
            ]
            if prices:
                # A separate, explicitly documented acceptance input, not a hidden search relaxation.
                cap = max(0, min(prices) - 1)
                criteria = [c.model_dump() for c in requirements.criteria]
                criteria.append(
                    {
                        "criterion_id": str(uuid.uuid4()),
                        "field": "price",
                        "operator": "lte",
                        "value": cap,
                        "unit": "SGD/month",
                        "strength": "hard",
                    }
                )
                tighter = None
                try:
                    tighter_snapshot = Requirements.model_validate(
                        {"criteria": criteria}
                    )
                except ValidationError:
                    record(
                        "tighter_requirements",
                        "blocked",
                        reason="The additional acceptance cap contradicts an existing criterion; original criteria were preserved",
                    )
                else:
                    tighter = recommend("tighter_requirements", tighter_snapshot)
                if tighter and any(
                    r.listing.price is None or r.listing.price > cap
                    for r in tighter.recommendations
                ):
                    record(
                        "tighter_requirements",
                        "failed",
                        reason="Returned listing violates explicit acceptance cap",
                    )
                report["steps"]["tighter_requirements"]["acceptance_budget_cap"] = cap
            else:
                record(
                    "tighter_requirements",
                    "blocked",
                    reason="Initial listings lack known prices",
                )
        else:
            for step in ("feedback", "duplicate_feedback", "tighter_requirements"):
                record(
                    step,
                    "blocked",
                    reason="Initial selected requirements yielded no verified listings; choose another explicit input to exercise feedback",
                )
        if latest:
            trace = service.store.resolve_reference(
                conversation, latest.execution.trace_ref
            )
            (output / "trace.json").write_text(json.dumps(trace, indent=2))
            passed_gate = any(
                s["kind"] == "final_gate" and s.get("passed") for s in trace["steps"]
            )
            record(
                "trace_inspection",
                "passed" if passed_gate else "failed",
                trace_id=latest.execution.trace_id,
            )
            before = service.store.current(conversation)
            service.close()
            service = create_service()
            after = service.store.current(conversation)
            record(
                "restart",
                "passed" if before == after else "failed",
                state_id=after["state_id"] if after else None,
            )
        else:
            for step in ("trace_inspection", "restart"):
                record(step, "blocked", reason="No committed response")
    except Exception as exc:
        # Never serialize connection/provider exception messages that may contain credentials.
        report["execution_error_type"] = type(exc).__name__
        for step in STEPS:
            if step not in report["steps"]:
                record(
                    step,
                    "blocked",
                    reason="Acceptance execution failed; inspect preceding step",
                )
    finally:
        if service:
            service.close()
    return save()


def main():
    load_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-mode", choices=["database", "fixture"], default="database"
    )
    parser.add_argument("--controller", choices=["live", "scripted"], default="live")
    parser.add_argument(
        "--requirements",
        default=str(Path(__file__).parents[1] / "examples/requirements.json"),
    )
    parser.add_argument("--geography")
    parser.add_argument("--config")
    parser.add_argument(
        "--output",
        default=str(
            ROOT / "dev/recommendationSubagent/.runtime/acceptance" / str(uuid.uuid4())
        ),
    )
    args = parser.parse_args()
    report = run_acceptance(
        args.output,
        Requirements.model_validate_json(Path(args.requirements).read_text()),
        data_mode=args.data_mode,
        controller_mode=args.controller,
        geography=(
            Requirements.model_validate_json(Path(args.geography).read_text())
            if args.geography
            else None
        ),
        config=RecommendationConfig.from_file(args.config),
    )
    print(json.dumps(report, indent=2))
    print(f"Artifacts: {Path(args.output).resolve()}")
    if report["status"] == "failed":
        raise SystemExit(1)
    if report["status"] == "blocked":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
