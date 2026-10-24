"""Application-only geographic data adapter, not a replacement ranker.

OSM evidence delegates byte-for-byte to the upstream repository. Official area
boundaries/station exits use the application's existing hash-checked geometry.
Native filtering, utilities, weights, ranking and final reread remain unchanged.
"""
import json
import math
import os
from pathlib import Path
import subprocess

from .contracts.evidence import Evidence
from .contracts.errors import RecommendationError
from .tracing.trace import digest, now

ROOT = Path(__file__).resolve().parents[1]


def measure_official(listings, criteria):
    request = {
        "listings": [{"listing_id": l.listing_id, "latitude": l.latitude, "longitude": l.longitude} for l in listings],
        "criteria": [{"criterion_id": c.criterion_id, "destination_id": c.destination_id, "field": c.field, "distance_type": c.distance_type} for c in criteria],
    }
    try:
        result = subprocess.run(
            [os.getenv("NODE_BINARY", "node"), str(ROOT / "scripts/official-geography.mjs")],
            input=json.dumps(request), capture_output=True, text=True, encoding="utf-8",
            cwd=ROOT, timeout=90, check=True,
        )
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise RecommendationError("DATA_UNAVAILABLE", "Official geographic catalogue/measurement bridge unavailable", True) from None


class ApplicationGeographicRepository:
    def __init__(self, native, measure=measure_official):
        self.native = native
        self.measure = measure
        self.last_snapshot = []

    def evidence(self, listings, criteria):
        official = [c for c in criteria if (c.destination_id or "").startswith(("URA:", "LTA:"))]
        ordinary = [c for c in criteria if c not in official]
        results = self.native.evidence(listings, ordinary) if ordinary else []
        self.last_snapshot = list(getattr(self.native, "last_snapshot", [])) if ordinary else []
        if not official:
            return results
        # Resolve the same stable entities already projected into the KG. Do not
        # substitute a similarly named station, centroid, or arbitrary POI.
        nodes = self.native.query(
            "MATCH (n:GeoLocation) WHERE n.location_id IN $ids RETURN n.location_id AS place_id,n.name AS name,n.source AS source,n.dataset_version AS dataset_version,labels(n) AS labels",
            ids=list(dict.fromkeys(c.destination_id for c in official)),
        )
        by_id = {n["place_id"]: n for n in nodes}
        known = [c for c in official if c.destination_id in by_id]
        measured = self.measure(listings, known) if known and listings else {"measurements": [], "catalogues": []}
        observations = {}
        for value in measured["measurements"]:
            key = (value["listing_id"], value["criterion_id"])
            if key in observations:
                raise RecommendationError("DATA_UNAVAILABLE", "Duplicate official geographic observation")
            observations[key] = value
        expected = {(l.listing_id, c.criterion_id) for c in known for l in listings}
        if set(observations) != expected:
            raise RecommendationError("DATA_UNAVAILABLE", "Official geographic evidence scope mismatch")
        official_results = []
        for c in official:
            node = by_id.get(c.destination_id)
            self.last_snapshot.append({"criterion_id": c.criterion_id, "places": [node] if node else [], "official_catalogues": [v for v in measured["catalogues"] if v["place_id"] == c.destination_id]})
            for listing in listings:
                item = observations.get((listing.listing_id, c.criterion_id))
                value, reason, method = None, "official destination is not projected in Knowledge Graph", "unavailable"
                if node:
                    if not item:
                        raise RecommendationError("DATA_UNAVAILABLE", "Incomplete official geographic evidence")
                    catalogue = item.get("catalogue")
                    if catalogue and (catalogue["place_id"] != c.destination_id or catalogue["name"] != node["name"] or node.get("dataset_version") and catalogue["version"] != node["dataset_version"]):
                        raise RecommendationError("DATA_UNAVAILABLE", "Knowledge Graph and official catalogue identity/version mismatch")
                    value, reason, method = item["value"], item["unknown_reason"], item["method"]
                    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or reason or item.get("destination_id") != c.destination_id):
                        raise RecommendationError("DATA_UNAVAILABLE", "Invalid official distance measurement")
                raw = {"listing": listing.model_dump(), "criterion": c.model_dump(), "graph": node, "measurement": item}
                proof = digest(raw)
                official_results.append(Evidence(
                    evidence_id=proof, listing_id=listing.listing_id, criterion_id=c.criterion_id,
                    source="application:official_catalog/GeoLocation", observed_at=now(), value=value, unit=c.unit,
                    distance_type=c.distance_type, method=method, coverage_m=None,
                    destination_id=c.destination_id if node else None, unknown_reason=reason,
                    mapping_status="known" if node else "missing", snapshot_hash=proof,
                ))
        indexed = {(e.listing_id, e.criterion_id): e for e in results + official_results}
        return [indexed[(l.listing_id, c.criterion_id)] for c in criteria for l in listings]

    def health(self):
        return self.native.health()

    def close(self):
        self.native.close()
