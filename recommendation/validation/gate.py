from ..validation.rules import evaluate, eligible
from ..tracing.trace import digest
from recommendation.contracts.errors import RecommendationError


class DataChanged(RecommendationError):
    """STALE_EVIDENCE because the listing/catalog data version moved (the service rebuilds once)."""


def final_gate(proposal, dispatcher, trace):
    """Check the proposal against fresh data before output (spec section 5, P3).

    The proposal must equal the dispatcher's selection exactly (select-v1: leaders then drawn
    items, in display order), never merely a prefix of the score order. The data version is queried
    again, in the same REPEATABLE READ transaction as a BY-ID lookup of the selected listings,
    and must equal the pool's. The re-read rows must equal the pool rows. Hard criteria are
    evaluated again for the selected rows only, with geographic evidence recomputed for those
    rows. A cache hit therefore never causes a full re-read.
    """
    from ..state import data_version as versions
    from ..tools.pool_builder import GEO_FIELDS, by_listing, normalize_evidence

    dispatcher.check_deadline()
    ids = proposal.listing_ids
    request = dispatcher.request
    pool = dispatcher.pool_result
    report = {
        "kind": "final_gate",
        "proposed_ids": ids,
        "passed": False,
        "requirements_version": request.requirements_version,
        "policy": "proposal == recorded selection (algorithm_version + display order); data_version re-query + by-id re-read of the selection; "
        "hard criteria and geographic evidence recomputed for the selected rows only",
    }
    if dispatcher.selection is not None:
        report["selection"] = {"algorithm_version": dispatcher.selection.summary.algorithm_version,
                               "mode": dispatcher.selection.summary.mode,
                               "selected_ids": list(dispatcher.selected)}
    trace["steps"].append(report)
    if dispatcher.selected is None or pool is None or ids != dispatcher.selected:
        raise RecommendationError(
            "INVALID_PROPOSAL", "Shortlist must be the current recorded selection"
        )
    if len(ids) != len(set(ids)) or len(ids) > request.result_limit:
        raise RecommendationError("INVALID_PROPOSAL", "Invalid shortlist IDs/count")
    if set(ids) - {r.listing_id for r in dispatcher.pool}:
        raise RecommendationError("INVALID_PROPOSAL", "Shortlist contains listings outside the pool")
    # The selection that will be stored must replay from its own selection_input (replay_gate).
    from .selection_replay import replay_select

    if replay_select(dispatcher.selection_input).ids != ids:
        raise RecommendationError("INVALID_PROPOSAL", "Recorded selection does not replay from its selection input")
    fresh_rows, fingerprint = dispatcher.listing.lookup_versioned(ids)
    report["rows_read"] = len(fresh_rows)
    report["pool"] = pool.reference()
    if pool.data_version != versions.UNAVAILABLE:
        try:
            geo_version = versions.catalog_version(dispatcher.geographic)
        except versions.VersionUnavailable:
            geo_version = None
        current = versions.data_version(fingerprint, geo_version)
        report["data_version"] = current
        if current != pool.data_version:
            raise DataChanged(
                "STALE_EVIDENCE",
                "Listing or catalog data version changed (or could not be verified) during this run; retry",
                True,
            )
    else:
        # Cache bypassed (no verified data version): fall back to a full re-read of the scope.
        current_rows = dispatcher.listing.retrieve()
        report["rows_read"] += len(current_rows)
        report["data_version"] = versions.UNAVAILABLE
        if digest([r.model_dump() for r in current_rows]) != pool.source_digest:
            raise DataChanged(
                "STALE_EVIDENCE", "Listings changed during this run; retry", True
            )
    rows = {r.listing_id: r for r in fresh_rows}
    pool_rows = {r.listing_id: r for r in dispatcher.pool}
    selected = [rows.get(i) for i in ids]
    if any(row is None or row != pool_rows.get(row.listing_id) for row in selected):
        raise RecommendationError(
            "STALE_EVIDENCE", "Selected listings changed or disappeared during this run; retry", True
        )
    criteria = [c for c in request.requirements.criteria if c.field in GEO_FIELDS]
    fresh = normalize_evidence(
        dispatcher.geographic.evidence(
            selected, criteria, resolutions=dispatcher.resolutions, settings=dispatcher.config
        )
        if criteria and selected
        else [],
        criteria,
        rows,
        dispatcher.resolved,
    )
    if criteria and selected:
        # The station exit each selected listing was measured to (spec R1c: provenance only),
        # bounded by result_limit x geographic criteria; the pool-wide exits stay in run memory.
        provenance = [{"criterion_id": r["criterion_id"], "method": r.get("method"), "nearest_exits": r["nearest_exits"]}
                      for r in getattr(dispatcher.geographic, "last_snapshot", None) or [] if r.get("nearest_exits")]
        if provenance:
            report["geographic_provenance"] = provenance
    wanted = set(ids)
    recorded = sorted(
        (e.criterion_id, e.listing_id, e.snapshot_hash)
        for e in dispatcher.evidence
        if e.listing_id in wanted
    )
    if sorted((e.criterion_id, e.listing_id, e.snapshot_hash) for e in fresh) != recorded:
        raise RecommendationError(
            "STALE_EVIDENCE",
            "Geographic evidence changed or was not retrieved; retry",
            True,
        )
    grouped = by_listing(fresh)
    report["checks"] = {}
    for row in selected:
        results = evaluate(row, request.requirements.criteria, grouped.get(row.listing_id, []))
        report["checks"][row.listing_id] = [r.model_dump() for r in results]
        if row.listing_type != "RENT" or not eligible(
            results, request.requirements.criteria
        ):
            raise RecommendationError(
                "INVALID_PROPOSAL", "Final hard constraints did not pass"
            )
    dispatcher.check_deadline()
    report["passed"] = True
    return ids


def replay_gate(response, snapshot):
    """Validate an immutable replay against its original stored request and selection input.

    Retrying an ID returns the historical result, never a claim of live freshness. New requests
    always use final_gate's live re-read policy. Compact snapshots (P3+) carry no pool rows:
    the selection is recomputed from ``selection_input`` alone (dispatch on its
    algorithm_version: select-v1, or ranked-prefix-v1 for older snapshots), item scores must
    equal the stored inputs, and every item must be eligible by its own stored criterion results and
    evidence. Snapshots that embed a full pool (P2-era v2 runs) keep the old check.
    """
    if "selection_input" not in snapshot:
        return _replay_full_snapshot(response, snapshot)
    from recommendation.contracts.request import RecommendationRequest
    from .selection_replay import replay_select

    request = RecommendationRequest.model_validate(snapshot["request"])
    criteria = request.requirements.criteria
    selection_input = snapshot["selection_input"]
    ids = [r.listing.listing_id for r in response.recommendations]
    replayed = replay_select(selection_input)
    if ids != replayed.ids:
        raise RecommendationError(
            "INVALID_SNAPSHOT", "Stored response differs from the replayed selection"
        )
    # select-v1 also defines the mode, the seed and every item's selection reason.
    if replayed.mode is not None and (
        response.selection is None
        or response.selection.mode != replayed.mode
        or response.selection.algorithm_version != selection_input["algorithm_version"]
        or response.selection.seed != selection_input["seeds"]["base"]
        or [r.selection_reason for r in response.recommendations] != [replayed.reasons[i] for i in ids]
    ):
        raise RecommendationError(
            "INVALID_SNAPSHOT", "Stored selection mode, seed or reasons differ from the replayed selection"
        )
    pool = snapshot["pool"]
    if response.pool is None or response.pool.pool_version != pool["pool_version"]:
        raise RecommendationError(
            "INVALID_SNAPSHOT", "Stored response does not belong to the stored pool version"
        )
    head = {h[0]: h for h in selection_input["head"]}
    evidence = {e.evidence_id: e for e in response.evidence}
    paired = {(e.evidence_id, e.criterion_id): e for e in response.evidence}
    for item in response.recommendations:
        listing_id, combined, price = head[item.listing.listing_id]
        if item.scores.combined_score != combined or item.listing.price != price:
            raise RecommendationError(
                "INVALID_SNAPSHOT", "Stored scores differ from the stored selection input"
            )
        # Each evidence reference resolves to evidence of THIS listing. Responses carry one
        # Evidence per (evidence_id, criterion_id); responses stored before that (one entry per
        # shared R4 id) resolve by id, and the evidence is attached to the referencing criterion.
        facts = []
        for result in item.criterion_results:
            for ref in result.evidence_refs:
                e = paired.get((ref, result.criterion_id)) or evidence.get(ref)
                if e is None or e.listing_id != item.listing.listing_id:
                    raise RecommendationError(
                        "INVALID_SNAPSHOT", "Stored evidence reference does not resolve"
                    )
                facts.append(e.model_copy(update={"criterion_id": result.criterion_id}))
        if (
            item.listing.listing_type != "RENT"
            or not eligible(item.criterion_results, criteria)
            or not eligible(evaluate(item.listing, criteria, facts), criteria)
        ):
            raise RecommendationError(
                "INVALID_SNAPSHOT", "Stored response failed replay gate"
            )


def _replay_full_snapshot(response, snapshot):
    """P2-era v2 snapshots embedded the full pool; their replays keep the original check."""
    from ..models.listing_summary import ListingSummary
    from recommendation.contracts.request import (
        RecommendationRequest,
    )
    from recommendation.contracts.evidence import Evidence

    request = RecommendationRequest.model_validate(snapshot["request"])
    # Effective limit of the original run (request.result_limit None -> settings default then).
    limit = snapshot.get("result_limit") or request.result_limit
    pool = snapshot["pool"]
    rows = {r["listing_id"]: ListingSummary.model_validate(r) for r in pool["rows"]}
    evidence = [Evidence.model_validate(e) for e in pool["evidence"]]
    ids = [r.listing.listing_id for r in response.recommendations]
    if ids != (pool["ranking"] or [])[:limit]:
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
