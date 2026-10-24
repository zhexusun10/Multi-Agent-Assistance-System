from typing import Literal
from pydantic import Field, model_validator
from recommendation.contracts.requirements import Contract
from recommendation.contracts.evidence import CriterionResult
from recommendation.contracts.limits import MAX_RESULT_LIMIT
from recommendation.contracts.response import (
    RecommendationResponse,
)
from .listing_summary import ListingSummary
from .score_breakdown import ScoreBreakdown

SELECTION_REASONS = ("ranked", "leader", "tie_group_sample", "price_exploration_sample", "small_pool")


class RecommendationItem(Contract):
    listing: ListingSummary
    rank: int = Field(ge=1, le=MAX_RESULT_LIMIT)  # display order
    score_rank: int = Field(ge=1)  # position in the full-pool score order
    scores: ScoreBreakdown
    criterion_results: list[CriterionResult]
    evidence_refs: list[str]
    strengths: list[str]
    trade_offs: list[str]
    unknowns: list[str]
    selection_reason: Literal["ranked", "leader", "tie_group_sample", "price_exploration_sample", "small_pool"]
    selection_note: str = Field(min_length=1)


class Response(RecommendationResponse[RecommendationItem]):
    @model_validator(mode="after")
    def coherent_items(self):
        ids = [item.listing.listing_id for item in self.recommendations]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate recommendation ID")
        if [item.rank for item in self.recommendations] != list(range(1, len(ids) + 1)):
            raise ValueError("Ranks must be consecutive and ordered")
        score_ranks = [item.score_rank for item in self.recommendations]
        if len(score_ranks) != len(set(score_ranks)):
            raise ValueError("Duplicate score_rank")
        refs = {e.evidence_id for e in self.evidence}
        used = {ref for item in self.recommendations for ref in item.evidence_refs}
        if used - refs:
            raise ValueError("Unresolved evidence reference")
        if refs - used:
            raise ValueError("Evidence not referenced by a returned recommendation")
        return self
