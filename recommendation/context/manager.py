"""Run-consistency checks and feedback-snapshot consumption for one request (contract 2.0).

No accumulation: every request carries the orchestrator's COMPLETE feedback snapshot
(previous-round browsing + all valid appointment signals). Nothing is merged with earlier
requests; earlier rounds' browsing therefore has no effect. The snapshot itself is scored by
ranking/scoring_v2.py (references inside the current pool only). Kept here from the old context
manager: optimistic concurrency (STALE_REFERENCE against the v2 head), requirements version
checks (STALE_REQUIREMENTS) and run_sequence. Idempotency/replay stay in the service.
"""

from recommendation.contracts.errors import RecommendationError
from ..tracing.trace import digest
from ..ranking.scoring_v2 import SCORING_VERSION, browsing_enabled, browsing_weight


def check_state(request, previous):
    """STALE_REFERENCE / STALE_REQUIREMENTS against the conversation's v2 head (or none)."""
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


def check_feedback(request, store, config):
    """Service-level feedback checks; returns the previous round's committed response (or None)."""
    counted = set(config.ranking.behavior.counted_appointment_statuses)
    for signal in request.feedback.appointments:
        outside = sorted(set(signal.statuses) - counted)
        if outside:
            raise RecommendationError(
                "INVALID_FEEDBACK",
                f"Appointment statuses {outside} for listing {signal.listing_id} are not counted "
                f"(ranking.behavior.counted_appointment_statuses: {sorted(counted)})",
            )
    round_ = request.feedback.previous_round
    if round_ is None:
        return None
    # The orchestrator's adopted round need not be the native head, but it must be a committed
    # v2 run of THIS conversation whose recommendation ID set equals the shown IDs.
    prior = store.cached(request.conversation_id, round_.request_id)
    if not prior:
        raise RecommendationError(
            "INVALID_FEEDBACK",
            "previous_round.request_id is not a committed run of this conversation",
        )
    returned = {item["listing"]["listing_id"] for item in prior[1]["recommendations"]}
    if returned != set(round_.shown_listing_ids):
        raise RecommendationError(
            "INVALID_FEEDBACK",
            "previous_round.shown_listing_ids must equal the recommendation IDs of that run",
        )
    return prior[1]


def prepare(request, previous, store, config):
    check_state(request, previous)
    check_feedback(request, store, config)
    feedback = request.feedback
    round_ = feedback.previous_round
    browsing = {}
    weights = {}
    if round_:
        records = {record.listing_id: record for record in round_.browsing}
        for listing_id in round_.shown_listing_ids:  # display order, independent of record order
            record = records[listing_id]
            browsing[listing_id] = {
                "status": record.status,
                "duration_seconds": record.duration_seconds,
            }
            if record.status == "recorded" and record.duration_seconds > 0:
                # Reference weight before the in-pool filter (applied by the scorer, recorded
                # in the rank_candidates trace step); None when the browsing branch is disabled.
                weights[listing_id] = (
                    browsing_weight(record.duration_seconds, config.ranking.browsing)
                    if browsing_enabled(config)
                    else None
                )
    appointments = sorted(signal.listing_id for signal in feedback.appointments)
    return {
        "appointments": appointments,
        "sequence": previous["sequence"] + 1 if previous else 1,
        "changes": {
            "requirements_changed": not previous
            or digest(request.requirements.model_dump())
            != digest(previous["request"]["requirements"]),
            "previous_round": round_.request_id if round_ else None,
            "browsing_records": len(browsing),
            "appointment_listings": len(appointments),
        },
        "profile": {
            "source": "feedback_snapshot",
            "previous_round": round_.request_id if round_ else None,
            "browsing": browsing,
            "interest_weights": weights,
            "browsing_transform": config.ranking.browsing.transform,
            "time_scale_seconds": config.ranking.browsing.time_scale_seconds,
            "duration_cap_seconds": config.ranking.browsing.duration_cap_seconds,
            "appointment_listing_ids": appointments,
            "reference_scope": config.ranking.behavior.reference_scope,
            "scoring_version": SCORING_VERSION,
        },
    }
