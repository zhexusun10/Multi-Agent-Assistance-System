"""Run-consistency checks and behaviour-input summary for one request.

No native accumulation: every request carries the orchestrator's behavior_context (main contract,
session-cumulative listing_view_durations + book_viewing_listings as of `as_of`). Nothing is merged
with earlier requests. The `feedback` events are the incremental, quality-gated form of the same
browsing time; they are validated but not scored (the md forbids adding them to the cumulative
summary). Scoring is in ranking/scoring_v2.py (references inside the current pool only). Kept here
from the old context manager: optimistic concurrency (STALE_REFERENCE against the v2 head),
requirements version checks (STALE_REQUIREMENTS) and run_sequence. Idempotency/replay stay in the
service.
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
    """Service-level behaviour checks (schema-level checks live in the contracts)."""
    behavior = request.behavior_context
    if behavior is None:
        return
    counted = set(config.ranking.behavior.counted_appointment_signals)
    for item in behavior.book_viewing_listings:
        if item.signal not in counted:
            raise RecommendationError(
                "INVALID_FEEDBACK",
                f"Viewing signal {item.signal!r} for listing {item.listing_id} is not counted "
                f"(ranking.behavior.counted_appointment_signals: {sorted(counted)})",
            )


def prepare(request, previous, store, config):
    check_state(request, previous)
    check_feedback(request, store, config)
    behavior = request.behavior_context
    browsing = {}
    weights = {}
    appointments = []
    if behavior is not None:
        for item in behavior.listing_view_durations:
            browsing[item.listing_id] = {
                "duration_seconds": item.duration_seconds,
                "view_count": item.view_count,
                "interest_eligible": item.interest_eligible,
            }
            eligible = item.interest_eligible or not config.ranking.behavior.browsing_requires_interest_eligible
            if eligible and item.duration_seconds > 0:
                # Reference weight before the in-pool filter (applied by the scorer, recorded
                # in the rank_candidates trace step); None when the browsing branch is disabled.
                weights[item.listing_id] = (
                    browsing_weight(item.duration_seconds, config.ranking.browsing)
                    if browsing_enabled(config)
                    else None
                )
        appointments = sorted({item.listing_id for item in behavior.book_viewing_listings})
    return {
        "appointments": appointments,
        "sequence": previous["sequence"] + 1 if previous else 1,
        "changes": {
            "requirements_changed": not previous
            or digest(request.requirements.model_dump())
            != digest(previous["request"]["requirements"]),
            "behavior_as_of": behavior.as_of.isoformat() if behavior else None,
            "browsing_records": len(browsing),
            "appointment_listings": len(appointments),
            "feedback_events": len(request.feedback),
        },
        "profile": {
            "source": "behavior_context" if behavior is not None else "none",
            "as_of": behavior.as_of.isoformat() if behavior else None,
            "browsing": browsing,
            "interest_weights": weights,
            "browsing_transform": config.ranking.browsing.transform,
            "time_scale_seconds": config.ranking.browsing.time_scale_seconds,
            "duration_cap_seconds": config.ranking.browsing.duration_cap_seconds,
            "browsing_requires_interest_eligible": config.ranking.behavior.browsing_requires_interest_eligible,
            "appointment_listing_ids": appointments,
            "reference_scope": config.ranking.behavior.reference_scope,
            # Validated, not scored (no double counting with behavior_context).
            "feedback_events": {"count": len(request.feedback),
                                "sources": sorted({e.source for e in request.feedback}),
                                "scored": False},
            "scoring_version": SCORING_VERSION,
        },
    }
