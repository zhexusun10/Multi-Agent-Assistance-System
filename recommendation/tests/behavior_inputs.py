"""Test helper: build main-contract behavior_context snapshots (simulating the orchestrator)."""
from recommendation.contracts.behavior import BehaviorContext, request_uuid

AS_OF = "2026-10-23T08:00:00Z"


def viewing_draft(listing_id, action="draft", request_id="previous-round", recorded_at=AS_OF):
    return {"listing_id": listing_id, "action_id": request_uuid(f"{action}-{listing_id}"),
            "source_request_id": request_uuid(request_id), "requirements_version": 1,
            "starts_at": "2026-10-24T06:00:00Z", "ends_at": "2026-10-24T07:00:00Z",
            "time_zone": "Asia/Singapore", "recorded_at": recorded_at, "signal": "viewing_draft_saved",
            "appointment_confirmed": False}


def behavior(browsing=(), appointments=(), request_id="previous-round", eligible=True, as_of=AS_OF, raw=False):
    """browsing = [(listing_id, cumulative seconds | None)] (None = no completed segment, omitted,
    as main's ledger does); ``eligible`` = bool or {listing_id: bool} for interest_eligible.
    appointments = [listing_id | (listing_id, anything)] saved timed viewing drafts."""
    views = []
    for listing_id, seconds in browsing:
        if seconds is None:
            continue
        ok = eligible.get(listing_id, True) if isinstance(eligible, dict) else eligible
        views.append({"listing_id": listing_id, "duration_seconds": seconds, "view_count": 1,
                      "last_viewed_at": as_of, "source_request_ids": [request_uuid(request_id)],
                      "interest_eligible": ok})
    drafts = [viewing_draft(a[0] if isinstance(a, (tuple, list)) else a) for a in appointments]
    data = {"version": "1.0", "as_of": as_of, "listing_view_durations": views, "book_viewing_listings": drafts}
    return data if raw else BehaviorContext.model_validate(data)
