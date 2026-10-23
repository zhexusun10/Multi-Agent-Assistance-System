"""Feedback snapshot sent in full by the orchestrator with every request (contract 2.0).

The native side never accumulates feedback across requests: browsing comes only from the
previous round, appointments are the orchestrator's complete history (deduplicated per
listing). Service-level checks (previous round is a committed run of this conversation whose
recommendation ID set equals shown_listing_ids; appointment statuses inside
ranking.behavior.counted_appointment_statuses) live in recommendation/context/manager.py.
"""

from typing import Annotated, Literal
from pydantic import Field, model_validator
from .requirements import Contract
from .limits import MAX_RESULT_LIMIT

Identifier = Annotated[str, Field(min_length=1, max_length=128)]


def _duplicates(values):
    seen, repeated = set(), []
    for value in values:
        if value in seen and value not in repeated:
            repeated.append(value)
        seen.add(value)
    return repeated


class BrowsingRecord(Contract):
    listing_id: Identifier
    status: Literal["recorded", "missing"]
    duration_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def coherent(self):
        if self.status == "recorded" and self.duration_seconds is None:
            raise ValueError(
                "recorded browsing requires a finite duration_seconds >= 0 (0 = shown but not browsed)"
            )
        if self.status == "missing" and self.duration_seconds is not None:
            raise ValueError("missing browsing must not carry duration_seconds")
        return self


class PreviousRound(Contract):
    request_id: Identifier
    shown_listing_ids: list[Identifier] = Field(min_length=1, max_length=MAX_RESULT_LIMIT)
    browsing: list[BrowsingRecord]

    @model_validator(mode="after")
    def one_record_per_shown_listing(self):
        if _duplicates(self.shown_listing_ids):
            raise ValueError(f"duplicate shown_listing_ids: {_duplicates(self.shown_listing_ids)}")
        ids = [record.listing_id for record in self.browsing]
        if _duplicates(ids):
            raise ValueError(f"duplicate browsing records for listings: {_duplicates(ids)}")
        if set(ids) != set(self.shown_listing_ids):
            raise ValueError(
                "browsing must contain exactly one record per shown listing "
                f"(missing: {sorted(set(self.shown_listing_ids) - set(ids))}, "
                f"not shown: {sorted(set(ids) - set(self.shown_listing_ids))})"
            )
        return self


class AppointmentSignal(Contract):
    """One entry per listing: every non-draft VIEWING action status and action ID for it."""

    listing_id: Identifier
    statuses: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(min_length=1)
    action_ids: list[Identifier] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_entries(self):
        if _duplicates(self.statuses):
            raise ValueError(f"duplicate appointment statuses: {_duplicates(self.statuses)}")
        if _duplicates(self.action_ids):
            raise ValueError(f"duplicate appointment action_ids: {_duplicates(self.action_ids)}")
        return self


class FeedbackSnapshot(Contract):
    previous_round: PreviousRound | None = None
    appointments: list[AppointmentSignal] = Field(default_factory=list)

    @model_validator(mode="after")
    def one_appointment_entry_per_listing(self):
        repeated = _duplicates([a.listing_id for a in self.appointments])
        if repeated:
            raise ValueError(
                f"duplicate appointment listings {repeated}: send one entry per listing with all statuses and action_ids"
            )
        return self
