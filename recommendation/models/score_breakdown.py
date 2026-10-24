from typing import Literal
from pydantic import Field
from recommendation.contracts.requirements import Contract


class Contribution(Contract):
    feature: str
    branch: Literal["soft", "appointment", "browsing"]
    reference: str | None = None
    raw_difference: float | None = None
    scale: float | None = None
    similarity: float | None = Field(default=None, ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    contribution: float = Field(ge=0, le=1)
    unknown: bool = False
    method: str | None = None


class ScoreBreakdown(Contract):
    soft_score: float | None = Field(default=None, ge=0, le=1)
    behavior_score: float | None = Field(default=None, ge=0, le=1)
    appointment_score: float | None = Field(default=None, ge=0, le=1)
    browsing_score: float | None = Field(default=None, ge=0, le=1)
    combined_score: float = Field(ge=0, le=1)
    soft_weight: float = Field(ge=0, le=1)
    behavior_weight: float = Field(ge=0, le=1)
    appointment_weight: float = Field(ge=0, le=1)
    browsing_weight: float = Field(ge=0, le=1)
    soft_evidence_coverage: float = Field(ge=0, le=1)
    comparison_coverage: float | None = Field(default=None, ge=0, le=1)
    contributions: list[Contribution]
    suppressed_features: list[str] = Field(default_factory=list)
    ordering: str
