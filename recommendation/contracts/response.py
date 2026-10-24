from typing import Generic, TypeVar, Literal
from pydantic import Field, model_validator
from .requirements import Contract
from .evidence import Evidence, CriterionResult
from .errors import ErrorDetail

T = TypeVar("T", bound=Contract)


class StateReference(Contract):
    state_id: str | None = None
    candidate_pool_ref: str | None = None
    run_sequence: int = 0
    feedback_version: int = 0
    scope: str = "all RENT listings"
    truncated: bool = False
    source_count: int = 0
    prefilter_excluded: int = 0
    assessed_count: int = 0


class Execution(Contract):
    trace_id: str
    trace_ref: str
    strategy: str
    scoring_version: str = "weighted-v1"
    model_identifier: str | None = None
    model_calls: int = 0
    tool_calls: int = 0
    duration_ms: float = 0
    tokens: int | None = None
    data_mode: str
    controller_mode: str


class Unverified(Contract):
    listing_id: str
    criterion_results: list[CriterionResult]


class RecommendationResponse(Contract, Generic[T]):
    contract_version: Literal["1.0"] = "1.0"
    request_id: str
    conversation_id: str
    requirements_version: int
    status: Literal["success", "needs_clarification", "no_match", "error"]
    recommendations: list[T] = Field(default_factory=list, max_length=5)
    unverified_candidates: list[Unverified] = Field(default_factory=list, max_length=4)
    evidence: list[Evidence] = Field(default_factory=list)
    clarifications: list[str] = Field(default_factory=list)
    relaxation_suggestions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    state: StateReference
    execution: Execution
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def consistent(self):
        if (self.status == "success") != bool(self.recommendations):
            raise ValueError(
                "success requires recommendations; other statuses prohibit them"
            )
        if (self.status == "error") != (self.error is not None):
            raise ValueError("error details/status mismatch")
        return self
