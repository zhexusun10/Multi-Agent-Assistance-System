from ..validation.rules import evaluate, eligible
from ..tracing.trace import digest
from dev.sharedContract.recommendationSubagentContract.errors import RecommendationError


def final_gate(proposal, dispatcher, trace):
    dispatcher.check_deadline()
    ids = proposal.listing_ids
    request = dispatcher.request
    report = {
        "kind": "final_gate",
        "proposed_ids": ids,
        "passed": False,
        "requirements_version": request.requirements_version,
        "policy": "immutable-run snapshot, re-read listing and geographic facts before output",
    }
    trace["steps"].append(report)
    if dispatcher.ranked is None or ids != dispatcher.ranked[: request.result_limit]:
        raise RecommendationError(
            "INVALID_PROPOSAL", "Shortlist must be the current recorded ranking prefix"
        )
    if len(ids) != len(set(ids)) or len(ids) > request.result_limit:
        raise RecommendationError("INVALID_PROPOSAL", "Invalid shortlist IDs/count")
    # Re-read the entire scope: changes to excluded candidates can change ranking too.
    current = dispatcher.listing.retrieve()
    if digest([r.model_dump() for r in current]) != digest(
        [r.model_dump() for r in dispatcher.rows]
    ):
        raise RecommendationError(
            "STALE_EVIDENCE", "Listings changed during this run; retry", True
        )
    criteria = [
        c for c in request.requirements.criteria if c.field in ("distance", "commute")
    ]
    fresh = dispatcher.geographic.evidence(dispatcher.pool, criteria)
    if [e.snapshot_hash for e in fresh] != [
        e.snapshot_hash for e in dispatcher.evidence
    ]:
        raise RecommendationError(
            "STALE_EVIDENCE",
            "Geographic evidence changed or was not retrieved; retry",
            True,
        )
    rows = {r.listing_id: r for r in current}
    report["checks"] = {}
    for id in ids:
        row = rows.get(id)
        results = evaluate(row, request.requirements.criteria, fresh) if row else []
        report["checks"][id] = [r.model_dump() for r in results]
        if (
            row is None
            or row.listing_type != "RENT"
            or not eligible(results, request.requirements.criteria)
        ):
            raise RecommendationError(
                "INVALID_PROPOSAL", "Final hard constraints did not pass"
            )
    dispatcher.check_deadline()
    report["passed"] = True
    return ids


def replay_gate(response, snapshot):
    """Validate an immutable replay against its original canonical request and facts.

    Retrying an ID returns the historical result, never a claim of live freshness.
    New requests always use final_gate's live re-read policy.
    """
    from ..models.listing_summary import ListingSummary
    from dev.sharedContract.recommendationSubagentContract.request import (
        RecommendationRequest,
    )
    from dev.sharedContract.recommendationSubagentContract.evidence import Evidence

    request = RecommendationRequest.model_validate(snapshot["request"])
    pool = snapshot["pool"]
    rows = {r["listing_id"]: ListingSummary.model_validate(r) for r in pool["rows"]}
    evidence = [Evidence.model_validate(e) for e in pool["evidence"]]
    ids = [r.listing.listing_id for r in response.recommendations]
    if ids != (pool["ranking"] or [])[: request.result_limit]:
        raise RecommendationError(
            "INVALID_SNAPSHOT", "Stored response differs from canonical ranking"
        )
    for item in response.recommendations:
        row = rows.get(item.listing.listing_id)
        if (
            row is None
            or row != item.listing
            or row.listing_type != "RENT"
            or not eligible(
                evaluate(row, request.requirements.criteria, evidence),
                request.requirements.criteria,
            )
        ):
            raise RecommendationError(
                "INVALID_SNAPSHOT", "Stored response failed replay gate"
            )
        if item.scores.model_dump() != pool["scores"][row.listing_id]:
            raise RecommendationError(
                "INVALID_SNAPSHOT", "Stored scores differ from trusted ranking"
            )
