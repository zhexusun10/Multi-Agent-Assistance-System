from recommendation.contracts.errors import RecommendationError
from ..models.listing_summary import ListingSummary
from ..tracing.trace import digest
import math


def prepare(request, previous, store):
    if previous:
        if request.previous_state_id != previous["state_id"]:
            raise RecommendationError(
                "STALE_REFERENCE", "Use the latest conversation state reference"
            )
        old = previous["request"]
        if request.requirements_version < old["requirements_version"]:
            raise RecommendationError(
                "STALE_REQUIREMENTS", "Requirements version moved backwards"
            )
        if (
            request.requirements.model_dump() != old["requirements"]
            and request.requirements_version <= old["requirements_version"]
        ):
            raise RecommendationError(
                "STALE_REQUIREMENTS", "Changed snapshot requires a newer version"
            )
    elif request.previous_state_id:
        raise RecommendationError("STALE_REFERENCE", "Unknown state reference")
    events = list(previous.get("events", [])) if previous else []
    by_id = {e["event_id"]: e for e in events}
    by_session = {e["payload"]["view_session_id"]: e for e in events}
    for event in request.feedback:
        e = event.model_dump(mode="json")
        existing = by_id.get(e["event_id"]) or by_session.get(
            e["payload"]["view_session_id"]
        )
        if existing:
            if existing != e:
                raise RecommendationError(
                    "FEEDBACK_CONFLICT",
                    "Conflicting event ID or completed view session",
                )
            continue
        prior = store.cached(request.conversation_id, event.recommendation_request_id)
        if not prior or not any(
            r["listing"]["listing_id"] == event.listing_id
            for r in prior[1]["recommendations"]
        ):
            raise RecommendationError(
                "INVALID_FEEDBACK",
                "Event must refer to a verified listing in an earlier response",
            )
        events.append(e)
        by_id[e["event_id"]] = e
        by_session[e["payload"]["view_session_id"]] = e
    totals = {}
    records = {}
    for e in events:
        prior = store.cached(request.conversation_id, e["recommendation_request_id"])
        item = next(
            r["listing"]
            for r in prior[1]["recommendations"]
            if r["listing"]["listing_id"] == e["listing_id"]
        )
        records[e["listing_id"]] = ListingSummary.model_validate(item)
        totals[e["listing_id"]] = (
            totals.get(e["listing_id"], 0) + e["payload"]["duration_seconds"]
        )
    return {
        "events": events,
        "viewed": [(records[k], v) for k, v in totals.items()],
        "sequence": previous["sequence"] + 1 if previous else 1,
        "changes": {
            "requirements_changed": not previous
            or digest(request.requirements.model_dump())
            != digest(previous["request"]["requirements"]),
            "new_events": len(events) - (len(previous["events"]) if previous else 0),
        },
        "profile": {
            "source": "sample",
            "duration_totals": totals,
            "interest_weights": {
                k: math.log1p(min(v, 120)) / math.log(121) for k, v in totals.items()
            },
            "duration_cap_seconds": 120,
            "scoring_version": "weighted-v1",
            "event_refs": [e["event_id"] for e in events],
        },
    }
