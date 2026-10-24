"""Geographic repositories: native catalog + resolver + straight-line distances (spec section 6).

Both repositories share the static LTA/URA catalog (recommendation/geography/catalog.py) and
differ only in their place source: synthetic fixture places, or active OSM ``Place`` nodes read
from Neo4j (cached per process by ``Dataset.snapshot_sha256``). Every distance is computed in
Python from the canonical SQL listing coordinates (recommendation/geography/distance.py); Neo4j
NEAR_PLACE edges and point.distance are not used.
"""

from ..tracing.trace import digest, now
from ..geography import catalog as geo_catalog
from ..geography.evidence import measure
from ..geography.resolver import resolve_criteria
from ..state.data_version import VersionUnavailable
from recommendation.contracts.evidence import Evidence
from recommendation.contracts.errors import RecommendationError


def _settings(settings):
    if settings is not None:
        return settings
    from ..config import RecommendationConfig

    return RecommendationConfig.default()


class GeographicBase:
    source = "geographic"

    def catalog(self):
        try:
            return geo_catalog.GeoCatalog(geo_catalog.static_catalog(), self.place_catalog())
        except geo_catalog.CatalogError as exc:
            raise RecommendationError("DATA_UNAVAILABLE", f"Geographic catalog unavailable: {exc}") from None

    def catalog_version(self):
        """Version of the catalog facts are computed from (pool_key, data_version)."""
        try:
            static = geo_catalog.static_catalog()
            version, source = self.places_version()
        except (geo_catalog.CatalogError, RecommendationError) as exc:
            raise VersionUnavailable(str(exc)) from None
        return geo_catalog.version_of(static, version, source)

    def resolve_criteria(self, criteria, settings=None, catalog=None):
        """{criterion_id: Resolution} for the geographic criteria (deterministic code)."""
        settings = _settings(settings)
        return resolve_criteria(catalog or self.catalog(), criteria, settings.entity_resolution.max_candidates)

    def evidence(self, listings, criteria, resolutions=None, settings=None):
        settings = _settings(settings)
        catalog = self.catalog()
        missing = [c for c in criteria if c.criterion_id not in (resolutions or {})]
        if missing:  # resolve here only what the caller's run-scoped resolution does not cover
            resolutions = {**(resolutions or {}), **self.resolve_criteria(missing, settings, catalog)}
        results = []
        self.last_snapshot = []
        for c in criteria:
            facts, record = measure(catalog, c, resolutions[c.criterion_id], listings,
                                    settings.geography.station_distance_method)
            self.last_snapshot.append(record)
            for listing, fact in zip(listings, facts):
                h = digest({"listing_id": listing.listing_id, "latitude": listing.latitude,
                            "longitude": listing.longitude, "criterion": c.model_dump(), "fact": fact})
                results.append(
                    Evidence(
                        evidence_id=h,
                        listing_id=listing.listing_id,
                        criterion_id=c.criterion_id,
                        source=fact["source"] or self.source,
                        observed_at=now(),
                        value=fact["value"],
                        unit=c.unit,
                        distance_type=c.distance_type,
                        method=fact["method"],
                        coverage_m=None,
                        destination_id=fact["destination_id"],
                        unknown_reason=fact["unknown_reason"],
                        snapshot_hash=h,
                        # Facts come from SQL coordinates and the catalog, not from graph mappings.
                        mapping_status="not_applicable",
                    )
                )
        return results


class FixtureGeographicRepository(GeographicBase):
    source = "synthetic-fixture:places"
    namespaces = ("fixture-",)  # synthetic place IDs (the real namespaces OSM:/LTA:/URA: also apply)

    def __init__(self):
        self.places = [
            {
                "place_id": "fixture-school",
                "name": "Fixture School",
                "category": "School",
                "latitude": 1.3,
                "longitude": 103.8,
            }
        ]

    def places_version(self):
        return digest(self.places), self.source

    def place_catalog(self):
        places = [dict(p) for p in self.places]
        return geo_catalog.place_catalog(self.source, digest(places), lambda: places, self.namespaces)

    def health(self):
        return {"contacted": True, "mode": "fixture", "places": len(self.places)}

    def close(self):
        pass


PLACES_QUERY = (
    "MATCH (p:Place) WHERE p.active = true RETURN p.place_id AS place_id, p.name AS name, "
    "p.name_local AS name_local, p.full_name AS full_name, p.alt_names AS alt_names, p.category AS category, "
    "p.latitude AS latitude, p.longitude AS longitude ORDER BY place_id"
)
DATASETS_QUERY = (
    "MATCH (d:Dataset) RETURN d.dataset_id AS dataset_id, d.snapshot_sha256 AS snapshot_sha256 ORDER BY dataset_id"
)


class Neo4jGeographicRepository(GeographicBase):
    source = "neo4j:Place"

    def __init__(self, driver, database):
        self.driver = driver
        self.database = database

    def query(self, query, **parameters):
        try:
            with self.driver.session(
                database=self.database, default_access_mode="READ"
            ) as session:
                from neo4j import unit_of_work

                @unit_of_work(timeout=10)
                def read(tx):
                    import json

                    return json.loads(
                        json.dumps(
                            [r.data() for r in tx.run(query, parameters)], default=str
                        )
                    )

                return session.execute_read(read)
        except Exception:
            raise RecommendationError(
                "DATA_UNAVAILABLE", "Neo4j read/schema check failed", True
            ) from None

    def datasets(self):
        return self.query(DATASETS_QUERY)

    def places_version(self):
        datasets = self.datasets()
        if not datasets or any(not d.get("snapshot_sha256") for d in datasets):
            raise VersionUnavailable("Neo4j Dataset.snapshot_sha256 missing")
        return {"database": self.database, "datasets": datasets}, self.source

    def place_catalog(self):
        datasets = self.datasets()
        versioned = bool(datasets) and all(d.get("snapshot_sha256") for d in datasets)
        # Without a Dataset snapshot the places are read for this call only, never cached.
        return geo_catalog.place_catalog(
            self.source, {"database": self.database, "datasets": datasets},
            lambda: self.query(PLACES_QUERY), cacheable=versioned,
        )

    def health(self):
        rows = self.query("MATCH (p:Place) RETURN count(p) AS places")
        return {"contacted": True, "database": self.database, **rows[0]}

    def close(self):
        self.driver.close()
