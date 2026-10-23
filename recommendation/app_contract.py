"""Application-only behavior handoff. Upstream ranking contracts stay intact."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator, model_validator
from .contracts.request import RecommendationRequest
from .contracts.requirements import Contract


class ListingViewDuration(Contract):
    listing_id: str = Field(pattern=r'^\d{1,20}$')
    duration_seconds: float = Field(ge=0)
    view_count: int = Field(ge=1)
    last_viewed_at: datetime
    source_request_ids: list[UUID] = Field(min_length=1)
    interest_eligible: bool

    @field_validator('last_viewed_at')
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError('timezone required')
        return value


class BookViewingListing(Contract):
    listing_id: str = Field(pattern=r'^\d{1,20}$')
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
        if value.tzinfo is None:
            raise ValueError('timezone required')
        return value

    @model_validator(mode='after')
    def ordered(self):
        if self.ends_at <= self.starts_at or (self.ends_at-self.starts_at).total_seconds() > 14400:
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
        if value.tzinfo is None:
            raise ValueError('timezone required')
        return value


class ApplicationRecommendationRequest(RecommendationRequest):
    behavior_context: BehaviorContext | None = None

    def native_request(self):
        return RecommendationRequest.model_validate(self.model_dump(exclude={'behavior_context'}))
