from typing import Literal
from pydantic import Field, model_validator
from .requirements import Contract, Requirements
from .feedback import ViewingEvent


class RecommendationRequest(Contract):
    contract_version: Literal["1.0"] = "1.0"
    request_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    requirements_version: int = Field(ge=1)
    requirements: Requirements
    feedback: list[ViewingEvent] = Field(default_factory=list)
    previous_state_id: str | None = None
    result_limit: int = Field(default=5, ge=1, le=5)

    @model_validator(mode="after")
    def scoped(self):
        if any(e.conversation_id != self.conversation_id for e in self.feedback):
            raise ValueError("feedback conversation mismatch")
        return self
