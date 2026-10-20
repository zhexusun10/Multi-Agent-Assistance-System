"""Validated application-owned policy; model tool arguments cannot change it."""

from pathlib import Path
import math
from typing import Literal
from pydantic import Field, model_validator
from recommendation.contracts.requirements import Contract
from .tracing.trace import digest

DEFAULT_SCALES = {"bedrooms": 3.0, "floor_area_sqft": 500.0, "geography": 3000.0}
DEFAULT_WEIGHTS = {
    "property_type": 0.25,
    "bedrooms": 0.20,
    "floor_area_sqft": 0.25,
    "geography": 0.30,
}
DEFAULT_BOUNDS = {
    "price": (0.0, 20000.0),
    "bedrooms": (0.0, 10.0),
    "floor_area_sqft": (0.0, 10000.0),
}


class ScoringPolicy(Contract):
    version: Literal["weighted-v1"] = "weighted-v1"
    scales: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_SCALES))
    feature_weights: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_WEIGHTS)
    )
    numeric_bounds: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: dict(DEFAULT_BOUNDS)
    )

    @model_validator(mode="after")
    def supported_policy(self):
        if set(self.scales) != set(DEFAULT_SCALES) or any(
            v <= 0 for v in self.scales.values()
        ):
            raise ValueError("Supply all supported positive feature scales")
        if (
            set(self.feature_weights) != set(DEFAULT_WEIGHTS)
            or any(v < 0 for v in self.feature_weights.values())
            or not math.isfinite(sum(self.feature_weights.values()))
            or sum(self.feature_weights.values()) <= 0
        ):
            raise ValueError(
                "Supply all supported nonnegative feature weights with positive total"
            )
        if set(self.numeric_bounds) != set(DEFAULT_BOUNDS) or any(
            lo < 0 or hi <= lo for lo, hi in self.numeric_bounds.values()
        ):
            raise ValueError(
                "Supply all supported increasing nonnegative numeric bounds"
            )
        return self

    @property
    def identifier(self):
        if self.model_dump() == ScoringPolicy().model_dump():
            return self.version
        return f"{self.version}:{digest(self.model_dump())[:12]}"


class RunLimits(Contract):
    model_turns: int = Field(default=8, ge=1, le=8, strict=True)
    tool_calls: int = Field(default=20, ge=1, le=20, strict=True)
    timeout_seconds: float = Field(default=90, gt=0, le=600)
    model_result_candidates: int = Field(default=100, ge=4, le=500, strict=True)
    repairs: int = Field(default=2, ge=0, le=2, strict=True)


class RecommendationConfig(Contract):
    scoring: ScoringPolicy = Field(default_factory=ScoringPolicy)
    limits: RunLimits = Field(default_factory=RunLimits)

    @classmethod
    def from_file(cls, path=None):
        return cls.model_validate_json(Path(path).read_text()) if path else cls()
