"""P3 (spec section 13): pool cache, data_version, gates and the compact run snapshot.

Fixture data mode with the scripted controller and a private state file (conftest isolation).
"""
import json
from pathlib import Path

import pytest

from recommendation.agent.controller import Proposal, ScriptedController
from recommendation.config import RecommendationConfig
from recommendation.contracts.errors import RecommendationError
from recommendation.contracts.request import RecommendationRequest
from recommendation.contracts.requirements import Criterion
from recommendation.models.candidate import Response
from recommendation.repositories.connections import repositories
from recommendation.repositories import listing_repository
from recommendation.service import RecommendationService
from recommendation.state import pool_cache as pool_cache_module
from recommendation.state.pool_cache import PoolCache, PoolMemory
from recommendation.state.store import StateStore
from recommendation.tools import pool_builder
from recommendation.tools.pool_builder import criterion_key, pool_key, pool_version
from recommendation.validation.gate import replay_gate

PACKAGE = Path(__file__).resolve().parents[1]
BUDGET = {"criterion_id": "budget", "field": "price", "operator": "lte", "value": 3000,
          "unit": "SGD/month", "strength": "hard"}
BEDROOMS = {"criterion_id": "bedrooms", "field": "bedrooms", "operator": "gte", "value": 2, "strength": "hard"}
DISTRICT = {"criterion_id": "district", "field": "district_code", "operator": "in", "value": ["D05"],
            "strength": "soft", "weight": 1}
SCHOOL = {"criterion_id": "school-distance", "field": "distance", "operator": "lte", "value": 1500,
          "unit": "m", "strength": "hard", "destination_id": "fixture-school", "distance_type": "straight_line"}
NEAR_SCHOOL = {"criterion_id": "nearer-school", "field": "distance", "operator": "lte", "value": 1500,
               "unit": "m", "strength": "soft", "category": "School", "distance_type": "straight_line",
               "utility": "proximity"}


@pytest.fixture(autouse=True)
def fresh_memory():
    pool_cache_module.MEMORY.clear()
    yield
    pool_cache_module.MEMORY.clear()


def settings(**overrides):
    return RecommendationConfig.default().with_overrides(overrides) if overrides else RecommendationConfig.default()


def make(tmp_path, config=None, name="state.sqlite"):
    listing, geo = repositories("fixture")
    return RecommendationService(listing, geo, StateStore(tmp_path / name), ScriptedController(), config=config)


def body(criteria, request_id="p3-1", conversation="p3", version=1, previous=None, **extra):
    return RecommendationRequest.model_validate({
        "request_id": request_id, "conversation_id": conversation, "requirements_version": version,
        "previous_state_id": previous, "requirements": {"listing_type": "RENT", "criteria": criteria}, **extra,
    })


def chain(service, criteria_list, conversation="p3"):
    """Consecutive requests of one conversation; returns the responses."""
    responses, previous, version, last = [], None, 0, None
    for index, criteria in enumerate(criteria_list):
        if criteria != last:
            version += 1
        last = criteria
        response = service.recommend(body(criteria, f"{conversation}-{index}", conversation, version, previous))
        assert response.status != "error", response.error
        responses.append(response)
        previous = response.state.state_id
    return responses


class Counting:
    """Count repository calls (and the listings passed to geographic evidence)."""

    def __init__(self, service):
        self.calls = {"retrieve": 0, "retrieve_versioned": 0, "lookup_versioned": 0, "fingerprint": 0}
        self.geo = []
        self.depth = 0
        listing, geo = service.listing, service.geographic
        for name in self.calls:
            original = getattr(listing, name)

            def wrapped(*args, _name=name, _original=original):
                # Count calls made by the dispatcher/gate, not the fixture's internal reuse of retrieve().
                if not self.depth:
                    self.calls[_name] += 1
                self.depth += 1
                try:
                    return _original(*args)
                finally:
                    self.depth -= 1

            setattr(listing, name, wrapped)
        original_evidence = geo.evidence

        def evidence(listings, criteria, **kwargs):
            self.geo.append((len(listings), sorted(c.criterion_id for c in criteria)))
            return original_evidence(listings, criteria, **kwargs)

        geo.evidence = evidence


# --- hit / miss ---------------------------------------------------------------------------
def test_soft_or_feedback_change_reuses_and_hard_change_rebuilds(tmp_path):
    from recommendation.terminal.__main__ import feedback_example

    service = make(tmp_path)
    try:
        first = service.recommend(body([BUDGET, BEDROOMS, DISTRICT]))
        assert first.pool.cache == "built"
        # Soft change (new requirements version) -> same pool reused.
        soft = dict(DISTRICT, value=["D21"])
        second = service.recommend(body([BUDGET, BEDROOMS, soft], "p3-2", version=2, previous=first.state.state_id))
        assert second.pool.cache == "reused"
        assert (second.pool.pool_key, second.pool.pool_version, second.pool.data_version) == (
            first.pool.pool_key, first.pool.pool_version, first.pool.data_version)
        assert [r.listing.listing_id for r in second.recommendations] != [r.listing.listing_id for r in first.recommendations]
        # Feedback change -> reused.
        feedback = feedback_example(second)
        third = service.recommend(body([BUDGET, BEDROOMS, soft], "p3-3", version=2, previous=second.state.state_id,
                                       feedback=feedback))
        assert third.pool.cache == "reused" and third.pool.pool_version == first.pool.pool_version
        # Hard change -> new pool_key, rebuilt.
        tighter = dict(BUDGET, value=2500)
        fourth = service.recommend(body([tighter, BEDROOMS, soft], "p3-4", version=3, previous=third.state.state_id))
        assert fourth.pool.cache == "built"
        assert fourth.pool.pool_key != first.pool.pool_key and fourth.pool.size < first.pool.size
        # Going back to the first hard criteria: its index entry is still among the 2 newest.
        fifth = service.recommend(body([BUDGET, BEDROOMS, DISTRICT], "p3-5", version=4, previous=fourth.state.state_id))
        assert fifth.pool.cache == "reused" and fifth.pool.pool_version == first.pool.pool_version
    finally:
        service.close()


def test_cache_hit_reads_no_full_pool_and_evaluates_no_hard_geography(tmp_path):
    service = make(tmp_path)
    counting = Counting(service)
    try:
        first, second = chain(service, [[SCHOOL, NEAR_SCHOOL], [SCHOOL, dict(NEAR_SCHOOL, weight=2)]])
        assert (first.pool.cache, second.pool.cache) == ("built", "reused")
        # Miss: one full versioned retrieve; hit: none. The gate never does a full retrieve.
        assert counting.calls["retrieve_versioned"] == 1
        assert counting.calls["retrieve"] == 0
        assert counting.calls["lookup_versioned"] == 2  # one by-id gate re-read per run
        hard_geo_calls = [g for g in counting.geo if g[1] == ["school-distance"]]
        assert len(hard_geo_calls) == 1, counting.geo  # hard geography evaluated on the miss only
        assert hard_geo_calls[0][0] == 12  # every source row (no cheap hard criteria)
        # The gate recomputes geography for the selected rows only (hard + soft criteria).
        gate_calls = [g for g in counting.geo if g[1] == ["nearer-school", "school-distance"]]
        assert [g[0] for g in gate_calls] == [len(first.recommendations), len(second.recommendations)]
        trace = service.store.trace("p3", "p3-1")
        assert trace["pool_stats"]["rows_transferred"] == 0 and trace["pool_ref"]["outcome"] == "hit"
        assert service.store.trace("p3", "p3-0")["pool_stats"]["rows_transferred"] == 12
    finally:
        service.close()


def test_data_update_and_deletion_trigger_refresh(tmp_path):
    service = make(tmp_path)
    try:
        criteria = [BUDGET, BEDROOMS, DISTRICT]
        first = service.recommend(body(criteria))
        same = service.recommend(body(criteria, "p3-2", previous=first.state.state_id))
        assert same.pool.cache == "reused"
        # Update of a row (even one outside the pool) changes data_version -> rebuild.
        row = next(r for r in service.listing.rows if r.listing_id == "fixture-11")
        row.title = "renamed"
        updated = service.recommend(body(criteria, "p3-3", previous=same.state.state_id))
        assert updated.pool.cache == "built" and updated.pool.data_version != first.pool.data_version
        assert updated.pool.pool_version == first.pool.pool_version  # pool content unaffected
        # An update that changes eligibility changes the pool content too.
        next(r for r in service.listing.rows if r.listing_id == "fixture-01").price = 9999.0
        priced = service.recommend(body(criteria, "p3-4", previous=updated.state.state_id))
        assert priced.pool.cache == "built" and priced.pool.size == first.pool.size - 1
        assert "fixture-01" not in [r.listing.listing_id for r in priced.recommendations]
        # Deletion -> rebuild without the deleted listing.
        service.listing.rows = [r for r in service.listing.rows if r.listing_id != "fixture-03"]
        deleted = service.recommend(body(criteria, "p3-5", previous=priced.state.state_id))
        assert deleted.pool.cache == "built" and deleted.pool.data_version != priced.pool.data_version
        assert deleted.pool.source_count == 11 and deleted.pool.size == priced.pool.size - 1
        assert "fixture-03" not in [r.listing.listing_id for r in deleted.recommendations]
    finally:
        service.close()


def test_conversations_are_isolated(tmp_path):
    service = make(tmp_path)
    try:
        a = service.recommend(body([BUDGET, BEDROOMS], "a-1", "conv-a"))
        b = service.recommend(body([BUDGET, BEDROOMS], "b-1", "conv-b"))
        assert (a.pool.cache, b.pool.cache) == ("built", "built")  # never shared across conversations
        assert (a.pool.pool_key, a.pool.pool_version) == (b.pool.pool_key, b.pool.pool_version)
        cache = PoolCache(service.store, service.config)
        assert len(cache.index_entries("conv-a")) == len(cache.index_entries("conv-b")) == 1
        again = service.recommend(body([BUDGET, BEDROOMS], "b-2", "conv-b", previous=b.state.state_id))
        assert again.pool.cache == "reused"
        assert len(cache.index_entries("conv-a")) == 1
        assert {k[0] for k in pool_cache_module.MEMORY.entries} == {"conv-a", "conv-b"}
        with pytest.raises(RecommendationError):  # pool content resolves only in its conversation
            service.store.resolve_reference("conv-b", a.state.candidate_pool_ref)
    finally:
        service.close()


def test_state_files_do_not_leak_through_the_process_memory(tmp_path):
    """The LRU is consulted only after the SQLite index confirms an entry."""
    one = make(tmp_path, name="one.sqlite")
    two = make(tmp_path, name="two.sqlite")
    try:
        assert one.recommend(body([BUDGET])).pool.cache == "built"
        assert two.recommend(body([BUDGET])).pool.cache == "built"
    finally:
        one.close()
        two.close()


# --- pool content -------------------------------------------------------------------------
def test_outputs_belong_to_the_pool_version_and_partition_invariant(tmp_path):
    service = make(tmp_path)
    try:
        scenarios = [
            [BUDGET, BEDROOMS, DISTRICT],
            [dict(BUDGET, value=3100), SCHOOL, NEAR_SCHOOL],
            [dict(BUDGET, value=1), BEDROOMS],
            [{"criterion_id": "furnished", "field": "furnishing", "operator": "eq", "value": "furnished",
              "strength": "hard"}],
            [DISTRICT],
        ]
        for index, criteria in enumerate(scenarios):
            response = service.recommend(body(criteria, f"part-{index}", f"part-{index}"))
            pool = response.pool
            assert pool.source_count == pool.size + pool.excluded_hard_fail + pool.excluded_hard_unknown == 12
            content = service.store.resolve_reference(f"part-{index}", response.state.candidate_pool_ref)
            assert pool_version({k: v for k, v in content.items() if k != "pool_ref"}) == pool.pool_version
            assert content["partition"]["size"] == pool.size == len(content["rows"])
            pool_ids = {r["listing_id"] for r in content["rows"]}
            assert {r.listing.listing_id for r in response.recommendations} <= pool_ids
            for item in response.recommendations:
                assert item.listing.model_dump() == next(r for r in content["rows"] if r["listing_id"] == item.listing.listing_id)
            snapshot = service.store.current(f"part-{index}")
            assert snapshot["pool"]["pool_version"] == pool.pool_version
        # fail > unknown: fixture-12 has no coordinates (distance unknown) AND price 3200 > 3100.
        geo = service.recommend(body([dict(BUDGET, value=3100), SCHOOL], "fail-first", "fail-first"))
        assert geo.pool.excluded_hard_unknown == 0  # fixture-10 (bedrooms null) is not constrained here
        assert geo.pool.excluded_hard_fail == 12 - geo.pool.size
        unknown = service.recommend(body([SCHOOL], "unknown", "unknown"))
        assert unknown.pool.excluded_hard_unknown == 1
        assert list(unknown.pool.unknown_reasons) == ["distance: listing has no valid coordinates (unknown; never 0 m)"]
    finally:
        service.close()


def test_unknown_reasons_are_capped():
    reasons = {f"r{i:02d}": i + 1 for i in range(14)}
    capped = pool_builder.capped(reasons)
    assert len(capped) == 11 and capped["other"] == sum(range(1, 5))
    assert "r13" in capped and "r00" not in capped


def test_pool_version_has_no_volatile_timestamps(tmp_path):
    first = make(tmp_path, name="a.sqlite")
    second = make(tmp_path, name="b.sqlite")
    try:
        a = first.recommend(body([SCHOOL, NEAR_SCHOOL], "t-1", "t-a"))
        b = second.recommend(body([SCHOOL, NEAR_SCHOOL], "t-2", "t-b"))
        assert a.pool.pool_version == b.pool.pool_version
        content = first.store.resolve_reference("t-a", a.state.candidate_pool_ref)
        assert "observed_at" not in json.dumps(content["facts"])
        assert "criterion_id" not in json.dumps(content["facts"])
    finally:
        first.close()
        second.close()


def test_criterion_ids_rebuilt_on_hit(tmp_path):
    service = make(tmp_path)
    try:
        first = service.recommend(body([SCHOOL, NEAR_SCHOOL]))
        renamed = dict(SCHOOL, criterion_id="school-within-1500", weight=3)
        second = service.recommend(body([renamed, NEAR_SCHOOL], "p3-2", version=2, previous=first.state.state_id))
        assert second.pool.cache == "reused" and second.pool.pool_key == first.pool.pool_key
        for item in second.recommendations:
            hard = next(r for r in item.criterion_results if r.criterion_id == "school-within-1500")
            assert hard.outcome == "pass" and hard.evidence_refs
            # The hard criterion's reference resolves to evidence of the hard criterion only
            # (different canonical key from the soft 'nearer-school', so the IDs cannot collide).
            assert {e.criterion_id for e in second.evidence if e.evidence_id == hard.evidence_refs[0]} == {"school-within-1500"}
            assert not any(r.criterion_id == "school-distance" for r in item.criterion_results)
        # evidence_id carries no criterion_id and no timestamp: same fact -> same id.
        refs = lambda r, cid: {i.listing.listing_id: next(c for c in i.criterion_results if c.criterion_id == cid).evidence_refs
                               for i in r.recommendations}
        assert refs(first, "school-distance") == refs(second, "school-within-1500")
        hard_evidence = [e for e in second.evidence if e.criterion_id == "school-within-1500"]
        assert hard_evidence and all(e.observed_at == hard_evidence[0].observed_at for e in hard_evidence)
    finally:
        service.close()


def test_criteria_sharing_a_canonical_key_each_get_their_own_evidence(tmp_path):
    """A hard and a soft criterion with the same key share R4 evidence IDs; the response carries
    one Evidence per (criterion, evidence_id), so each reference resolves to its own criterion."""
    service = make(tmp_path)
    try:
        base = {"field": "distance", "operator": "lte", "value": 100000, "unit": "m",
                "destination_id": "fixture-school", "distance_type": "straight_line"}
        criteria = [dict(base, criterion_id="hard-school", strength="hard"),
                    dict(base, criterion_id="near-school", strength="soft", utility="proximity")]
        response = service.recommend(body(criteria))
        assert response.status == "success"
        pairs = {(e.evidence_id, e.criterion_id) for e in response.evidence}
        assert len(pairs) == len(response.evidence)  # no duplicate (id, criterion) entries
        for item in response.recommendations:
            results = {r.criterion_id: r for r in item.criterion_results}
            assert results["hard-school"].evidence_refs == results["near-school"].evidence_refs  # shared R4 id
            for result in item.criterion_results:
                for ref in result.evidence_refs:
                    assert (ref, result.criterion_id) in pairs
        assert {e.criterion_id for e in response.evidence} == {"hard-school", "near-school"}
        replay_gate(response, service.store.current("p3"))
    finally:
        service.close()


def test_destination_name_variants_share_a_pool_only_when_they_resolve_alike(tmp_path):
    """P4 (rule changed from the P3 verbatim key): the geo target in pool_key is the NATIVELY
    resolved entity, so spellings that normalise to the same entity share one pool, while a
    name that resolves differently (here: not at all) never does. Either way the outcome equals
    an uncached run of the second name."""
    def school(name):
        return [dict(SCHOOL, destination_id=None, destination_name=name, value=100000)]

    def uncached(name, index, conversation):
        # Separate directory per case (macOS file names are case-insensitive). P5: the same
        # conversation and request IDs as the cached run, so select-v1 draws with the same seeds
        # (was: "u"/"u"; the selection seed depends on conversation_id and request_id, R3).
        other = make(tmp_path / f"uncached-{index}", settings(**{"cache.enabled": False}))
        try:
            result = other.recommend(body(school(name), "n-2", conversation))
            assert result.status != "error", (name, result.error)
            return result
        finally:
            other.close()

    cases = ((["fixture school", "Fixture School"], "reused"), (["Fixture School", "fixture school"], "reused"),
             (["Fixture School", " Fixture School "], "reused"), (["Fixture School", "Fixture Schools"], "built"))
    for index, (names, expected_cache) in enumerate(cases):
        conversation = f"names-{index}"
        service = make(tmp_path, name=f"{conversation}.sqlite")
        try:
            first = service.recommend(body(school(names[0]), "n-1", conversation))
            second = service.recommend(body(school(names[1]), "n-2", conversation, version=2,
                                            previous=first.state.state_id))
            assert second.status != "error", second.error
            assert second.pool.cache == expected_cache, names
            assert (second.pool.pool_key == first.pool.pool_key) == (expected_cache == "reused"), names
            expected = uncached(names[1], index, conversation)
            assert (second.status, second.pool.size, [r.listing.listing_id for r in second.recommendations]) == (
                expected.status, expected.pool.size, [r.listing.listing_id for r in expected.recommendations])
            [resolution] = second.resolution
            assert resolution.status == ("resolved" if expected_cache == "reused" else "unresolved"), names
        finally:
            service.close()
    # An identical name in the same conversation is still a hit.
    service = make(tmp_path, name="same-name.sqlite")
    try:
        first = service.recommend(body(school("Fixture School"), "s-1", "same"))
        again = service.recommend(body(school("Fixture School"), "s-2", "same", previous=first.state.state_id))
        assert (first.status, again.pool.cache) == ("success", "reused")
    finally:
        service.close()


def test_pool_version_does_not_depend_on_how_the_entity_was_resolved(tmp_path):
    """P4 review: an ID, a name and an alias that resolve to one entity share pool_key AND
    pool_version, in separate (uncached) builds: the hashed content keeps only the resolved
    status/entity, never the per-request resolution method or reason."""
    geo = {"criterion_id": "geo", "field": "distance", "operator": "lte", "value": 20000, "unit": "m",
           "strength": "hard", "distance_type": "straight_line"}
    pools = {}
    for index, target in enumerate(({"destination_name": "Clementi MRT"}, {"destination_id": "LTA:MRT:CLEMENTI"},
                                    {"destination_name": "金文泰地铁站"},
                                    {"destination_id": "LTA:MRT:CLEMENTI", "destination_name": "Clementi MRT"})):
        pool_cache_module.MEMORY.clear()
        service = make(tmp_path, name=f"resolved-{index}.sqlite")
        try:
            response = service.recommend(body([dict(geo, **target)], f"r-{index}", f"resolved-{index}"))
            assert response.status == "success" and response.pool.cache == "built", response.error
            pools[index] = (response.pool.pool_key, response.pool.pool_version, response.resolution[0].method)
        finally:
            service.close()
    assert len({key for key, _, _ in pools.values()}) == 1 and len({v for _, v, _ in pools.values()}) == 1
    assert [method for _, _, method in pools.values()] == ["name", "id", "name", "id+name"]


# --- canonical keys -----------------------------------------------------------------------
def test_canonical_criterion_keys_and_pool_key():
    config = settings()
    c = lambda **d: Criterion.model_validate(d)
    a = c(criterion_id="a", field="property_type", operator="in", value=["Condo", "hdb", "condo"], strength="hard")
    b = c(criterion_id="b", field="property_type", operator="in", value=["HDB", "CONDO"], strength="hard", weight=5)
    assert criterion_key(a) == criterion_key(b) and criterion_key(a)["value"] == ["condo", "hdb"]
    assert criterion_key(c(criterion_id="p", field="price", operator="lte", value=3000, unit="SGD/month", strength="hard")) \
        == criterion_key(c(criterion_id="q", field="price", operator="lte", value=3000.0, unit="SGD/month", strength="hard"))
    first = body([BUDGET, BEDROOMS, DISTRICT])
    reordered = body([dict(BEDROOMS, criterion_id="x"), dict(BUDGET, criterion_id="y")])
    assert pool_key(first, config, "geo") == pool_key(reordered, config, "geo")  # ids, order, soft ignored
    assert pool_key(first, config, "geo") != pool_key(first, config, "geo-2")  # catalog version
    other_geo = settings(**{"geography.station_distance_method": "exit_centroid"})
    assert pool_key(first, config, "geo") != pool_key(first, other_geo, "geo")  # geography config digest
    duplicate = body([BUDGET, dict(BUDGET, criterion_id="budget-2")])
    assert pool_key(duplicate, config, "geo") == pool_key(body([BUDGET]), config, "geo")
    resolved = {"school-distance": ("OSM:node:1", "osm_place")}
    geo = body([SCHOOL])
    assert criterion_key(geo.requirements.criteria[0], resolved)["entity"] == {"id": "OSM:node:1", "kind": "osm_place"}
    assert pool_key(geo, config, "geo", resolved) != pool_key(geo, config, "geo")


def test_pool_format_version_is_part_of_the_key(monkeypatch):
    request = body([BUDGET])
    before = pool_key(request, settings(), "geo")
    monkeypatch.setattr(pool_builder, "POOL_FORMAT_VERSION", "pool-v999")
    assert pool_key(request, settings(), "geo") != before


# --- TTL, retention, memory ---------------------------------------------------------------
def test_ttl_zero_means_no_expiry_and_positive_ttl_expires(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(pool_cache_module.time, "time", lambda: now[0])
    monkeypatch.setattr(pool_builder.time, "time", lambda: now[0])
    service = make(tmp_path)
    try:
        first = service.recommend(body([BUDGET]))
        now[0] += 10 ** 7
        assert service.recommend(body([BUDGET], "p3-2", previous=first.state.state_id)).pool.cache == "reused"
    finally:
        service.close()
    other = tmp_path / "ttl"
    other.mkdir()
    service = make(other, settings(**{"cache.ttl_seconds": 60}))
    try:
        first = service.recommend(body([BUDGET]))
        now[0] += 59
        second = service.recommend(body([BUDGET], "p3-2", previous=first.state.state_id))
        assert second.pool.cache == "reused"
        now[0] += 2  # 61 s after the build (a hit does not extend the TTL)
        third = service.recommend(body([BUDGET], "p3-3", previous=second.state.state_id))
        assert third.pool.cache == "built"
        assert service.store.trace("p3", "p3-3")["pool_ref"]["outcome"] == "expired"
        fourth = service.recommend(body([BUDGET], "p3-4", previous=third.state.state_id))
        assert fourth.pool.cache == "reused"
    finally:
        service.close()


def test_retention_index_bound_and_evicted_pool_reference(tmp_path):
    service = make(tmp_path)  # committed values: index 2, retention 4
    try:
        budgets = [2000, 2200, 2400, 2600, 2800, 3000]
        responses = chain(service, [[dict(BUDGET, value=v)] for v in budgets])
        cache = PoolCache(service.store, service.config)
        assert len(cache.index_entries("p3")) == 2
        retained = cache.retained_versions("p3")
        assert len(retained) == 4
        assert retained == [r.pool.pool_version for r in reversed(responses[-4:])]
        with pytest.raises(RecommendationError) as evicted:
            service.store.resolve_reference("p3", responses[0].state.candidate_pool_ref)
        assert "evicted" in evicted.value.detail.message
        assert service.store.resolve_reference("p3", responses[-1].state.candidate_pool_ref)["rows"]
        # Replays of runs whose pool was evicted still pass replay_gate (compact snapshot).
        replay = service.recommend(body([dict(BUDGET, value=2000)], "p3-0", version=1))
        assert replay == responses[0]
        # The oldest pool is no longer indexed: same hard criteria rebuild.
        again = service.recommend(body([dict(BUDGET, value=2000)], "p3-again", version=7,
                                       previous=responses[-1].state.state_id))
        assert again.pool.cache == "built"
    finally:
        service.close()


def test_index_keeps_the_newest_built_entries(tmp_path):
    """Spec section 5: the index keeps the max_entries_per_conversation NEWEST (built) entries; a
    hit refreshes content retention only, never the index position."""
    service = make(tmp_path)  # committed max_entries_per_conversation: 2
    try:
        budgets = [2000, 2200, 2000, 2400, 2200, 2000]
        responses = chain(service, [[dict(BUDGET, value=v)] for v in budgets])
        # 2000 built, 2200 built, 2000 reused (no index refresh), 2400 built -> index {2200, 2400};
        # so 2200 is reused and 2000 (evicted although recently used) is rebuilt.
        assert [r.pool.cache for r in responses] == ["built", "built", "reused", "built", "reused", "built"]
        cache = PoolCache(service.store, service.config)
        assert [e["pool_version"] for e in cache.index_entries("p3")] == [
            responses[5].pool.pool_version, responses[3].pool.pool_version]
        # Content retention is by use: the reused 2200 content is retained with the newer ones.
        assert responses[4].pool.pool_version in cache.retained_versions("p3")
    finally:
        service.close()


def test_pool_cache_keeps_the_run_transaction_and_its_write_lock(tmp_path):
    """PoolCache runs no DDL: executescript() would COMMIT the run's BEGIN IMMEDIATE transaction
    and release the cross-process write lock while the run is still in flight."""
    import sqlite3

    store = StateStore(tmp_path / "lock.sqlite")
    try:
        with store.transaction():
            assert store.db.in_transaction
            PoolCache(store, settings())
            assert store.db.in_transaction
    finally:
        store.close()
    service = make(tmp_path)
    blocked = []

    class Interleaving(ScriptedController):
        def propose(self, dispatcher, context, trace):
            # Another process tries to write the same state file while this run is in flight.
            other = sqlite3.connect(tmp_path / "state.sqlite", timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    other.execute("BEGIN IMMEDIATE")
                blocked.append(True)
            finally:
                other.close()
            return super().propose(dispatcher, context, trace)

    service.controller = Interleaving()
    try:
        assert service.recommend(body([BUDGET])).status == "success" and blocked
    finally:
        service.close()


def test_memory_lru_is_bounded(tmp_path):
    memory = PoolMemory()
    for i in range(10):
        memory.put(("c", str(i)), i, 4)
    assert len(memory) == 4 and list(memory.entries) == [("c", str(i)) for i in range(6, 10)]
    memory.get(("c", "6"))
    memory.put(("c", "x"), 0, 4)
    assert ("c", "6") in memory.entries and ("c", "7") not in memory.entries
    service = make(tmp_path, settings(**{"cache.memory_max_entries": 2}))
    try:
        chain(service, [[dict(BUDGET, value=v)] for v in (2000, 2200, 2400)])
        assert len(pool_cache_module.MEMORY) == 2
    finally:
        service.close()


def test_memory_is_filled_only_after_commit(tmp_path):
    service = make(tmp_path)
    try:
        bad = body([BUDGET])
        service.controller = type("Broken", (ScriptedController,), {
            "propose": lambda self, d, c, t: (ScriptedController.propose(self, d, c, t), Proposal(listing_ids=["nope"]))[1]
        })()
        response = service.recommend(bad)
        assert response.error.code == "INVALID_PROPOSAL"
        assert len(pool_cache_module.MEMORY) == 0
        cache = PoolCache(service.store, service.config)
        assert cache.retained_versions("p3") == [] and cache.index_entries("p3") == []
        trace = service.store.trace("p3")
        assert trace["pool_ref"]["persisted"] is False
        with pytest.raises(RecommendationError) as missing:
            service.store.resolve_reference("p3", f"{trace['trace_id']}/pool")
        assert "not persisted" in missing.value.detail.message
    finally:
        service.close()


# --- data_version failure -----------------------------------------------------------------
def test_fingerprint_failure_bypasses_the_cache_with_a_warning(tmp_path):
    service = make(tmp_path)

    def broken(rows):
        raise RuntimeError("fingerprint query failed")

    try:
        first = service.recommend(body([BUDGET, BEDROOMS]))
        original = service.listing._fingerprint
        service.listing._fingerprint = broken
        bypassed = service.recommend(body([BUDGET, BEDROOMS], "p3-2", previous=first.state.state_id))
        assert bypassed.status == "success"
        assert bypassed.pool.cache == "bypassed" and bypassed.pool.data_version == "unavailable"
        assert bypassed.pool.pool_version == first.pool.pool_version  # rebuilt, never served from the cache
        assert any("data version unavailable" in w for w in bypassed.warnings)
        assert service.store.trace("p3", "p3-2")["pool_stats"]["rows_transferred"] == 12
        cache = PoolCache(service.store, service.config)
        assert all(e["data_version"] != "unavailable" for e in cache.index_entries("p3"))
        service.listing._fingerprint = original
        recovered = service.recommend(body([BUDGET, BEDROOMS], "p3-3", previous=bypassed.state.state_id))
        assert recovered.pool.cache == "reused"
    finally:
        service.close()


def test_catalog_version_failure_bypasses_the_cache(tmp_path, monkeypatch):
    from recommendation.state import data_version

    service = make(tmp_path)
    try:
        def fail(geographic):
            raise data_version.VersionUnavailable("no catalog")

        monkeypatch.setattr(data_version, "catalog_version", fail)
        response = service.recommend(body([BUDGET]))
        assert response.pool.cache == "bypassed" and response.status == "success"
        assert any("catalog version unavailable" in w for w in response.warnings)
    finally:
        service.close()


def test_cache_disabled_builds_every_time(tmp_path):
    service = make(tmp_path, settings(**{"cache.enabled": False}))
    try:
        first, second = chain(service, [[BUDGET], [BUDGET]])
        assert (first.pool.cache, second.pool.cache) == ("bypassed", "bypassed")
        assert not any("unavailable" in w for w in second.warnings)
    finally:
        service.close()


# --- final gate ---------------------------------------------------------------------------
def test_final_gate_detects_data_change_and_rebuilds_once(tmp_path):
    service = make(tmp_path)
    original = service.listing.lookup_versioned
    state = {"calls": 0}

    def change_once(ids):
        state["calls"] += 1
        if state["calls"] == 1:  # data changes between pool build and the gate
            next(r for r in service.listing.rows if r.listing_id == "fixture-11").title = "changed"
        return original(ids)

    service.listing.lookup_versioned = change_once
    try:
        response = service.recommend(body([BUDGET, BEDROOMS]))
        assert response.status == "success" and response.pool.cache == "built"
        gates = [s for s in service.store.trace("p3")["steps"] if s["kind"] == "final_gate"]
        assert [g["passed"] for g in gates] == [False, True]
        assert gates[0]["error"]["code"] == "STALE_EVIDENCE" and gates[0]["error"]["retryable"]
        assert gates[1]["data_version"] == response.pool.data_version != gates[0]["pool"]["data_version"]
    finally:
        service.close()


def test_final_gate_rebuilds_at_most_once(tmp_path):
    service = make(tmp_path)
    original = service.listing.lookup_versioned
    counter = [0]

    def always_change(ids):
        counter[0] += 1
        next(r for r in service.listing.rows if r.listing_id == "fixture-11").title = f"changed-{counter[0]}"
        return original(ids)

    service.listing.lookup_versioned = always_change
    try:
        response = service.recommend(body([BUDGET, BEDROOMS]))
        assert response.error.code == "STALE_EVIDENCE" and counter[0] == 2
        assert service.store.current("p3") is None
    finally:
        service.close()


def test_data_change_discards_the_pool_for_a_controller_that_does_not_retrieve(tmp_path):
    """After a data change the service discards the stale pool: a controller that re-proposes the
    old selection without rebuilding the pool (resolve_entities + build_pool) cannot pass the gate,
    and ranking without a rebuild is a STALE_REFERENCE tool error; nothing is committed from the stale pool."""
    service = make(tmp_path)
    original = service.listing.lookup_versioned
    state = {"calls": 0}

    def change_once(ids):
        state["calls"] += 1
        if state["calls"] == 1:
            next(r for r in service.listing.rows if r.listing_id == "fixture-11").title = "changed"
        return original(ids)

    class Lazy(ScriptedController):
        """Runs the tools once, then only re-ranks / re-proposes the recorded ids (no retrieve)."""
        first = None

        def propose(self, dispatcher, context, trace):
            if self.first is None:
                self.first = super().propose(dispatcher, context, trace)
                return self.first
            result = dispatcher.call("rank_candidates", {"context_ref": context["context_ref"]})
            assert result.status == "error" and result.error.code == "STALE_REFERENCE"
            return self.first

    service.listing.lookup_versioned = change_once
    service.controller = Lazy()
    try:
        response = service.recommend(body([BUDGET, BEDROOMS]))
        assert response.status == "error" and response.error.code == "INVALID_PROPOSAL"
        assert service.store.current("p3") is None
        gates = [s for s in service.store.trace("p3")["steps"] if s["kind"] == "final_gate"]
        assert gates[0]["error"]["code"] == "STALE_EVIDENCE" and not any(g["passed"] for g in gates)
        assert state["calls"] == 1  # later proposals are rejected before any data is re-read
    finally:
        service.close()


def test_final_gate_requires_exact_selection_and_unchanged_selected_rows(tmp_path):
    service = make(tmp_path)
    try:
        class Reordered(ScriptedController):
            def propose(self, dispatcher, context, trace):
                proposal = super().propose(dispatcher, context, trace)
                return Proposal(listing_ids=list(reversed(proposal.listing_ids)))

        service.controller = Reordered()
        assert service.recommend(body([BUDGET, BEDROOMS])).error.code == "INVALID_PROPOSAL"
    finally:
        service.close()


def test_sql_by_id_lookup_and_fingerprint_share_one_repeatable_read(monkeypatch):
    calls = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

        def one(self):
            return self.rows[0]

    class Connection:
        def __enter__(self):
            calls.append("CONNECT")
            return self

        def __exit__(self, *args):
            calls.append("CLOSE")

        def execute(self, query, params=None):
            sql = str(query)
            calls.append((sql, params))
            if "ANY(" in sql:
                return Result([{"listing_id": 7, "listing_type": "RENT", "price": 2000}])
            if "string_agg" in sql:
                return Result([{"row_count": 3, "max_updated_epoch": 1.5, "rows_md5": "abc"}])
            return Result([])

    from types import SimpleNamespace
    repo = listing_repository.SQLListingRepository(SimpleNamespace(connect=lambda: Connection()))
    rows, fingerprint = repo.lookup_versioned(["7", "x-not-numeric", "7"])
    assert [r.listing_id for r in rows] == ["7"] and fingerprint["rows_md5"] == "abc"
    assert calls[0] == "CONNECT" and calls[-1] == "CLOSE" and calls.count("CONNECT") == 1
    statements = [c[0] for c in calls[1:-1]]
    assert statements[:3] == ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '10000ms'",
                              "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"]
    by_id = next(c for c in calls[1:-1] if "ANY(" in c[0])
    assert "listing_id = ANY(CAST(:ids AS BIGINT[]))" in by_id[0] and by_id[1]["ids"] == [7]
    assert "LIMIT" not in by_id[0] and "listing_id>:after" not in by_id[0]  # never a full retrieve
    assert "string_agg" in statements[-1]


def test_fingerprint_sql_is_alias_free_and_covers_every_mapped_column():
    sql = listing_repository.FINGERPRINT_SQL
    row = sql[sql.index("row(") + 4: sql.index(")::text")]
    assert " AS " not in row.upper()
    for column in listing_repository.COLUMNS:
        assert column in row
    assert "extract(epoch from updated_at)" in row and "raw_json ->> 'dataset_scope'" in row
    assert "ORDER BY listing_id" in sql and "count(*)" in sql and "extract(epoch from max(updated_at))" in sql
    assert "WHERE listing_type=:kind" in sql
    from recommendation.models.listing_summary import ListingSummary
    mapped = set(ListingSummary.model_fields) - {"source", "observed_at", "normalization_warnings"}
    assert mapped <= set(listing_repository.COLUMNS)


def test_fixture_fingerprint_is_deterministic():
    one, _ = repositories("fixture")
    two, _ = repositories("fixture")
    assert one.fingerprint() == two.fingerprint()
    two.rows[0].title = "x"
    assert one.fingerprint() != two.fingerprint()


# --- compact snapshot and replay ------------------------------------------------------------
def test_compact_snapshot_and_replay_gate(tmp_path):
    service = make(tmp_path)
    try:
        response = service.recommend(body([BUDGET, BEDROOMS, DISTRICT]))
        snapshot = service.store.current("p3")
        assert set(snapshot["pool"]) == {"pool_key", "pool_version", "data_version", "cache"}
        def keys(value):
            if isinstance(value, dict):
                return set(value) | {k for v in value.values() for k in keys(v)}
            return {k for v in value for k in keys(v)} if isinstance(value, list) else set()

        run_data = {k: v for k, v in snapshot.items() if k != "configuration"}  # settings have a "ranking" section
        assert not keys(run_data) & {"rows", "evidence", "scores", "ranking", "validation", "pool_summary"}
        assert len(json.dumps(run_data)) < 8000
        selection = snapshot["selection_input"]
        assert selection["algorithm_version"] == "select-v1" and selection["K"] == len(response.recommendations)
        assert [h[0] for h in selection["head"]] == [r.listing.listing_id for r in response.recommendations]
        replay_gate(response, snapshot)
        assert service.recommend(body([BUDGET, BEDROOMS, DISTRICT])) == response  # stored response
        trace = service.store.trace("p3")
        assert trace["steps"][0]["mode"] == "immutable_replay" and trace["pool_ref"]["pool_version"] == response.pool.pool_version
        data = response.model_dump(mode="json")
        tampered = json.loads(json.dumps(data))
        tampered["recommendations"][0]["scores"]["combined_score"] = 0.123
        with pytest.raises(RecommendationError):
            replay_gate(Response.model_validate(tampered), snapshot)
        swapped = json.loads(json.dumps(data))
        swapped["recommendations"][0]["listing"], swapped["recommendations"][1]["listing"] = (
            swapped["recommendations"][1]["listing"], swapped["recommendations"][0]["listing"])
        with pytest.raises(RecommendationError):
            replay_gate(Response.model_validate(swapped), snapshot)
        bad_head = json.loads(json.dumps(snapshot))
        bad_head["selection_input"]["head"].reverse()
        with pytest.raises(RecommendationError):
            replay_gate(response, bad_head)
        other_pool = json.loads(json.dumps(snapshot))
        other_pool["pool"]["pool_version"] = "0" * 64
        with pytest.raises(RecommendationError):
            replay_gate(response, other_pool)
        unknown = json.loads(json.dumps(snapshot))
        unknown["selection_input"]["algorithm_version"] = "nope"
        with pytest.raises(RecommendationError):
            replay_gate(response, unknown)
    finally:
        service.close()


def test_replay_gate_checks_geographic_evidence_of_items(tmp_path):
    service = make(tmp_path)
    try:
        response = service.recommend(body([SCHOOL, NEAR_SCHOOL]))
        snapshot = service.store.current("p3")
        replay_gate(response, snapshot)
        data = response.model_dump(mode="json")
        far = json.loads(json.dumps(data))
        ref = next(c for c in far["recommendations"][0]["criterion_results"] if c["criterion_id"] == "school-distance")["evidence_refs"][0]
        for e in far["evidence"]:
            if e["evidence_id"] == ref:
                e["value"] = 99999.0  # stored evidence no longer satisfies the hard criterion
        with pytest.raises(RecommendationError):
            replay_gate(Response.model_validate(far), snapshot)
    finally:
        service.close()


def test_p2_era_full_snapshots_still_replay(tmp_path):
    """A committed v2 run whose snapshot embeds the full pool (P2 format) still replays."""
    service = make(tmp_path)
    try:
        response = service.recommend(body([BUDGET, BEDROOMS]))
        snapshot = service.store.current("p3")
        scores = {r.listing.listing_id: r.scores.model_dump() for r in response.recommendations}
        legacy = {k: v for k, v in snapshot.items() if k != "selection_input"}
        legacy["pool"] = {
            "rows": [r.listing.model_dump() for r in response.recommendations], "evidence": [],
            "ranking": [r.listing.listing_id for r in response.recommendations], "scores": scores,
        }
        replay_gate(response, legacy)
    finally:
        service.close()


def test_app_presentation_counts_from_pool_summary(tmp_path, monkeypatch):
    import recommendation.app as app_module

    monkeypatch.setenv("RECOMMENDATION_STATE_PATH", str(tmp_path / "app.sqlite"))
    envelope = app_module.execute(body([BUDGET, BEDROOMS, DISTRICT], "app-1", "app"), app_view=True)
    pool = envelope["response"]["pool"]
    assert envelope["presentation"]["counts"] == {
        "assessed": pool["source_count"], "eligible": pool["size"],
        "unverified": pool["excluded_hard_unknown"], "excluded": pool["excluded_hard_fail"],
    } == {"assessed": 12, "eligible": 9, "unverified": 1, "excluded": 2}
    state = envelope["response"]["state"]
    assert (state["source_count"], state["assessed_count"], state["prefilter_excluded"]) == (12, 9, 3)
    again = app_module.execute(body([BUDGET, BEDROOMS, DISTRICT], "app-2", "app"), app_view=True)
    assert again["response"]["pool"]["cache"] == "reused"
    assert again["presentation"]["counts"] == envelope["presentation"]["counts"]
