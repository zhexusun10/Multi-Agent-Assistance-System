from typing import Literal
from pydantic import Field, field_validator, model_validator
from .requirements import Contract, Requirements
from .feedback import FeedbackSnapshot, Identifier
from .limits import CONTRACT_VERSION, MAX_RESULT_LIMIT


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
    contract_version: Literal["2.0"] = CONTRACT_VERSION
    request_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    requirements_version: int = Field(ge=1)
    requirements: Requirements
    previous_state_id: str | None = None
    # None -> recommendation.default_result_limit of the run's settings.
    result_limit: int | None = Field(default=None, ge=1, le=MAX_RESULT_LIMIT)
    feedback: FeedbackSnapshot = Field(default_factory=FeedbackSnapshot)
    cold_start: ColdStartInput = Field(default_factory=ColdStartInput)

    @field_validator("contract_version", mode="before")
    @classmethod
    def supported_version(cls, value):
        if value != CONTRACT_VERSION:
            raise ValueError(
                f"UNSUPPORTED_VERSION: native contract {CONTRACT_VERSION} required, got {value!r}"
            )
        return value
