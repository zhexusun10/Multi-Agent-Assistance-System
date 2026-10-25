"""Stage records of the pipeline tools (slim trace, tracing/slim.py; spec section 9, P6).

``tool_stages`` turns one pipeline tool call of the dispatcher into its stage record(s):
resolve_entities -> entity_resolution, build_pool -> pool_build_or_load,
get_geographic_evidence -> geographic_evidence, rank_candidates -> scoring,
cold_start_diagnostics, price_sampling, final_selection. Every record holds counts, key
parameters, versions, a preview of at most trace.preview_rows rows and unknown summaries;
nothing here is proportional to the pool size. A failed tool call gives an error record for
the stage that failed (for rank_candidates: the first sub-stage that did not finish).
"""

from collections import Counter

from . import slim


def _criteria(request):
    return request.requirements.criteria


def entity_resolution(d):
    from ..tools.pool_builder import GEO_FIELDS

    view = d.resolution_view()
    counts = Counter(r["status"] for r in view)
    geo = [c for c in _criteria(d.request) if c.field in GEO_FIELDS]
    problems = [r["criterion_id"] for r in view if r["status"] in ("ambiguous", "unresolved")]
    return {
        "inputs": {"geographic_criteria": len(geo), "bindings": len(d.bindings)},
        "outputs": {status: counts.get(status, 0)
                    for status in ("resolved", "ambiguous", "unresolved", "not_applicable")},
        "params": {"max_candidates": d.config.entity_resolution.max_candidates,
                   "max_search_attempts": d.config.entity_resolution.max_search_attempts},
        "preview": [{k: r.get(k) for k in ("criterion_id", "field", "status", "entity_id", "entity_kind",
                                           "method", "reason", "strength")}
                    | {"candidates": len(r.get("candidates") or [])} for r in view],
        "unknown": {"unresolved_or_ambiguous": problems} if problems else {},
    }


def pool_build_or_load(d):
    from ..tools.pool_builder import hard_criteria

    pool = d.pool_result
    partition = pool.partition
    cache = d.config.cache
    return {
        "pool": pool,
        "inputs": {"hard_criteria": len(hard_criteria(d.request)), "source_count": partition["source_count"],
                   "rows_transferred": pool.stats.get("rows_transferred")},
        "outputs": {"size": partition["size"], "excluded_hard_fail": partition["excluded_hard_fail"],
                    "excluded_hard_unknown": partition["excluded_hard_unknown"], "cache": pool.cache,
                    "outcome": pool.outcome, "persisted": pool.persisted},
        "params": {"cache_enabled": cache.enabled, "ttl_seconds": cache.ttl_seconds,
                   "pool_retention_per_conversation": cache.pool_retention_per_conversation,
                   "timings_ms": {k: v for k, v in pool.stats.items() if k.endswith("_ms")}},
        "preview": [r.listing_id for r in d.pool],
        "unknown": {"excluded_hard_unknown": partition["excluded_hard_unknown"],
                    "reasons": partition["unknown_reasons"], "warnings": list(pool.warnings)},
    }


def geographic_evidence(d):
    from ..tools.pool_builder import GEO_FIELDS

    from ..tools.dispatcher import compact_record

    soft = [c for c in _criteria(d.request) if c.field in GEO_FIELDS and c.strength == "soft"]
    records = [compact_record(r) for r in d.geo_records]
    unknown = {r["criterion_id"]: r["unknown"] for r in records if r.get("unknown")}
    return {
        "pool": d.pool_result,
        "inputs": {"soft_geographic_criteria": len(soft), "pool_rows": len(d.pool)},
        "outputs": {"evidence": len(d.evidence), "known": sum(r.get("known") or 0 for r in records)},
        "params": {"station_distance_method": d.config.geography.station_distance_method},
        "preview": records,
        "unknown": {"unknown_by_criterion": unknown} if unknown else {},
    }


def scoring(d):
    from ..ranking.scoring_v2 import scoring_identifier

    s = d.scored
    f = s.fused
    n = len(s.ids)
    preview = [{"listing_id": k, "score_rank": s.score_rank[k],
                "combined_score": float(f.combined[s.index[k]])} for k in s.ranked[:d.config.trace.preview_rows]]
    low = int((f.soft_coverage < 1).sum()) if s.soft_criteria and n else 0
    return {
        "pool": d.pool_result,
        "inputs": {"pool_rows": len(d.pool), "criteria": len(_criteria(d.request)),
                   "soft_criteria": len(s.soft_criteria),
                   "references": {b: len(s.references[b]["ids"]) for b in s.references}},
        "outputs": {"scored": n, "ineligible": len(d.pool) - n, "breakdowns_built": len(d.scores),
                    "pairs_available": int(f.pair_available.sum()) if n else 0,
                    "behavior_available": int(f.behavior_available.sum()) if n else 0},
        "params": {"scoring_version": scoring_identifier(d.config),
                   "weights": s.weights.as_dict(s.criterion_ids, s.features),
                   "suppressed_features": list(s.suppressed), "dropped_features": dict(s.dropped)},
        "preview": preview,
        "preview_total": len(s.ranked),  # the preview is the head of the full score order
        "unknown": {"soft_coverage_below_1": low} if low else {},
    }


def cold_start_diagnostics(d):
    cold = d.selection.summary.cold_start
    record = d.selection.record["cold_start"]
    return {
        "status": "ok" if cold.evaluated else "skipped",
        "note": None if cold.evaluated else cold.reason,
        "pool": d.pool_result,
        "inputs": {"pool_size": len(d.ranked), "result_limit": d.request.result_limit},
        "outputs": {k: v for k, v in cold.model_dump(mode="json").items()
                    if k not in ("detector", "detector_version", "candidate_window", "threshold")},
        "params": {"enabled": d.config.cold_start.enabled, "detector": cold.detector,
                   "detector_version": cold.detector_version, "candidate_window": cold.candidate_window,
                   "min_mean_pairwise_difference": cold.threshold,
                   "request_cold_start": record.get("request_cold_start"),
                   "request_cold_start_used": record.get("request_cold_start_used")},
    }


def price_sampling(d):
    summary = d.selection.summary
    explored = summary.exploration
    sampling = d.config.cold_start.price_sampling
    params = {"exploration_window": sampling.exploration_window, "intermediate_size": sampling.intermediate_size,
              "quantile_bins": sampling.quantile_bins, "bin_edges": d.selection_input["bin_edges"],
              "bands_from": "exploration window prices (leaders included)"}
    if explored is None:
        return {"status": "skipped", "note": f"selection mode {summary.mode} (price sampling only in cold start)",
                "pool": d.pool_result, "params": params}
    bins = [b.model_dump(mode="json") for b in explored.bins]
    unknown = [b for b in bins if b["lower"] is None]
    return {
        "pool": d.pool_result,
        "inputs": {"window": explored.window_size, "sampling_candidates": explored.sampling_candidates},
        "outputs": {"intermediate": len(explored.intermediate_ids), "intermediate_leaders": explored.intermediate_leaders,
                    "intermediate_sampled": explored.intermediate_sampled, "returned": len(explored.final_ids),
                    "returned_sampled": explored.final_sampled, "bins": len(bins)},
        "params": params,
        "preview": bins,
        "unknown": {"unknown_price_candidates": unknown[0]["count"]} if unknown else {},
    }


def final_selection(d):
    summary = d.selection.summary
    seeds = d.selection_input["seeds"]
    return {
        "pool": d.pool_result,
        "inputs": {"pool_size": len(d.ranked), "result_limit": summary.result_limit},
        "outputs": {"mode": summary.mode, "selected": len(d.selected), "leaders": summary.leaders,
                    "tie_group": summary.tie_group.model_dump() if summary.tie_group else None},
        "params": {"algorithm_version": summary.algorithm_version,
                   "epsilon": d.config.selection.tie_max_score_span,
                   "random_policy": d.config.selection.random_policy, "seed": summary.seed,
                   "seed_override": seeds["override"]},
        "preview": [{"listing_id": i, "rank": n, "score_rank": d.scored.score_rank[i],
                     "selection_reason": d.selection.reasons[i]} for n, i in enumerate(d.selected, 1)],
    }


BUILDERS = {
    "entity_resolution": entity_resolution,
    "pool_build_or_load": pool_build_or_load,
    "geographic_evidence": geographic_evidence,
    "scoring": scoring,
    "cold_start_diagnostics": cold_start_diagnostics,
    "price_sampling": price_sampling,
    "final_selection": final_selection,
}


def tool_stages(d, name, err, duration_ms, marks, ref):
    """Append the stage record(s) of pipeline tool ``name`` to the trace; returns their names."""
    stages = slim.TOOL_STAGES.get(name)
    if not stages:
        return []
    if err is not None:
        # The first stage that did not finish is the one that failed (rank: marks per sub-stage).
        # Sub-stages that finished before it are recorded as ok: scoring with its details (the
        # scores exist), the selection sub-stages with their duration only (no selection exists).
        failed = next((s for s in stages if s not in marks), stages[-1])
        for done in stages[:stages.index(failed)]:
            if done == "scoring" and d.scored is not None:
                slim.record(d.trace, done, "ok", marks[done], d.config, call=ref, **scoring(d))
            else:
                slim.record(d.trace, done, "ok", marks[done], d.config, pool=d.pool_result, call=ref,
                            note="finished; details unavailable because a later sub-stage failed")
        slim.record(d.trace, failed, "error", duration_ms if len(stages) == 1 else None, d.config,
                    pool=d.pool_result, error=slim.error_of(err), call=ref)
        return list(stages[:stages.index(failed) + 1])
    for stage in stages:
        fields = BUILDERS[stage](d)
        status = fields.pop("status", "ok")
        took = duration_ms if len(stages) == 1 else marks.get(stage)
        slim.record(d.trace, stage, status, took, d.config, call=ref, **fields)
    return list(stages)
