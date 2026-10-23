"""Read-only data access for the P7 analyses, through the PRODUCTION code paths.

``Data`` opens the repositories once (database: PostgreSQL READ ONLY REPEATABLE READ
transactions and Neo4j READ sessions, exactly as the service; fixture: the synthetic fixture
files), reads every RENT row ONCE and then builds the hard-criteria pool of any scenario with
``tools.pool_builder.build_content`` (the same partition, hard-geo evaluation and resolution the
build_pool stage runs, minus the cache), and scores it with ``ranking.scoring_v2.score_pool``
after evaluating every criterion like the rank_candidates stage. Nothing is written anywhere.
"""

import time
from dataclasses import dataclass

from recommendation.contracts.feedback import FeedbackSnapshot
from recommendation.contracts.request import RecommendationRequest
from ..geography.resolver import key_map
from ..ranking.scoring_v2 import score_pool
from ..repositories.connections import load_environment, repositories
from ..tools.pool_builder import GEO_FIELDS, build_content, by_listing, normalize_evidence, pool_key, pool_version
from ..validation.rules import eligible, evaluate


@dataclass
class PoolBuild:
    name: str
    request: RecommendationRequest
    rows: list
    content: dict
    resolutions: dict
    resolved: dict
    build_ms: float
    key: str
    version: str

    @property
    def partition(self):
        return self.content["partition"]


class Data:
    """Listing rows (read once) + geographic repository, read-only."""

    def __init__(self, mode="database"):
        if mode == "database":
            load_environment()
        self.mode = mode
        self.listing, self.geographic = repositories(mode)
        started = time.perf_counter()
        self.rows, self.fingerprint = self.listing.retrieve_versioned()
        self.retrieve_ms = (time.perf_counter() - started) * 1000

    def close(self):
        self.listing.close()
        self.geographic.close()

    def request(self, criteria, conversation="analysis", request_id="analysis", feedback=None, **extra):
        body = {"request_id": request_id, "conversation_id": conversation, "requirements_version": 1,
                "requirements": {"listing_type": "RENT", "criteria": list(criteria)}, **extra}
        if feedback is not None:
            body["feedback"] = feedback
        return RecommendationRequest.model_validate(body)

    def build(self, name, hard_criteria, settings):
        """Hard-criteria pool of a scenario (production partition and hard-geo evaluation)."""
        request = self.request(hard_criteria)
        geo = [c for c in request.requirements.criteria if c.field in GEO_FIELDS]
        started = time.perf_counter()
        resolutions = self.geographic.resolve_criteria(geo, settings) if geo else {}
        resolved = key_map(resolutions)
        content, rows, _ = build_content(request, self.rows, self.geographic, resolved, resolutions, settings)
        build_ms = (time.perf_counter() - started) * 1000
        try:
            geo_version = self.geographic.catalog_version()
        except Exception:  # pragma: no cover - only the key text changes
            geo_version = None
        return PoolBuild(name, request, rows, content, resolutions, resolved, build_ms,
                         pool_key(request, settings, geo_version, resolved), pool_version(content))

    def score(self, build, soft_criteria, settings, feedback=None):
        """PoolScores of ``build`` under hard + ``soft_criteria`` (rank stage semantics)."""
        request = self.request(build.request.model_dump(mode="json")["requirements"]["criteria"] + list(soft_criteria))
        criteria = request.requirements.criteria
        soft_geo = [c for c in criteria if c.field in GEO_FIELDS and c.strength == "soft"]
        by_id = {r.listing_id: r for r in build.rows}
        hard_geo = [c for c in criteria if c.field in GEO_FIELDS and c.strength == "hard"]
        evidence = []
        if hard_geo:
            # Same facts the pool holds (rebuilt with the request's criterion IDs, as build_pool does).
            from ..tools.pool_builder import Pool

            pool = Pool(build.key, build.version, "analysis", "built", build.content, build.rows, 0.0, "analysis")
            evidence += pool.hard_evidence(request, build.resolved)
        if soft_geo:
            resolutions = {**build.resolutions, **self.geographic.resolve_criteria(soft_geo, settings)}
            fresh = self.geographic.evidence(build.rows, soft_geo, resolutions=resolutions, settings=settings)
            evidence += normalize_evidence(fresh, soft_geo, by_id, key_map(resolutions))
        grouped = by_listing(evidence)
        results = {r.listing_id: evaluate(r, criteria, grouped.get(r.listing_id, [])) for r in build.rows}
        valid = [r for r in build.rows if eligible(results[r.listing_id], criteria)]
        snapshot = FeedbackSnapshot.model_validate(feedback or {})
        return score_pool(valid, criteria, results, grouped, snapshot, settings), request.model_copy(
            update={"feedback": snapshot, "result_limit": settings.recommendation.default_result_limit})
