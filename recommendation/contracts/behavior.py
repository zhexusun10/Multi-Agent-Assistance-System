"""behavior_context: the orchestrator's read-only behaviour snapshot as of `as_of` (main contract,
docs/Recommendation_Contract_浏览时长与看房意向.md). Session-cumulative per listing; never merged
with earlier requests on the native side."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from pydantic import Field, field_validator, model_validator
from .requirements import Contract

# The app (src/contracts/behavior-context.ts) sends canonical numeric listing IDs; the native
# side accepts any native listing identifier (fixture data mode uses e.g. "fixture-01"), a
# superset that every app body satisfies.
ListingId = Annotated[str, Field(min_length=1, max_length=128)]


def _aware(value):
    if value.tzinfo is None:
        raise ValueError('timezone required')
    return value


class ListingViewDuration(Contract):
    listing_id: ListingId
    duration_seconds: float = Field(ge=0)
    view_count: int = Field(ge=1)
    last_viewed_at: datetime
    source_request_ids: list[UUID] = Field(min_length=1)
    interest_eligible: bool

    @field_validator('last_viewed_at')
    @classmethod
    def aware(cls, value):
        return _aware(value)


class BookViewingListing(Contract):
    listing_id: ListingId
    action_id: UUID
    source_request_id: UUID
    requirements_version: int = Field(ge=0)
    starts_at: datetime
    ends_at: datetime
    time_zone: Literal['Asia/Singapore']
    recorded_at: datetime
    signal: Literal['viewing_draft_saved']
    appointment_confirmed: Literal[False]

    @field_validator('starts_at', 'ends_at', 'recorded_at')
    @classmethod
    def aware(cls, value):
        return _aware(value)

    @model_validator(mode='after')
    def ordered(self):
        if self.ends_at <= self.starts_at or (self.ends_at - self.starts_at).total_seconds() > 14400:
            raise ValueError('invalid viewing interval')
        return self


class BehaviorContext(Contract):
    version: Literal['1.0']
    as_of: datetime
    listing_view_durations: list[ListingViewDuration]
    book_viewing_listings: list[BookViewingListing]

    @field_validator('as_of')
    @classmethod
    def aware(cls, value):
        return _aware(value)

    @model_validator(mode='after')
    def one_summary_per_listing(self):
        for name in ('listing_view_durations', 'book_viewing_listings'):
            ids = [item.listing_id for item in getattr(self, name)]
            if len(ids) != len(set(ids)):
                raise ValueError(f'duplicate listing summary in {name}')
        return self


def request_uuid(request_id):
    """behavior_context source_request_ids are UUIDs (the app's request IDs). Tooling that
    simulates the orchestrator with non-UUID request IDs (fixtures, terminal) maps them
    deterministically."""
    from uuid import NAMESPACE_URL, uuid5

    try:
        return str(UUID(str(request_id)))
    except ValueError:
        return str(uuid5(NAMESPACE_URL, f"recommendation-request:{request_id}"))


# Orchestrator interest gate (main src/domain/behavior.ts INTEREST_POLICY), used ONLY by tooling
# that simulates the orchestrator; the native service trusts interest_eligible as sent.
INTEREST_POLICY = {"minimum_listing_ms": 15000, "minimum_listings": 2, "minimum_total_ms": 60000,
                   "listing_cap_ms": 120000}


def simulated_interest_eligibility(durations_seconds):
    """{listing_id: interest_eligible} for cumulative seconds per listing, emulating main's gate:
    ready when >=2 listings have >=15 s and their capped total >=60 s; eligible = ready and >=15 s."""
    policy = INTEREST_POLICY
    qualifying = {k: v for k, v in durations_seconds.items() if v * 1000 >= policy["minimum_listing_ms"]}
    total = sum(min(v * 1000, policy["listing_cap_ms"]) for v in qualifying.values())
    ready = len(qualifying) >= policy["minimum_listings"] and total >= policy["minimum_total_ms"]
    return {k: ready and k in qualifying for k in durations_seconds}
