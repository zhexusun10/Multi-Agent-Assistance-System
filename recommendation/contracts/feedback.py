from datetime import datetime
from typing import Literal
from pydantic import Field, field_validator
from .requirements import Contract


class ViewPayload(Contract):
    view_session_id: str = Field(min_length=1)
    duration_seconds: float = Field(ge=0)


class ViewingEvent(Contract):
    event_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    recommendation_request_id: str = Field(min_length=1)
    listing_id: str = Field(min_length=1)
    event_type: Literal["listing_view_duration"] = "listing_view_duration"
    source: Literal["sample"] = "sample"
    occurred_at: datetime
    payload: ViewPayload

    @field_validator("occurred_at")
    @classmethod
    def aware(cls, v):
        if v.tzinfo is None:
            raise ValueError("timezone required")
        return v
