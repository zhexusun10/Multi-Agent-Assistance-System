"""Structured failure reports for the code-controlled repair loop (service.py).

Final validation (validation/gate.py) and code-side selection raise GateFailure; the service turns
each into a FailureReport, decides the recovery in code (invalidate stages, rebuild the pool, stop)
and, only when the failure is repairable by retrieval or recomputation, returns the report to the
agent, which keeps its tool history and uses the same constrained tools. The agent can never relax
criteria or override validation: the next completion goes through selection and final validation
again.
"""

from typing import Literal

from pydantic import Field

from recommendation.contracts.errors import RecommendationError
from recommendation.contracts.requirements import Contract

Action = Literal["targeted_retrieval", "refresh_and_recompute", "refilter_and_rerank", "none"]
Resolvable = Literal["retrieval", "recomputation", "none"]


class GateFailure(RecommendationError):
    """A final-validation / selection failure with its repair metadata."""

    def __init__(self, code, message, retryable=False, *, stage="final_gate", action="none",
                 resolvable_by="none", listing_ids=(), criterion_ids=(), invalidate=()):
        super().__init__(code, message, retryable)
        self.stage = stage
        self.action = action
        self.resolvable_by = resolvable_by
        self.listing_ids = list(listing_ids)
        self.criterion_ids = list(criterion_ids)
        self.invalidate = list(invalidate)


class FailureReport(Contract):
    code: str
    stage: str
    message: str
    retryable: bool
    listing_ids: list[str] = Field(default_factory=list)
    criterion_ids: list[str] = Field(default_factory=list)
    references: dict[str, str | None] = Field(default_factory=dict)
    action: Action = "none"
    resolvable_by: Resolvable = "none"
    invalidated: list[str] = Field(default_factory=list)
    attempt: int = 1

    @property
    def repairable(self):
        return self.retryable and self.resolvable_by != "none"


def report_of(error, references, attempt):
    detail = error.detail
    if isinstance(error, GateFailure):
        return FailureReport(code=detail.code, stage=error.stage, message=detail.message, retryable=detail.retryable,
                             listing_ids=error.listing_ids, criterion_ids=error.criterion_ids, references=references,
                             action=error.action, resolvable_by=error.resolvable_by, invalidated=error.invalidate,
                             attempt=attempt)
    return FailureReport(code=detail.code, stage="final_gate", message=detail.message, retryable=False,
                         references=references, attempt=attempt)
