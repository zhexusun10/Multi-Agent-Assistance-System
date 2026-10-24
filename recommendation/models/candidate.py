from pydantic import Field, model_validator
from recommendation.contracts.requirements import Contract
from recommendation.contracts.evidence import CriterionResult
from recommendation.contracts.response import (
    RecommendationResponse,
)
from .listing_summary import ListingSummary
from .score_breakdown import ScoreBreakdown


class RecommendationItem(Contract):
    listing: ListingSummary
    rank: int = Field(ge=1, le=5)
    scores: ScoreBreakdown
    criterion_results: list[CriterionResult]
    evidence_refs: list[str]
    strengths: list[str]
    trade_offs: list[str]
    unknowns: list[str]


class Response(RecommendationResponse[RecommendationItem]):
    @model_validator(mode="after")
    def coherent_items(self):
        ids = [item.listing.listing_id for item in self.recommendations]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate recommendation ID")
        if [item.rank for item in self.recommendations] != list(range(1, len(ids) + 1)):
            raise ValueError("Ranks must be consecutive and ordered")
        refs = {e.evidence_id for e in self.evidence}
        if any(
            ref not in refs
            for item in self.recommendations
            for ref in item.evidence_refs
        ):
            raise ValueError("Unresolved evidence reference")
        return self
