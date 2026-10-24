from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
import math


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


NUMERIC = {
    "price": "SGD/month",
    "bedrooms": None,
    "floor_area_sqft": "sqft",
    "distance": "m",
    "commute": "minutes",
    "lease_months": "months",
}
CATEGORICAL = {"district_code", "property_type", "furnishing", "availability"}


class Criterion(Contract):
    criterion_id: str = Field(min_length=1)
    field: Literal[
        "price",
        "bedrooms",
        "floor_area_sqft",
        "district_code",
        "property_type",
        "distance",
        "commute",
        "furnishing",
        "availability",
        "lease_months",
    ]
    operator: Literal["eq", "in", "gte", "lte", "between"]
    value: float | str | list[float] | list[str]
    unit: str | None = None
    strength: Literal["hard", "soft"]
    weight: float = Field(default=1, gt=0)
    utility: Literal["threshold", "proximity", "minimize", "maximize"] = "threshold"
    destination_id: str | None = None
    destination_name: str | None = None
    category: str | None = None
    distance_type: Literal["straight_line", "walking", "transit"] | None = None
    transport_mode: Literal["walk", "transit", "drive"] | None = None

    @field_validator("value", mode="before")
    @classmethod
    def reject_booleans(cls, value):
        values = value if isinstance(value, list) else [value]
        if any(isinstance(v, bool) for v in values):
            raise ValueError("Boolean is not a criterion value")
        return value

    @model_validator(mode="after")
    def valid(self):
        if self.field in NUMERIC:
            if self.unit != NUMERIC[self.field]:
                raise ValueError(f"{self.field} requires unit {NUMERIC[self.field]}")
            values = self.value if isinstance(self.value, list) else [self.value]
            if not all(
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(v)
                and v >= 0
                for v in values
            ):
                raise ValueError("numeric values must be finite and nonnegative")
            if self.operator == "in" or (
                self.operator == "between"
                and (len(values) != 2 or values[0] > values[1])
            ):
                raise ValueError("invalid numeric operator/range")
            if self.operator != "between" and isinstance(self.value, list):
                raise ValueError("scalar required")
        else:
            if self.operator not in ("eq", "in") or self.unit is not None:
                raise ValueError("categorical fields support eq/in without units")
            if self.operator == "eq" and not isinstance(self.value, str):
                raise ValueError("string required")
            if self.operator == "in" and (
                not isinstance(self.value, list)
                or not self.value
                or not all(isinstance(v, str) for v in self.value)
            ):
                raise ValueError("nonempty string list required")
        if self.field in ("distance", "commute"):
            # A category stands alone; a destination ID and a name may come together (the
            # name is resolved too: invalid ID -> name search, ID/name conflict -> ambiguous).
            if (
                not (self.destination_id or self.destination_name or self.category)
                or self.category
                and (self.destination_id or self.destination_name)
            ):
                raise ValueError(
                    "a category alone, or a destination ID and/or name, is required"
                )
            if self.distance_type is None:
                raise ValueError("distance semantics required")
            if self.field == "commute" and not self.transport_mode:
                raise ValueError("commute transport mode required")
        if self.utility == "proximity" and (
            self.field != "distance"
            or self.operator != "lte"
            or not isinstance(self.value, (int, float))
            or self.value <= 0
        ):
            raise ValueError("proximity requires positive distance/lte radius")
        if self.utility in ("minimize", "maximize") and self.field not in (
            "price",
            "bedrooms",
            "floor_area_sqft",
        ):
            raise ValueError("unsupported utility field")
        if self.strength == "hard" and self.utility != "threshold":
            raise ValueError("hard rules use exact thresholds")
        if self.field == "property_type":
            self.value = (
                [v.strip().casefold() for v in self.value]
                if isinstance(self.value, list)
                else self.value.strip().casefold()
            )
        return self


class Requirements(Contract):
    listing_type: Literal["RENT"] = "RENT"
    criteria: list[Criterion]

    @model_validator(mode="after")
    def consistent(self):
        ids = [c.criterion_id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate criterion_id")
        groups = {}
        for c in self.criteria:
            if c.strength == "hard":
                groups.setdefault(
                    (
                        c.field,
                        c.destination_id,
                        c.destination_name,
                        c.category,
                        c.distance_type,
                        c.transport_mode,
                    ),
                    [],
                ).append(c)
        for cs in groups.values():
            if cs[0].field in NUMERIC:
                lo, hi = 0, float("inf")
                for c in cs:
                    if c.operator in ("gte", "eq"):
                        lo = max(lo, c.value)
                    if c.operator in ("lte", "eq"):
                        hi = min(hi, c.value)
                    if c.operator == "between":
                        lo = max(lo, c.value[0])
                        hi = min(hi, c.value[1])
                if lo > hi:
                    raise ValueError("contradictory hard bounds")
            else:
                allowed = None
                for c in cs:
                    vals = set(c.value if c.operator == "in" else [c.value])
                    allowed = vals if allowed is None else allowed & vals
                if not allowed:
                    raise ValueError("contradictory hard categories")
        return self
