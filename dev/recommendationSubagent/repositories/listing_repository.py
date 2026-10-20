from pathlib import Path
import json, math
from ..models.listing_summary import ListingSummary
from dev.sharedContract.recommendationSubagentContract.errors import RecommendationError

FIELDS = "listing_id, listing_type, title, price, bedrooms, floor_area_sqft, property_type, district_code, postal_code, latitude, longitude, url, updated_at, currency, raw_json ->> 'dataset_scope' AS dataset_scope"


def normalize(row, source):
    row = dict(row)
    if row.get("dataset_scope") == "recommendation_csv_sample":
        source += ":partial_csv_sample"
    data = {
        k: row.get(k)
        for k in ListingSummary.model_fields
        if k not in ("source", "observed_at", "normalization_warnings")
    }
    for k, v in data.items():
        if v == "":
            data[k] = None
    warnings = []
    if row.get("listing_id") in (None, ""):
        raise RecommendationError("INVALID_SOURCE", "Listing has no identity")
    data["listing_id"] = str(row["listing_id"])
    for key in ("price", "bedrooms", "floor_area_sqft"):
        original = data[key]
        if original is None:
            continue
        try:
            value = float(original)
            if not math.isfinite(value) or value < 0:
                raise ValueError()
            data[key] = value
        except (ValueError, TypeError):
            data[key] = None
            warnings.append(f"{key}: invalid source numeric value treated as unknown")
    for k in ("postal_code", "district_code"):
        if data[k] is not None:
            data[k] = str(data[k])
    if row.get("currency") not in (None, "", "SGD"):
        data["price"] = None
    for key, limit in [("latitude", 90), ("longitude", 180)]:
        try:
            value = float(data[key])
            data[key] = value if math.isfinite(value) and abs(value) <= limit else None
        except (TypeError, ValueError):
            data[key] = None
    if data["property_type"]:
        data["property_type"] = data["property_type"].strip().casefold()
    data["observed_at"] = (
        str(row.get("updated_at") or row.get("observed_at") or "") or None
    )
    return ListingSummary(**data, source=source, normalization_warnings=warnings)


def deduplicate(rows):
    found = {}
    for row in rows:
        if row.listing_id in found and row != found[row.listing_id]:
            raise RecommendationError(
                "CONFLICTING_SOURCE", "Conflicting duplicate listing ID"
            )
        found[row.listing_id] = row
    return sorted(found.values(), key=lambda r: r.listing_id)


class FixtureListingRepository:
    mode = "fixture"

    def __init__(self, path=None):
        self.rows = [
            normalize(r, "synthetic-fixture")
            for r in json.loads(
                (
                    Path(path)
                    if path
                    else Path(__file__).parents[1] / "examples/listings.json"
                ).read_text()
            )
        ]

    def retrieve(self):
        return deduplicate(
            [r.model_copy(deep=True) for r in self.rows if r.listing_type == "RENT"]
        )

    def lookup(self, ids):
        return [r for r in self.retrieve() if r.listing_id in ids]

    def health(self):
        return {"contacted": True, "mode": "fixture", "count": len(self.retrieve())}

    def close(self):
        pass


class SQLListingRepository:
    mode = "database"

    def __init__(self, engine):
        self.engine = engine

    def retrieve(self):
        from sqlalchemy import text

        rows = []
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text("SET LOCAL statement_timeout = '10000ms'"))
                # One repeatable snapshot; keyset pagination retains SQL NULLs.
                conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                after = -1
                while True:
                    page = (
                        conn.execute(
                            text(
                                f"SELECT {FIELDS} FROM properties WHERE listing_type=:kind AND listing_id>:after ORDER BY listing_id LIMIT :size"
                            ),
                            {"kind": "RENT", "after": after, "size": 500},
                        )
                        .mappings()
                        .all()
                    )
                    if not page:
                        break
                    rows.extend(normalize(r, "postgresql:properties") for r in page)
                    after = page[-1]["listing_id"]
            return deduplicate(rows)
        except RecommendationError:
            raise
        except Exception:
            raise RecommendationError(
                "DATA_UNAVAILABLE", "Listing database read/schema check failed", True
            ) from None

    def lookup(self, ids):
        return [r for r in self.retrieve() if r.listing_id in ids]

    def health(self):
        return {
            "contacted": True,
            "source": "postgresql:properties",
            "rental_count": len(self.retrieve()),
        }

    def close(self):
        self.engine.dispose()
