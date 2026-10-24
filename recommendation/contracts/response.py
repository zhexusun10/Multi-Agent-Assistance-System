from typing import Generic, TypeVar, Literal
from pydantic import Field, model_validator
from .requirements import Contract
from .evidence import Evidence, CriterionResult
from .errors import ErrorDetail
from .limits import CONTRACT_VERSION, MAX_RESULT_LIMIT
from .summary import ResolutionResult, PoolSummary, SelectionSummary

T = TypeVar("T", bound=Contract)


class StateReference(Contract):
    state_id: str | None = None
    candidate_pool_ref: str | None = None
    run_sequence: int = 0
    scope: str = "all RENT listings"
    truncated: bool = False
    source_count: int = 0
    prefilter_excluded: int = 0
    assessed_count: int = 0


class Execution(Contract):
    trace_id: str
    trace_ref: str
    strategy: str
    scoring_version: str
    selection_algorithm: str
    config_version: int
    config_hash: str
    # Digest of the result-affecting settings only; selection seeds derive from it (spec R2).
    selection_hash: str
    model_identifier: str | None = None
    model_calls: int = 0
    tool_calls: int = 0
    duration_ms: float = 0
    tokens: int | None = None
    data_mode: str
    controller_mode: str


class RecommendationResponse(Contract, Generic[T]):
    contract_version: Literal["2.0"] = CONTRACT_VERSION
    request_id: str
    conversation_id: str
    requirements_version: int
    status: Literal["success", "needs_clarification", "no_match", "error"]
    recommendations: list[T] = Field(default_factory=list, max_length=MAX_RESULT_LIMIT)
    # Only evidence referenced by returned recommendations (never the whole pool). One entry per
    # (evidence_id, criterion_id): R4 evidence IDs carry no criterion_id, so criteria with the same
    # canonical key share an evidence_id, and each such criterion gets its own entry; a reference
    # in criterion_results always resolves to an entry carrying that result's criterion_id.
    evidence: list[Evidence] = Field(default_factory=list)
    resolution: list[ResolutionResult] = Field(default_factory=list)
    pool: PoolSummary | None = None
    selection: SelectionSummary | None = None
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
        if self.selection and len(self.recommendations) > self.selection.result_limit:
            raise ValueError("more recommendations than the effective result_limit")
        if self.pool and len(self.recommendations) > self.pool.size:
            raise ValueError("more recommendations than eligible pool listings")
        return self
