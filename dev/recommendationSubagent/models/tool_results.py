from typing import Literal
from pydantic import Field
from dev.sharedContract.recommendationSubagentContract.requirements import Contract
from dev.sharedContract.recommendationSubagentContract.errors import ErrorDetail
from dev.sharedContract.recommendationSubagentContract.evidence import CriterionResult
from .listing_summary import ListingSummary


class ToolArguments(Contract):
    context_ref: str


class ScorePreview(Contract):
    explicit_score: float | None
    implicit_score: float | None
    combined_score: float
    explicit_weight: float
    implicit_weight: float
    evidence_coverage: float
    ordering: str


class CandidatePreview(Contract):
    listing: ListingSummary
    scores: ScorePreview | None = None
    criterion_results: list[CriterionResult] = Field(default_factory=list)


class ToolResult(Contract):
    call_id: str
    tool: str
    input_version: str
    output_version: str
    status: Literal["ok", "error"]
    result_ref: str
    candidate_ids: list[str]
    candidate_count: int = 0
    candidate_ids_truncated: bool = False
    ranked_ids_truncated: bool = False
    ranked_ids: list[str] = Field(default_factory=list)
    previews: list[CandidatePreview] = Field(default_factory=list, max_length=4)
    previews_truncated: bool = False
    verified_count: int = 0
    evidence_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: ErrorDetail | None = None
