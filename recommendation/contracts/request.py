from typing import Annotated, Literal
from pydantic import Field, field_validator, model_validator
from .requirements import Contract, Requirements
from .feedback import ViewingEvent
from .behavior import BehaviorContext
from .limits import REQUEST_CONTRACT_VERSION, MAX_RESULT_LIMIT

Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class ColdStartInput(Contract):
    """Cold-start exit state managed by the orchestrator (no native history)."""

    exited: bool = False
    exited_pool_key: Identifier | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.exited != (self.exited_pool_key is not None):
            raise ValueError("exited_pool_key is required exactly when exited is true")
        return self


class RecommendationRequest(Contract):
    contract_version: Literal["1.0"] = REQUEST_CONTRACT_VERSION
    request_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    requirements_version: int = Field(ge=1)
    requirements: Requirements
    feedback: list[ViewingEvent] = Field(default_factory=list)
    previous_state_id: str | None = None
    # None -> recommendation.default_result_limit of the run's settings.
    result_limit: int | None = Field(default=None, ge=1, le=MAX_RESULT_LIMIT)
    behavior_context: BehaviorContext | None = None
    cold_start: ColdStartInput = Field(default_factory=ColdStartInput)

    @field_validator("contract_version", mode="before")
    @classmethod
    def supported_version(cls, value):
        if value != REQUEST_CONTRACT_VERSION:
            raise ValueError(
                f"UNSUPPORTED_VERSION: request contract {REQUEST_CONTRACT_VERSION} required, got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def scoped(self):
        if any(e.conversation_id != self.conversation_id for e in self.feedback):
            raise ValueError("feedback conversation mismatch")
        ids = [e.event_id for e in self.feedback]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate feedback event_id")
        return self
