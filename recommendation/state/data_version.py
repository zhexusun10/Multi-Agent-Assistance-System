"""Data and catalog versions for the pool cache (spec section 5, R5).

data_version = digest(listing fingerprint, geographic catalog version). The listing fingerprint
comes from the listing repository: for PostgreSQL one statement over every RENT row (count,
newest updated_at as epoch seconds, ordered md5 of each row's shared column list), run inside
the same REPEATABLE READ transaction as the full retrieve (miss path) or the by-id lookup
(final gate); for the fixture repository a digest of the current rows. Both detect updates and
deletions.

Geographic catalog version: ``catalog_version()`` of the geographic repository, i.e. the native
catalog (recommendation/geography/catalog.py): versions and sha256 of knowledge/locations
(LTA stations, URA areas, both alias files) and of the hash-checked datasets, plus the place
source version (Neo4j ``Dataset`` dataset_id + snapshot_sha256, re-queried on every call, or the
digest of the fixture place list). A repository without a version source has no catalog
version, and the pool cache is then bypassed (never served unverified).

Documented limits: Neo4j Listing coordinates are not hashed (SQL coordinates are canonical and
geographic facts never read graph edges since P4); OSM place changes are covered only through
Dataset.snapshot_sha256; the SQL fingerprint still scans every RENT row server-side, so a cache
hit saves row transfer, normalisation and hard-geo evaluation, not that scan.
"""

from ..tracing.trace import digest

DATA_VERSION_FORMAT = "data-version-v1"
UNAVAILABLE = "unavailable"  # PoolSummary.data_version when the fingerprint could not be read


class VersionUnavailable(Exception):
    """A data or catalog version could not be determined; the pool cache must be bypassed."""


def catalog_version(geographic):
    """Version of the geographic catalog the hard-geo facts were computed from."""
    custom = getattr(geographic, "catalog_version", None)
    if not callable(custom):
        raise VersionUnavailable(f"{type(geographic).__name__} has no catalog version source")
    try:
        return custom()
    except VersionUnavailable:
        raise
    except Exception:
        raise VersionUnavailable("Geographic catalog version could not be read") from None


def data_version(fingerprint, geo_version):
    """digest(sql/fixture fingerprint, geo catalog version); None when either is missing."""
    if not fingerprint or not geo_version:
        return None
    return digest({"format": DATA_VERSION_FORMAT, "listings": fingerprint, "geography": geo_version})
