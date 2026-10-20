from pydantic import Field
from recommendation.contracts.requirements import Contract


class ListingSummary(Contract):
    listing_id: str
    listing_type: str = "RENT"
    title: str | None = None
    price: float | None = Field(default=None, ge=0)
    bedrooms: float | None = Field(default=None, ge=0)
    floor_area_sqft: float | None = Field(default=None, ge=0)
    property_type: str | None = None
    district_code: str | None = None
    postal_code: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    url: str | None = None
    observed_at: str | None = None
    source: str
    normalization_warnings: list[str] = Field(default_factory=list)
