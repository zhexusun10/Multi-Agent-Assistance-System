"""Typed tool arguments (spec section 6, P4). Every model is a Contract (extra=forbid,
allow_inf_nan=False) and carries ``context_ref``: a call is scoped to the CURRENT run, and an
argument object for another run, an unknown key or a wrong type is rejected before any tool
runs. The JSON schema of each model is what the live controller sends to the model.

Bounds below are validation ceilings of model-supplied arguments (like the schema ceilings of
the settings loader), not tunable algorithm parameters. Run-dependent bounds are applied by the
dispatcher from the settings: ``listing_ids`` at most ``controller.model_result_candidates``
(the number of listing IDs a tool result ever shows the model), search ``limit`` at most
``entity_resolution.max_candidates``.
"""

from typing import Literal

from pydantic import Field, field_validator

from ..geography.catalog import OSM_CATEGORIES
from ..models.tool_results import ToolArguments

ENTITY_KINDS = ("lta_station", "ura_area", "ura_region", "osm_place")
MAX_QUERY_CHARS = 200
MAX_ID_CHARS = 200
MAX_RADIUS_M = 50000.0  # the east-west extent of Singapore; a validation ceiling, not a "near" rule
MAX_AMENITIES_PER_LISTING = 20

EntityKind = Literal[ENTITY_KINDS]
PlaceCategory = Literal[OSM_CATEGORIES]


def _unique(values):
    if len(values) != len(set(values)):
        raise ValueError("values must be unique")
    return values


class ListingArguments(ToolArguments):
    listing_ids: list[str] = Field(min_length=1, description="Listing IDs returned by build_pool/rank_candidates in this run")

    @field_validator("listing_ids")
    @classmethod
    def unique_ids(cls, values):
        if any(not v or len(v) > MAX_ID_CHARS for v in values):
            raise ValueError("listing IDs must be non-empty strings")
        return _unique(values)


class SearchPlacesArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS,
                       description="Place name to look up (a hypothesis; exact normalised name/alias match)")
    kind: EntityKind | None = Field(default=None, description="Keep only entities of this kind")
    category: PlaceCategory | None = Field(default=None, description="Keep only OSM places of this category")
    limit: int | None = Field(default=None, ge=1, description="Candidates listed (<= entity_resolution.max_candidates)")
    criterion_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_ID_CHARS,
        description="Geographic criterion this search is for; required for a later bind_entity and counted "
        "against entity_resolution.max_search_attempts of that criterion")


class DescribeFieldsArguments(ToolArguments):
    pass


class NearestMrtArguments(ListingArguments):
    mode: Literal["MRT", "LRT"] | None = Field(default=None, description="Station mode (default MRT; LRT excluded)")


class DistanceToArguments(ListingArguments):
    destination_id: str = Field(min_length=1, max_length=MAX_ID_CHARS,
                                description="Entity ID returned by a tool in this run")


class NearbyAmenitiesArguments(ListingArguments):
    categories: list[PlaceCategory] = Field(min_length=1)
    radius_m: float = Field(gt=0, le=MAX_RADIUS_M)
    limit: int = Field(ge=1, le=MAX_AMENITIES_PER_LISTING, description="Amenities listed per listing")

    @field_validator("categories")
    @classmethod
    def unique_categories(cls, values):
        return _unique(values)


class DistrictMembershipArguments(ListingArguments):
    pass


class BindEntityArguments(ToolArguments):
    criterion_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    entity_id: str = Field(min_length=1, max_length=MAX_ID_CHARS,
                           description="The single candidate of a search_places result for this criterion")
