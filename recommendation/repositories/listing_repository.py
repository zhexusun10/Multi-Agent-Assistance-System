from pathlib import Path
import json, math
from ..models.listing_summary import ListingSummary
from recommendation.contracts.errors import RecommendationError

# One alias-free column list shared by the full retrieve, the by-id lookup and the data-version
# fingerprint (spec R5): every column mapped into ListingSummary, plus currency and the
# dataset_scope expression.
COLUMNS = (
    "listing_id", "listing_type", "title", "price", "bedrooms", "floor_area_sqft", "property_type",
    "district_code", "postal_code", "latitude", "longitude", "url", "updated_at", "currency",
)
DATASET_SCOPE = "raw_json ->> 'dataset_scope'"
FIELDS = ", ".join(COLUMNS) + f", {DATASET_SCOPE} AS dataset_scope"
# Fingerprint of every RENT row in ONE statement: count, newest update (epoch) and an ordered
# digest of each row's columns, so updates AND deletions change it. updated_at enters as epoch
# seconds so the text form never depends on the session time zone.
FINGERPRINT_ROW = ", ".join(
    ("extract(epoch from updated_at)" if c == "updated_at" else c) for c in COLUMNS
) + f", {DATASET_SCOPE}"
FINGERPRINT_SQL = (
    "SELECT count(*) AS row_count, extract(epoch from max(updated_at)) AS max_updated_epoch, "
    f"md5(string_agg(md5(row({FINGERPRINT_ROW})::text), '' ORDER BY listing_id)) AS rows_md5 "
    "FROM properties WHERE listing_type=:kind"
)
FINGERPRINT_FORMAT = "postgresql-rent-rows-md5-v1"


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

    # Deterministic data version of the fixture rows (spec section 5): a digest of every
    # current RENT row, so in-place updates and deletions change it like the SQL fingerprint.
    def _fingerprint(self, rows):
        from ..tracing.trace import digest

        return {
            "format": "fixture-rows-v1",
            "row_count": len(rows),
            "rows_digest": digest([r.model_dump() for r in rows]),
        }

    def fingerprint(self):
        return self._fingerprint(self.retrieve())

    def retrieve_versioned(self):
        """(rows, fingerprint) of one consistent read; fingerprint None when it failed."""
        rows = self.retrieve()
        try:
            return rows, self._fingerprint(rows)
        except Exception:
            return rows, None

    def lookup(self, ids):
        wanted = set(ids)
        return [r for r in self.retrieve() if r.listing_id in wanted]

    def lookup_versioned(self, ids):
        """(rows with these IDs, fingerprint of all rows) from one consistent read."""
        rows = self.retrieve()
        wanted = set(ids)
        try:
            fingerprint = self._fingerprint(rows)
        except Exception:
            fingerprint = None
        return [r for r in rows if r.listing_id in wanted], fingerprint

    def health(self):
        return {"contacted": True, "mode": "fixture", "count": len(self.retrieve())}

    def close(self):
        pass


class SQLListingRepository:
    mode = "database"

    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _begin(conn):
        from sqlalchemy import text

        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '10000ms'"))
        # One repeatable snapshot for every statement of this read (rows + fingerprint).
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))

    @staticmethod
    def _read_pages(conn):
        from sqlalchemy import text

        rows = []
        # Keyset pagination retains SQL NULLs.
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
        return rows

    @staticmethod
    def _read_ids(conn, ids):
        from sqlalchemy import text

        # listing_id is BIGINT: non-numeric IDs cannot exist in the table.
        keys = sorted({int(i) for i in ids if str(i).isdecimal()})
        if not keys:
            return []
        page = (
            conn.execute(
                text(
                    f"SELECT {FIELDS} FROM properties WHERE listing_type=:kind AND listing_id = ANY(CAST(:ids AS BIGINT[])) ORDER BY listing_id"
                ),
                {"kind": "RENT", "ids": keys},
            )
            .mappings()
            .all()
        )
        return [normalize(r, "postgresql:properties") for r in page]

    @staticmethod
    def _fingerprint(conn):
        from sqlalchemy import text

        row = conn.execute(text(FINGERPRINT_SQL), {"kind": "RENT"}).mappings().one()
        epoch = row["max_updated_epoch"]
        return {
            "format": FINGERPRINT_FORMAT,
            "row_count": int(row["row_count"]),
            "max_updated_epoch": None if epoch is None else str(epoch),
            "rows_md5": row["rows_md5"],
        }

    def _read(self, reader, versioned):
        try:
            with self.engine.connect() as conn:
                self._begin(conn)
                rows = reader(conn)
                fingerprint = None
                if versioned:
                    try:
                        fingerprint = self._fingerprint(conn)
                    except Exception:
                        fingerprint = None  # caller bypasses the pool cache (spec R5)
            return deduplicate(rows), fingerprint
        except RecommendationError:
            raise
        except Exception:
            raise RecommendationError(
                "DATA_UNAVAILABLE", "Listing database read/schema check failed", True
            ) from None

    def retrieve(self):
        return self._read(self._read_pages, False)[0]

    def retrieve_versioned(self):
        """All RENT rows and their fingerprint from the same REPEATABLE READ transaction."""
        return self._read(self._read_pages, True)

    def lookup(self, ids):
        """Rows for ``ids`` read BY ID (never a full retrieve)."""
        return self._read(lambda conn: self._read_ids(conn, ids), False)[0]

    def lookup_versioned(self, ids):
        """Rows for ``ids`` and the fingerprint of all RENT rows, in one REPEATABLE READ transaction."""
        return self._read(lambda conn: self._read_ids(conn, ids), True)

    def fingerprint(self):
        try:
            with self.engine.connect() as conn:
                self._begin(conn)
                return self._fingerprint(conn)
        except Exception:
            raise RecommendationError(
                "DATA_UNAVAILABLE", "Listing fingerprint query failed", True
            ) from None

    def health(self):
        return {
            "contacted": True,
            "source": "postgresql:properties",
            "rental_count": len(self.retrieve()),
        }

    def close(self):
        self.engine.dispose()
