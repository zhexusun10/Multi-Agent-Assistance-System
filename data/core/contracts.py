"""Versioned HTTP API contracts and filter-bound keyset cursors."""
import base64
import hashlib
import json
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

PlaceCategory = Literal["School", "FoodCourt", "Mall", "Market", "Hospital", "Clinic", "Park", "Supermarket", "Library", "CampusBuilding"]
DistrictCode = str
T = TypeVar("T")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ListingFilters(Contract):
    q: str = Field(default="", max_length=200)
    district: DistrictCode | None = Field(default=None, pattern=r"^D(0[1-9]|1[0-9]|2[0-8])$")
    listing_type: Literal["SALE", "RENT"] | None = None
    property_type: str | None = Field(default=None, max_length=100)
    min_price: float | None = Field(default=None, ge=0, le=1e15)
    max_price: float | None = Field(default=None, ge=0, le=1e15)
    min_bedrooms: int | None = Field(default=None, ge=0, le=100)
    max_bedrooms: int | None = Field(default=None, ge=0, le=100)
    mrt_station_id: str | None = Field(default=None, max_length=150)
    max_mrt_distance_m: float | None = Field(default=None, ge=0, le=50000)
    include_planned: bool = True
    coordinates_only: bool = False
    place_category: PlaceCategory | None = None
    place_id: str | None = Field(default=None, max_length=150)
    max_place_distance_m: float | None = Field(default=None, ge=0, le=10000)

    @model_validator(mode="after")
    def ordered_ranges(self):
        for lower, upper in ((self.min_price, self.max_price), (self.min_bedrooms, self.max_bedrooms)):
            if lower is not None and upper is not None and lower > upper:
                raise ValueError("minimum must not exceed maximum")
        self.q = self.q.strip()
        return self


class ListingSearch(Contract):
    filters: ListingFilters = Field(default_factory=ListingFilters)
    cursor: str | None = Field(default=None, max_length=2000)
    page_size: int = Field(default=50, ge=1, le=1000)


class GraphExpand(Contract):
    entity_id: str = Field(min_length=1, max_length=200)
    filters: ListingFilters = Field(default_factory=ListingFilters)
    cursor: str | None = Field(default=None, max_length=2000)
    page_size: int = Field(default=500, ge=1, le=1000)


class PlaceFilters(Contract):
    q: str = Field(default="", max_length=200)
    category: PlaceCategory | None = None
    district: DistrictCode | None = Field(default=None, pattern=r"^D(0[1-9]|1[0-9]|2[0-8])$")


class PlaceSearch(Contract):
    filters: PlaceFilters = Field(default_factory=PlaceFilters)
    cursor: str | None = Field(default=None, max_length=2000)
    page_size: int = Field(default=50, ge=1, le=1000)


class NearbyPlaces(Contract):
    listing_id: int | None = Field(default=None, ge=1, le=9223372036854775807)
    latitude: float | None = Field(default=None, ge=1.15, le=1.50)
    longitude: float | None = Field(default=None, ge=103.55, le=104.20)
    radius_m: float = Field(default=1500, ge=1, le=10000)
    categories: list[PlaceCategory] = Field(default_factory=list, max_length=10)
    cursor: str | None = Field(default=None, max_length=2000)
    page_size: int = Field(default=50, ge=1, le=1000)

    @model_validator(mode="after")
    def one_center(self):
        coords = self.latitude is not None and self.longitude is not None
        partial = (self.latitude is None) != (self.longitude is None)
        if partial or (self.listing_id is not None) == coords:
            raise ValueError("Choose listing_id OR both latitude and longitude")
        self.categories = sorted(set(self.categories))
        return self


class Source(Contract):
    store: Literal["neo4j", "postgresql", "sqlite"]
    role: str
    distance_type: str | None = None
    route_verified: bool = False


class Page(Contract, Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False
    page_size: int
    source: Source
    warnings: list[str] = Field(default_factory=list)


class Entity(Contract):
    entity_id: str
    label: str
    properties: dict[str, Any]


class Item(Contract, Generic[T]):
    item: T
    source: Source
    warnings: list[str] = Field(default_factory=list)


class RelationshipEvidence(Contract):
    type: str
    target: Entity
    evidence: dict[str, Any]


class ListingContext(Contract):
    listing: Entity
    relationships: list[RelationshipEvidence]
    source: Source
    warnings: list[str] = Field(default_factory=list)


class GraphNode(Contract):
    id: str
    entity_id: str
    label: str
    caption: str
    properties: dict[str, Any]


class GraphEdge(Contract):
    id: str
    source: str
    target: str
    type: str
    properties: dict[str, Any]


class GraphPage(Contract):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    listing_count: int = 0
    page_size: int = 500
    has_more: bool = False
    next_cursor: str | None = None


def fingerprint(resource, filters):
    return hashlib.sha256(json.dumps([resource, filters], sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def encode_cursor(resource, filters, key):
    payload = {"v": 1, "f": fingerprint(resource, filters), "key": key}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(value, resource, filters, default=None):
    if value is None:
        return default
    try:
        payload = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if payload["v"] != 1 or payload["f"] != fingerprint(resource, filters):
            raise ValueError("Cursor does not match these filters")
        return payload["key"]
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid cursor, or filters changed; start with cursor=null") from exc
