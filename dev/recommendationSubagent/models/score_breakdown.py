from pydantic import Field
from dev.sharedContract.recommendationSubagentContract.requirements import Contract


class Contribution(Contract):
    feature: str
    reference: str | None = None
    raw_difference: float | None = None
    scale: float | None = None
    similarity: float | None = Field(default=None, ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    contribution: float = Field(ge=0, le=1)
    unknown: bool = False


class ScoreBreakdown(Contract):
    explicit_score: float | None = Field(default=None, ge=0, le=1)
    implicit_score: float | None = Field(default=None, ge=0, le=1)
    combined_score: float = Field(ge=0, le=1)
    explicit_weight: float = Field(ge=0, le=1)
    implicit_weight: float = Field(ge=0, le=1)
    evidence_coverage: float = Field(ge=0, le=1)
    contributions: list[Contribution]
    suppressed_features: list[str] = Field(default_factory=list)
    ordering: str
