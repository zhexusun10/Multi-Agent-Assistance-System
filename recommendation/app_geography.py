"""Application location data overlay for native Recommendation 2.0.

URA, LTA, OSM measurements, caching and final gate use upstream code. Only the
application's hash-verified neighbourhood entities extend the place catalogue.
"""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

from .config import RecommendationConfig
from .contracts.errors import RecommendationError
from .geography import catalog as geo_catalog
from .geography.normalize import normalize_place
from .geography.resolver import resolve, resolve_criteria
from .repositories.geographic_repository import GeographicBase

ROOT = Path(__file__).resolve().parents[1]


def neighbourhood_catalog(root=ROOT):
    try:
        metadata = json.loads((root / "knowledge/locations/neighbourhoods.json").read_text(encoding="utf-8"))
        raw = (root / "data/datasets/osm_neighbourhoods.json").read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        rows = json.loads(raw)
        if fingerprint != metadata["raw_sha256"] or len(rows) != metadata["count"]:
            raise ValueError("catalogue fingerprint/count mismatch")
        ids = set()
        for row in rows:
            if (not row["place_id"].startswith("OSM:node:") or row["place_id"] in ids
                    or row["category"] != "Neighbourhood"
                    or row["osm_place_type"] not in ("neighbourhood", "suburb")
                    or not geo_catalog.valid_point(row["latitude"], row["longitude"])):
                raise ValueError("invalid neighbourhood entity")
            ids.add(row["place_id"])
        return rows, fingerprint
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RecommendationError("DATA_UNAVAILABLE", "Application neighbourhood catalogue is invalid", True) from exc


class ApplicationGeographicRepository(GeographicBase):
    def __init__(self, native):
        self.native = native
        self.source = native.source
        self.last_snapshot = []

    def catalog(self):
        base = self.native.catalog()
        rows, fingerprint = neighbourhood_catalog()

        def combined():
            existing = {p.place_id: asdict(p) for p in base.places.places}
            for row in rows:
                if row["place_id"] in existing:
                    p = existing[row["place_id"]]
                    if any(p.get(k) != row.get(k) for k in ("name", "latitude", "longitude")):
                        raise RecommendationError("DATA_UNAVAILABLE", "Neighbourhood catalogue identity conflict")
                    continue
                existing[row["place_id"]] = {**row, "alt_names": row.get("aliases", [])}
            return list(existing.values())

        places = geo_catalog.place_catalog(
            base.places.source + "+application:osm_neighbourhoods",
            {"native": base.places.version, "neighbourhoods_sha256": fingerprint},
            combined, namespaces=base.places.namespaces,
        )
        return geo_catalog.GeoCatalog(base.static, places)

    def catalog_version(self):
        return self.catalog().version

    def resolve_criteria(self, criteria, settings=None, catalog=None):
        settings = settings or RecommendationConfig.default()
        catalog = catalog or self.catalog()
        resolved = resolve_criteria(catalog, criteria, settings.entity_resolution.max_candidates)
        neighbourhoods, _ = neighbourhood_catalog()
        by_id = {r["place_id"]: r for r in neighbourhoods}
        for c in criteria:
            row = by_id.get(c.destination_id)
            if row is None or c.category:
                continue
            # Keep the Requirement Agent's verified neighbourhood ID only when
            # its name is an exact catalogue alias. Wrong ID/name pairs retain
            # upstream ambiguity checks; same-named stations remain distinct.
            aliases = [row["name"], row.get("name_local"), *row.get("aliases", [])]
            if c.destination_name and normalize_place(c.destination_name) not in {normalize_place(n) for n in aliases if n}:
                continue
            result = resolve(catalog, destination_id=c.destination_id,
                             max_candidates=settings.entity_resolution.max_candidates)
            resolved[c.criterion_id] = replace(result, method="application_verified_neighbourhood_id")
        return resolved

    def health(self):
        return self.native.health()

    def close(self):
        self.native.close()
