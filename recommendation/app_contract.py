"""Application behaviour handoff (main contract). behavior_context is part of the native request
since the 2026-10-23 merge, so the app request IS the native request; the models live in
recommendation/contracts/behavior.py and are re-exported here for existing importers."""
from .contracts.behavior import BehaviorContext, BookViewingListing, ListingViewDuration
from .contracts.request import RecommendationRequest


class ApplicationRecommendationRequest(RecommendationRequest):
    def native_request(self):
        # behavior_context and cold_start are scored natively; nothing is stripped.
        return RecommendationRequest.model_validate(self.model_dump())


__all__ = ["ApplicationRecommendationRequest", "BehaviorContext", "BookViewingListing", "ListingViewDuration"]
