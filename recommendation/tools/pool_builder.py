"""Build or reuse the hard-criteria exploration pool (spec section 5, R4, R5).

Pool = every RENT listing whose hard criteria ALL pass, including the geographic ones.
Each listing goes into exactly one bucket: any hard fail -> excluded_hard_fail; otherwise any
hard unknown/unsupported -> excluded_hard_unknown; otherwise eligible. So
source_count == size + fail + unknown.

pool_key = digest(POOL_FORMAT_VERSION, listing_type, the sorted unique canonical hard-criterion
keys, digest(geography settings), geo catalog version). A canonical key holds the field, the
operator, the canonical value ('in' lists sorted and deduplicated, numbers as float, property
types casefolded), unit, distance_type, transport_mode and the geographic target. Criterion
IDs and weights are not part of it. The geographic target is the RESOLVED entity
(``resolved``: criterion_id -> (entity_id, entity_kind) from recommendation/geography/resolver.py;
for a category target the category itself with kind 'category'), so two spellings that resolve
to one entity share a pool. A target that is unresolved or ambiguous keeps the request's own
destination_id / destination_name / category verbatim (its facts are all unknown; verbatim can
only cause an extra miss). The catalog version in the key covers catalog/alias changes.

Pool content (hashed into pool_version; it holds no timestamps): eligible ListingSummary rows,
hard-geo facts of eligible rows keyed by canonical key, native resolution status/entity per hard-geo key,
and the partition counts with capped unknown_reasons. On a cache hit, Evidence and
CriterionResult are rebuilt from the facts with the CURRENT request's criterion IDs.
evidence_id = digest(canonical key, listing, value, method): no timestamp and no criterion_id.
Two criteria with the same canonical key (e.g. a hard and a soft distance to one target)
therefore share evidence IDs; the response carries one Evidence per (criterion, evidence_id)
(assembly.returned_evidence), so every reference resolves to evidence of its own criterion.
"""

import json, time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from recommendation.contracts.evidence import Evidence
from recommendation.contracts.requirements import NUMERIC
from recommendation.contracts.summary import UNKNOWN_REASON_KEYS
from ..models.listing_summary import ListingSummary
from ..geography.resolver import key_map
from ..settings.loader import sha256_json
from ..state import data_version as versions
from ..tracing.trace import digest
from ..validation.rules import evaluate

# Bump on ANY change to pool content, normalisation, partition or hard-geo evaluation code.
# v4: TS-identical explicit-station rule, generic MRT names, ID+name targets, places without coordinates
# unresolved, accurate missing-coordinate reason, key-only resolution in the content.
# v3: native geography (resolved entity keys, catalog distances); v2 verbatim target.
POOL_FORMAT_VERSION = "pool-v4"
GEO_FIELDS = ("distance", "commute")
FACT_FIELDS = ("value", "unit", "distance_type", "method", "coverage_m", "destination_id",
               "unknown_reason", "mapping_status", "source")


# --- canonical keys ------------------------------------------------------------------------
def hard_criteria(request):
    return [c for c in request.requirements.criteria if c.strength == "hard"]


def canonical_value(c):
    if c.field in NUMERIC:
        return [float(v) for v in c.value] if isinstance(c.value, list) else float(c.value)
    fold = (lambda v: v.strip().casefold()) if c.field == "property_type" else (lambda v: v)
    if c.operator == "in":
        return sorted({fold(v) for v in c.value})
    return fold(c.value)


def criterion_key(c, resolved=None):
    key = {
        "field": c.field, "operator": c.operator, "value": canonical_value(c), "unit": c.unit,
        "distance_type": c.distance_type, "transport_mode": c.transport_mode,
    }
    if c.field in GEO_FIELDS:
        entity = (resolved or {}).get(c.criterion_id)
        if entity:
            key["entity"] = {"id": entity[0], "kind": entity[1]}
        else:  # unresolved / ambiguous / not resolved yet: the request's own target, verbatim
            key["target"] = {
                "destination_id": c.destination_id,
                "destination_name": c.destination_name,
                "category": c.category,
            }
    return key


def key_digest(key):
    return digest(key)


def geography_digest(settings):
    return sha256_json(settings.geography.model_dump(mode="json"))


def pool_key(request, settings, geo_version, resolved=None):
    hard = sorted({json.dumps(criterion_key(c, resolved), sort_keys=True) for c in hard_criteria(request)})
    return digest({
        "format": POOL_FORMAT_VERSION,
        "listing_type": request.requirements.listing_type,
        "hard": hard,
        "geography": geography_digest(settings),
        "catalog": geo_version or versions.UNAVAILABLE,
    })


def capped(reasons):
    ordered = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
    kept = dict(ordered[:UNKNOWN_REASON_KEYS])
    rest = sum(count for _, count in ordered[UNKNOWN_REASON_KEYS:])
    if rest:
        kept["other"] = rest
    return kept


# --- evidence ------------------------------------------------------------------------------
def fact_of(evidence):
    return {name: getattr(evidence, name) for name in FACT_FIELDS}


def evidence_from_fact(fact, criterion, listing, observed_at, resolved=None):
    kd = key_digest(criterion_key(criterion, resolved))
    return Evidence(
        evidence_id=digest({"key": kd, "listing_id": listing.listing_id, "value": fact["value"],
                            "method": fact["method"]}),
        listing_id=listing.listing_id,
        criterion_id=criterion.criterion_id,
        observed_at=observed_at,
        snapshot_hash=digest({"key": kd, "listing_id": listing.listing_id, "latitude": listing.latitude,
                              "longitude": listing.longitude, "fact": fact}),
        **fact,
    )


def normalize_evidence(evidence, criteria, rows_by_id, resolved=None):
    """Repository evidence -> R4 evidence IDs (canonical key, no criterion ID, no timestamp)."""
    by_id = {c.criterion_id: c for c in criteria}
    return [
        evidence_from_fact(fact_of(e), by_id[e.criterion_id], rows_by_id[e.listing_id], e.observed_at, resolved)
        for e in evidence
    ]


def by_listing(evidence):
    grouped = {}
    for e in evidence:
        grouped.setdefault(e.listing_id, []).append(e)
    return grouped


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def resolution_summary(record):
    """Key-relevant native resolution of a geographic record (repository ``last_snapshot`` entry):
    status and entity only. The method/reason (name, id, id+name, bound_search, ...) describe HOW
    one request reached the entity, so they stay out of the hashed content: requests that share a
    pool_key share a pool_version whatever their spelling or cache history."""
    resolution = record["resolution"]
    return {k: resolution[k] for k in ("status", "entity_id", "entity_kind")}


# --- pool ----------------------------------------------------------------------------------
@dataclass
class Pool:
    key: str
    version: str
    data_version: str  # versions.UNAVAILABLE when the fingerprint/catalog version failed
    cache: str  # built | reused | bypassed
    content: dict
    rows: list
    created_at: float
    outcome: str  # hit | miss | expired | content_missing | disabled | no_store | fingerprint_unavailable | catalog_unavailable
    source_digest: str | None = None  # digest of every source row (bypass path final gate)
    warnings: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    persisted: bool = False

    @property
    def partition(self):
        return self.content["partition"]

    def reference(self):
        return {"pool_key": self.key, "pool_version": self.version, "data_version": self.data_version,
                "cache": self.cache, "outcome": self.outcome}

    def hard_evidence(self, request, resolved=None):
        """Hard-geo Evidence for every pool row, rebuilt with the CURRENT criterion IDs."""
        rows = {r.listing_id: r for r in self.rows}
        observed = iso(self.created_at)
        evidence = []
        for c in hard_criteria(request):
            if c.field not in GEO_FIELDS:
                continue
            facts = self.content["facts"].get(key_digest(criterion_key(c, resolved)), {}).get("listings", {})
            for listing_id in sorted(facts):
                if listing_id in rows:
                    evidence.append(evidence_from_fact(facts[listing_id], c, rows[listing_id], observed, resolved))
        return evidence


def parse_rows(content):
    return [ListingSummary.model_validate(r) for r in content["rows"]]


def build_content(request, rows, geographic, resolved=None, resolutions=None, settings=None):
    """Partition all source rows and keep eligible rows + their hard-geo facts (no timestamps).

    ``resolutions`` (criterion_id -> geography.resolver.Resolution) are the run's native
    resolutions; the repository resolves itself when they are not given."""
    hard = hard_criteria(request)
    cheap = [c for c in hard if c.field not in GEO_FIELDS]
    geo = [c for c in hard if c.field in GEO_FIELDS]
    stats = {}
    started = time.monotonic()
    cheap_results = {r.listing_id: evaluate(r, cheap, []) for r in rows}
    survivors = [r for r in rows if not any(v.outcome == "fail" for v in cheap_results[r.listing_id])]
    stats["cheap_evaluate_ms"] = (time.monotonic() - started) * 1000
    started = time.monotonic()
    raw, records = [], []
    if geo:
        raw = geographic.evidence(survivors, geo, resolutions=resolutions, settings=settings)
        records = list(getattr(geographic, "last_snapshot", []))
    stats["hard_geo_ms"] = (time.monotonic() - started) * 1000
    stats["hard_geo_listings"] = len(survivors) if geo else 0
    by_id = {r.listing_id: r for r in rows}
    evidence = normalize_evidence(raw, geo, by_id, resolved)
    grouped = by_listing(evidence)
    by_criterion = {c.criterion_id: c for c in hard}
    fail = unknown = 0
    reasons = Counter()
    eligible = []
    for row in rows:
        results = cheap_results[row.listing_id]
        if not any(v.outcome == "fail" for v in results):
            results = results + evaluate(row, geo, grouped.get(row.listing_id, []))
        if any(v.outcome == "fail" for v in results):
            fail += 1
        elif any(v.outcome != "pass" for v in results):
            unknown += 1
            reasons.update({f"{by_criterion[v.criterion_id].field}: {v.reason}" for v in results if v.outcome != "pass"})
        else:
            eligible.append(row)
    eligible_ids = {r.listing_id for r in eligible}
    facts = {}
    for c in geo:
        kd = key_digest(criterion_key(c, resolved))
        entry = facts.setdefault(kd, {"key": criterion_key(c, resolved), "listings": {}})
        for e in evidence:
            if e.criterion_id == c.criterion_id and e.listing_id in eligible_ids:
                entry["listings"][e.listing_id] = fact_of(e)
    by_record = {r["criterion_id"]: r for r in records}
    resolution = {
        key_digest(criterion_key(c, resolved)): resolution_summary(by_record[c.criterion_id])
        for c in geo if c.criterion_id in by_record
    }
    content = {
        "format": POOL_FORMAT_VERSION,
        "listing_type": request.requirements.listing_type,
        "hard_keys": [json.loads(k) for k in sorted({json.dumps(criterion_key(c, resolved), sort_keys=True) for c in hard})],
        "rows": [r.model_dump() for r in sorted(eligible, key=lambda r: r.listing_id)],
        "facts": facts,
        "resolution": resolution,
        "partition": {
            "source_count": len(rows), "size": len(eligible), "excluded_hard_fail": fail,
            "excluded_hard_unknown": unknown, "unknown_reasons": capped(reasons),
        },
    }
    return content, sorted(eligible, key=lambda r: r.listing_id), stats


def pool_version(content):
    return digest(content)


def build_pool(request, settings, listing, geographic, cache=None, resolved=None, clock=None, resolutions=None):
    """Reuse the conversation's cached pool for (pool_key, data_version), or build and store it.

    Hit: no full retrieve and no hard-geo evaluation (one fingerprint query + cache read).
    Miss: full retrieve and fingerprint in one REPEATABLE READ transaction, then evaluation.
    A fingerprint/catalog failure bypasses the cache: the pool is rebuilt and never indexed.
    """
    stats = {}
    warnings = []
    conversation = request.conversation_id
    if resolutions is None and any(c.field in GEO_FIELDS for c in request.requirements.criteria):
        resolutions = geographic.resolve_criteria(request.requirements.criteria, settings)
    if resolved is None and resolutions is not None:
        resolved = key_map(resolutions)
    started = time.monotonic()
    try:
        geo_version = versions.catalog_version(geographic)
    except versions.VersionUnavailable:
        geo_version = None
    stats["catalog_ms"] = (time.monotonic() - started) * 1000
    key = pool_key(request, settings, geo_version, resolved)
    usable = cache is not None and settings.cache.enabled
    outcome = "disabled" if cache is not None else "no_store"
    if usable and geo_version is None:
        outcome = "catalog_unavailable"
    elif usable:
        started = time.monotonic()
        try:
            fingerprint = listing.fingerprint()
        except Exception:
            fingerprint = None
        stats["fingerprint_ms"] = (time.monotonic() - started) * 1000
        current = versions.data_version(fingerprint, geo_version)
        if current is None:
            outcome = "fingerprint_unavailable"
        else:
            started = time.monotonic()
            hit, outcome = cache.lookup(conversation, key, current)
            stats["cache_read_ms"] = (time.monotonic() - started) * 1000
            if hit:
                stats.update(rows_transferred=0, cache_source=hit["source"])
                rows = hit["rows"] if hit["rows"] is not None else parse_rows(hit["content"])
                return Pool(key, hit["pool_version"], current, "reused", hit["content"], rows,
                            hit["created_at"], "hit", stats=stats, persisted=True)
    started = time.monotonic()
    source, fingerprint = listing.retrieve_versioned()
    stats["retrieve_ms"] = (time.monotonic() - started) * 1000
    stats["rows_transferred"] = len(source)
    version = versions.data_version(fingerprint, geo_version)
    content, rows, build_stats = build_content(request, source, geographic, resolved, resolutions, settings)
    stats.update(build_stats)
    pv = pool_version(content)
    indexed = usable and version is not None and outcome not in ("catalog_unavailable", "fingerprint_unavailable")
    if usable and not indexed:
        if outcome == "catalog_unavailable" or geo_version is None:
            outcome = "catalog_unavailable"
            warnings.append("Geographic catalog version unavailable; the pool was rebuilt without the cache.")
        else:
            outcome = "fingerprint_unavailable"
            warnings.append("Listing data version unavailable; the pool was rebuilt without the cache.")
    cache_status = "built" if indexed else "bypassed"
    data_version = version or versions.UNAVAILABLE
    created_at = (clock or time.time)()
    persisted = False
    if cache is not None:
        started = time.monotonic()
        # Content is always retained for '<trace>/pool'; only a verified data version is indexed.
        created_at = cache.save(conversation, key, data_version, pv, content, rows, index=indexed)
        stats["cache_write_ms"] = (time.monotonic() - started) * 1000
        persisted = True
    return Pool(key, pv, data_version, cache_status, content, rows, created_at, outcome,
                source_digest=digest([r.model_dump() for r in source]), warnings=warnings,
                stats=stats, persisted=persisted)
