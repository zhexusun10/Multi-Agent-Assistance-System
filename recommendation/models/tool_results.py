from typing import Any, Literal
from pydantic import Field
from recommendation.contracts.requirements import Contract
from recommendation.contracts.errors import ErrorDetail
from recommendation.contracts.evidence import CriterionResult
from .listing_summary import ListingSummary


class ToolArguments(Contract):
    context_ref: str


class ScorePreview(Contract):
    soft_score: float | None
    behavior_score: float | None
    combined_score: float
    soft_weight: float
    behavior_weight: float
    soft_evidence_coverage: float
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
    # The final selection of the latest rank_candidates (the proposal must equal it exactly).
    selected_ids: list[str] = Field(default_factory=list)
    completed_stages: list[str] = Field(default_factory=list)
    # At most trace.preview_rows previews (settings); the full pool stays in pool_cache.
    previews: list[CandidatePreview] = Field(default_factory=list)
    previews_truncated: bool = False
    verified_count: int = 0
    evidence_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: ErrorDetail | None = None
    # Tool-specific result (resolution, search candidates, distances, ...); JSON data only.
    data: dict[str, Any] | None = None
