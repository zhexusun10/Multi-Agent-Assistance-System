from types import SimpleNamespace
from ..ranking.metrics import haversine
from ..tracing.trace import digest, now
from recommendation.contracts.evidence import Evidence
from recommendation.contracts.errors import RecommendationError


class GeographicBase:
    def evidence(self, listings, criteria):
        results = []
        self.last_snapshot = []
        for c in criteria:
            places = self.resolve(c)
            relations = self.relations(
                [r.listing_id for r in listings], [p["place_id"] for p in places]
            )
            self.last_snapshot.append(
                {
                    "criterion_id": c.criterion_id,
                    "places": places,
                    "relationships": list(relations.values()),
                }
            )
            for listing in listings:
                reason = None
                value = None
                method = "unavailable"
                place_id = None
                coverage = None
                if c.field == "commute" or c.distance_type != "straight_line":
                    reason = "unsupported travel mode: route evidence unavailable"
                elif not places:
                    reason = "destination unresolved or category has no known places"
                elif not c.category and len(places) > 1:
                    reason = "ambiguous destination; supply place ID"
                else:
                    facts = []
                    for place in places:
                        graph = relations.get(
                            (listing.listing_id, place["place_id"]), {}
                        )
                        # SQL coordinates are canonical; edge distance is used only with matching graph coordinates.
                        match = (
                            graph.get("latitude") == listing.latitude
                            and graph.get("longitude") == listing.longitude
                        )
                        d = haversine(
                            listing,
                            SimpleNamespace(
                                latitude=place.get("latitude"),
                                longitude=place.get("longitude"),
                            ),
                        )
                        source = "coordinate_haversine"
                        if (
                            match
                            and graph.get("distance_type") == "geodesic"
                            and graph.get("distance_m") is not None
                            and d is not None
                            and abs(graph["distance_m"] - d) <= 1
                        ):
                            d = graph["distance_m"]
                            source = "neo4j.NEAR_PLACE"
                        if d is not None:
                            facts.append(
                                (d, place["place_id"], source, graph.get("radius_m"))
                            )
                    if facts:
                        value, place_id, method, coverage = min(
                            facts, key=lambda f: (f[0], f[1])
                        )
                    else:
                        reason = "missing valid coordinates/mapping; absent NEAR_PLACE edge is not absence of amenity"
                raw = {
                    "listing": listing.model_dump(),
                    "criterion": c.model_dump(),
                    "places": places,
                    "value": value,
                    "method": method,
                    "coverage_m": coverage,
                }
                h = digest(raw)
                results.append(
                    Evidence(
                        evidence_id=h,
                        listing_id=listing.listing_id,
                        criterion_id=c.criterion_id,
                        source=self.source,
                        observed_at=now(),
                        value=value,
                        unit=c.unit,
                        distance_type=c.distance_type,
                        method=method,
                        coverage_m=coverage,
                        destination_id=place_id,
                        unknown_reason=reason,
                        snapshot_hash=h,
                        mapping_status=(
                            "not_applicable"
                            if self.source.startswith("synthetic")
                            else (
                                "known"
                                if any(
                                    key[0] == listing.listing_id for key in relations
                                )
                                else "missing"
                            )
                        ),
                    )
                )
        return results


class FixtureGeographicRepository(GeographicBase):
    source = "synthetic-fixture:places"

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

    def resolve(self, c):
        return [
            p
            for p in self.places
            if (
                c.destination_id == p["place_id"]
                or c.destination_name == p["name"]
                or c.category == p["category"]
            )
        ]

    def relations(self, listing_ids, place_ids):
        return {}

    def health(self):
        return {"contacted": True, "mode": "fixture", "places": len(self.places)}

    def close(self):
        pass


class Neo4jGeographicRepository(GeographicBase):
    source = "neo4j:Place/NEAR_PLACE"

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

    def resolve(self, c):
        return self.query(
            "MATCH (p:Place) WHERE p.active = true AND (($id IS NOT NULL AND p.place_id=$id) OR ($name IS NOT NULL AND toLower(p.name)=toLower($name)) OR ($category IS NOT NULL AND p.category=$category)) RETURN p.place_id AS place_id, p.name AS name, p.category AS category, p.latitude AS latitude, p.longitude AS longitude, p.observed_at AS observed_at ORDER BY p.place_id",
            id=c.destination_id,
            name=c.destination_name,
            category=c.category,
        )

    def relations(self, listing_ids, place_ids):
        rows = self.query(
            "MATCH (l:Listing) WHERE l.listing_id IN $ids OPTIONAL MATCH (l)-[r:NEAR_PLACE]->(p:Place) WHERE p.place_id IN $places RETURN l.listing_id AS listing_id,p.place_id AS place_id,l.latitude AS latitude,l.longitude AS longitude,r.distance_m AS distance_m,r.distance_type AS distance_type,r.radius_m AS radius_m",
            ids=[int(value) if value.isdecimal() else value for value in listing_ids],
            places=place_ids,
        )
        return {(str(r["listing_id"]), r["place_id"]): r for r in rows}

    def health(self):
        rows = self.query("MATCH (p:Place) RETURN count(p) AS places")
        return {"contacted": True, "database": self.database, **rows[0]}

    def close(self):
        self.driver.close()
