from typing import Literal
from pydantic import Field
from .requirements import Contract


class Evidence(Contract):
    evidence_id: str
    listing_id: str
    criterion_id: str
    source: str
    observed_at: str
    value: float | str | None = None
    unit: str | None = None
    distance_type: str | None = None
    method: str
    coverage_m: float | None = None
    destination_id: str | None = None
    unknown_reason: str | None = None
    mapping_status: Literal["known", "missing", "not_applicable"] = "not_applicable"
    snapshot_hash: str


class CriterionResult(Contract):
    criterion_id: str
    outcome: Literal["pass", "fail", "unknown", "unsupported"]
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
