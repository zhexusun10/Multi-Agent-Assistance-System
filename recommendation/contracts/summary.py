"""Response 2.0 summaries: entity resolution, exploration pool and selection (spec section 3).

Phase P2 populates these with transitional values (no pool cache, no cold start, ranked
prefix selection); later phases fill the remaining diagnostics without changing the shapes.
"""

from typing import Annotated, Literal
from pydantic import Field, model_validator
from .requirements import Contract
from .limits import MAX_RESULT_LIMIT

Unit = Annotated[float, Field(ge=0, le=1)]
Count = Annotated[int, Field(ge=0)]
UNKNOWN_REASON_KEYS = 10  # distinct reasons kept; the rest are summed under "other"


class ResolutionOriginal(Contract):
    destination_id: str | None = None
    destination_name: str | None = None
    category: str | None = None


class ResolutionCandidate(Contract):
    entity_id: str
    name: str | None = None
    kind: str


class ResolutionResult(Contract):
    criterion_id: str
    field: str
    original: ResolutionOriginal
    status: Literal["resolved", "ambiguous", "unresolved", "not_applicable"]
    entity_id: str | None = None
    entity_kind: Literal["lta_station", "ura_area", "ura_region", "osm_place", "category"] | None = None
    method: str
    reason: str | None = None
    candidates: list[ResolutionCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent(self):
        if (self.status == "resolved") != (self.entity_id is not None):
            raise ValueError("entity_id is required exactly when status is resolved")
        return self


class PoolSummary(Contract):
    pool_key: str
    pool_version: str
    data_version: str
    cache: Literal["built", "reused", "bypassed"]
    size: Count
    source_count: Count
    excluded_hard_fail: Count
    excluded_hard_unknown: Count
    unknown_reasons: dict[str, Count] = Field(default_factory=dict, max_length=UNKNOWN_REASON_KEYS + 1)

    @model_validator(mode="after")
    def partition(self):
        if self.source_count != self.size + self.excluded_hard_fail + self.excluded_hard_unknown:
            raise ValueError("source_count must equal size + excluded_hard_fail + excluded_hard_unknown")
        if self.unknown_reasons and not self.excluded_hard_unknown:
            raise ValueError("unknown_reasons require unknown exclusions")
        return self


class TieGroup(Contract):
    size: Annotated[int, Field(ge=1)]
    min_score: Unit
    max_score: Unit
    span: Annotated[float, Field(ge=0)]
    epsilon: Annotated[float, Field(ge=0)]


class ExplorationBin(Contract):
    lower: float | None
    upper: float | None
    count: Count
    sampled: Count
    quota: Count


class Exploration(Contract):
    candidate_count: Count
    sample_size: Count
    bins: list[ExplorationBin]


class ColdStartThresholds(Contract):
    c: Unit
    b: Annotated[int, Field(ge=1)]
    s: Unit


class ColdStartDiagnostics(Contract):
    evaluated: bool
    active: bool
    reason: str
    coverage_c: Unit | None = None
    boundary_group_b: Annotated[int, Field(ge=1)] | None = None
    stability_s: Unit | None = None
    thresholds: ColdStartThresholds
    exit_conditions_met: bool
    pool_key: str | None = None

    @model_validator(mode="after")
    def exit_requires_diagnostics(self):
        # R9: never claim an exit without evaluated diagnostics and a computed S.
        if self.exit_conditions_met and (not self.evaluated or self.stability_s is None):
            raise ValueError("exit_conditions_met requires evaluated diagnostics with stability_s")
        if self.active and not self.evaluated:
            raise ValueError("cold start cannot be active without evaluated diagnostics")
        return self


class SelectionSummary(Contract):
    mode: Literal["ranked", "near_tie_sampling", "cold_start_exploration", "small_pool", "empty"]
    algorithm_version: str
    seed: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    result_limit: Annotated[int, Field(ge=1, le=MAX_RESULT_LIMIT)]
    leaders: Count
    tie_group: TieGroup | None = None
    exploration: Exploration | None = None
    cold_start: ColdStartDiagnostics
