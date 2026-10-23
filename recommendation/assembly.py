"""Response 2.0 assembly for a completed run.

P3: the pool summary comes from the run's exploration pool (tools/pool_builder.py, built or
reused through the pool cache). P4: resolution comes from the run's native entity-resolution
stage (recommendation/geography/resolver.py). P5: selection, cold-start diagnostics and the
per-item selection reasons come from the run's select-v1 selection (ranking/selection.py).
"""

from recommendation.contracts.summary import PoolSummary
from .models.candidate import RecommendationItem
from .validation.rules import UNSUPPORTED

from .geography.resolver import describe
from .tools.pool_builder import hard_criteria
from .ranking.selection import ALGORITHM_VERSION

SELECTION_ALGORITHM = ALGORITHM_VERSION


def pool_summary(request, dispatcher):
    """PoolSummary of the run's pool: every source listing is eligible, hard fail or hard unknown."""
    pool = dispatcher.pool_result
    partition = pool.partition
    return PoolSummary(
        pool_key=pool.key,
        pool_version=pool.version,
        data_version=pool.data_version,
        cache=pool.cache,
        size=partition["size"],
        source_count=partition["source_count"],
        excluded_hard_fail=partition["excluded_hard_fail"],
        excluded_hard_unknown=partition["excluded_hard_unknown"],
        unknown_reasons=partition["unknown_reasons"],
    )


def resolution(request, dispatcher):
    """ResolutionResult per geographic criterion from the run's native resolution stage."""
    return dispatcher.resolution_results()


def criterion_problems(request, resolved):
    """R8: criterion-level problems that make an empty result needs_clarification."""
    by_id = {r.criterion_id: r for r in resolved}
    problems = []
    for c in hard_criteria(request):
        if c.field in UNSUPPORTED or (c.field == "distance" and c.distance_type != "straight_line"):
            problems.append(
                f"Hard criterion '{c.criterion_id}' ({c.field}) is not supported by the native recommender; revise or remove it."
            )
        elif c.criterion_id in by_id and by_id[c.criterion_id].status in ("unresolved", "ambiguous"):
            r = by_id[c.criterion_id]
            problems.append(f"Hard criterion '{c.criterion_id}' ({c.field}): destination {r.status} ({describe(r)}).")
    return problems


def selection_summary(dispatcher):
    """SelectionSummary of the run's select-v1 selection (mode, seed, tie group, exploration,
    cold-start diagnostics)."""
    return dispatcher.selection.summary


def items(request, dispatcher, ids):
    rows = {r.listing_id: r for r in dispatcher.pool}
    selection = dispatcher.selection
    built = []
    for rank, listing_id in enumerate(ids, 1):
        results = dispatcher.results[listing_id]
        built.append(
            RecommendationItem(
                listing=rows[listing_id].model_copy(deep=True),
                rank=rank,
                score_rank=dispatcher.scored.score_rank[listing_id],
                scores=dispatcher.scores[listing_id],
                criterion_results=results,
                evidence_refs=[ref for r in results for ref in r.evidence_refs],
                strengths=[r.criterion_id for r in results if r.outcome == "pass"],
                trade_offs=[r.criterion_id for r in results if r.outcome == "fail"],
                unknowns=[r.criterion_id for r in results if r.outcome in ("unknown", "unsupported")],
                selection_reason=selection.reasons[listing_id],
                selection_note=selection.notes[listing_id],
            )
        )
    return built


def returned_evidence(dispatcher, built):
    """Evidence referenced by returned items: one entry per (evidence_id, criterion_id) pair.

    R4 evidence IDs carry no criterion_id, so criteria sharing a canonical key share an ID; each
    referencing criterion still gets an Evidence carrying its own criterion_id.
    """
    used = {
        (ref, result.criterion_id)
        for item in built for result in item.criterion_results for ref in result.evidence_refs
    }
    seen, evidence = set(), []
    for e in dispatcher.evidence:
        pair = (e.evidence_id, e.criterion_id)
        if pair in used and pair not in seen:
            seen.add(pair)
            evidence.append(e)
    return evidence


def warnings(request, pool, extra=()):
    notes = [
        "Sample viewing durations are simulated observations.",
        "Posting time does not establish current availability.",
        *extra,
    ]
    if pool.excluded_hard_unknown:
        notes.append(
            f"{pool.excluded_hard_unknown} listings were excluded because hard-criterion data is unknown "
            "(see pool.unknown_reasons); they are not verified matches."
        )
    return notes
