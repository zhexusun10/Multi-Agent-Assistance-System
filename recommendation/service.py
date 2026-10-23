import time, uuid
from recommendation.contracts.request import (
    RecommendationRequest,
)
from recommendation.contracts.response import (
    StateReference,
    Execution,
)
from recommendation.contracts.errors import (
    RecommendationError,
    ErrorDetail,
)
from .models.candidate import Response
from .context.manager import prepare
from .tools.dispatcher import Dispatcher
from .validation.gate import final_gate, replay_gate, DataChanged
from .tracing.trace import digest
from .tracing import slim
from .config import RecommendationConfig
from .ranking import scoring_v2
from .ranking import selection as selection_v1
from .state.store import TRACE_FORMAT, SNAPSHOT_FORMAT
from .state.pool_cache import PoolCache
from .tools.pool_builder import parse_rows
from . import assembly


class RecommendationService:
    def __init__(
        self, listing, geographic, store, controller, progress=None, config=None
    ):
        # Settings are immutable and validated at load; None means the committed YAML.
        self.config = config if config is not None else RecommendationConfig.default()
        if not isinstance(self.config, RecommendationConfig):
            raise TypeError("config must be a RecommendationConfig loaded from the settings file")
        self.listing = listing
        self.geographic = geographic
        self.store = store
        self.controller = controller
        self.progress = progress

    def recommend(self, request: RecommendationRequest) -> Response:
        request = RecommendationRequest.model_validate(
            request.model_dump()
            if isinstance(request, RecommendationRequest)
            else request
        )
        started = time.monotonic()
        config = self.config  # frozen per request: later reassignment never affects this run
        trace_id = str(uuid.uuid4())
        payload_hash = digest(request.model_dump(mode="json"))
        trace = {
            "format": TRACE_FORMAT,
            "trace_id": trace_id,
            "request": request.model_dump(mode="json"),
            "data_mode": self.listing.mode,
            "controller_mode": self.controller.mode,
            "layout": slim.LAYOUT,
            "trace_mode": config.trace.mode,
            "stages": [],
            "steps": [],
            "configuration": config.to_data(),
            **config.identity(),
        }
        execution = Execution(
            trace_id=trace_id,
            trace_ref=f"sqlite:{trace_id}",
            strategy="full-pool fused-v2 scoring + select-v1 selection",
            data_mode=self.listing.mode,
            controller_mode=self.controller.mode,
            model_identifier=self.controller.model_identifier,
            scoring_version=scoring_v2.scoring_identifier(config),
            selection_algorithm=assembly.SELECTION_ALGORITHM,
            config_version=config.config_version,
            config_hash=config.config_hash,
            selection_hash=config.selection_hash,
        )
        base = {
            "request_id": request.request_id,
            "conversation_id": request.conversation_id,
            "requirements_version": request.requirements_version,
        }
        dispatcher = None
        feedback = request.feedback
        validation = slim.Running(trace, "input_validation", config, inputs={
            "criteria": len(request.requirements.criteria),
            "hard_criteria": sum(c.strength == "hard" for c in request.requirements.criteria),
            "previous_state_id": request.previous_state_id is not None,
            "previous_round_browsing": len(feedback.previous_round.browsing) if feedback.previous_round else 0,
            "appointments": len(feedback.appointments),
            "result_limit": request.result_limit,
            "cold_start_exited": request.cold_start.exited,
        })
        try:
            with self.store.transaction():
                self.store.reserve(request, payload_hash)
            with self.store.transaction():
                cached = self.store.cached(request.conversation_id, request.request_id)
                if cached:
                    if cached[0] != payload_hash:
                        raise RecommendationError(
                            "IDEMPOTENCY_CONFLICT",
                            "Request ID already used with another payload",
                        )
                    # Immutable replay, verified against its original snapshot. Never advertised as refreshed data.
                    validation.done(outputs={"replay": True})
                    response = Response.model_validate(cached[1])
                    gate = slim.Running(trace, "final_gate", config, params={"mode": "immutable_replay"})
                    replay_gate(response, cached[2])
                    gate.done(outputs={"selected": len(response.recommendations)},
                              note="stored response verified against its run snapshot (replay_gate)")
                    trace["steps"].append(
                        {
                            "kind": "final_gate",
                            "mode": "immutable_replay",
                            "passed": True,
                            "original_trace_ref": response.execution.trace_ref,
                        }
                    )
                    trace["response"] = response.model_dump(mode="json")
                    # Compact snapshots reference the pool; P2-era ones embedded it.
                    trace["pool_ref"] = cached[2].get("pool") if "selection_input" in cached[2] else None
                    self.store.save_trace(
                        request.conversation_id,
                        trace_id,
                        request.request_id,
                        slim.finalize(trace),
                    )
                    return response
                previous = self.store.current(request.conversation_id)
                ctx = prepare(request, previous, self.store, config)
                # result_limit None -> settings default; the stored request keeps the original body.
                limit = request.result_limit or config.recommendation.default_result_limit
                if limit > config.recommendation.max_result_limit:
                    raise RecommendationError(
                        "INVALID_REQUEST",
                        f"result_limit {limit} exceeds recommendation.max_result_limit "
                        f"{config.recommendation.max_result_limit}",
                    )
                effective = request.model_copy(update={"result_limit": limit})
                validation.done(
                    outputs={"replay": False, "result_limit": limit, "run_sequence": ctx["sequence"],
                             "previous_state_id": request.previous_state_id},
                    params={"default_result_limit": config.recommendation.default_result_limit,
                            "max_result_limit": config.recommendation.max_result_limit,
                            "counted_appointment_statuses": list(config.ranking.behavior.counted_appointment_statuses)},
                )
                ctx["profile"]["scoring_version"] = scoring_v2.scoring_identifier(config)
                context = {
                    "context_ref": trace_id,
                    "request": effective.model_dump(mode="json"),
                    "profile": ctx["profile"],
                    "changes": ctx["changes"],
                    "previous_state_id": request.previous_state_id,
                    "capabilities": ["listing attributes", "straight_line distance"],
                    "unsupported": [
                        "commute",
                        "furnishing",
                        "availability",
                        "lease_months",
                    ],
                    "scoring_policy": scoring_v2.describe(config),
                    "selection_policy": selection_v1.describe(config),
                    "freshness": "pool cache keyed by (conversation, pool_key, data_version); "
                    "final gate re-queries the data version and re-reads the selection by id",
                }
                trace["context"] = context
                dispatcher = Dispatcher(
                    effective,
                    ctx,
                    self.listing,
                    self.geographic,
                    trace,
                    self.progress,
                    config,
                    pools=PoolCache(self.store, config, parse=parse_rows),
                )
                rebuilt = False
                for attempt in range(config.controller.repairs + 1):
                    proposal = self.controller.propose(dispatcher, context, trace)
                    gate = slim.Running(trace, "final_gate", config, inputs={
                        "proposed": len(proposal.listing_ids), "attempt": attempt + 1})
                    try:
                        ids = final_gate(proposal, dispatcher, trace)
                        gate.done(pool=dispatcher.pool_result, outputs={
                            "selected": len(ids), "rows_read": trace["steps"][-1].get("rows_read")})
                        break
                    except RecommendationError as e:
                        gate.failed(e.detail)
                        # A changed data version rebuilds the pool through the cache ONCE. The
                        # stale pool is discarded here, so no controller can re-propose from it:
                        # only new resolve_entities + build_pool calls (a rebuild) can yield an acceptable proposal.
                        if isinstance(e, DataChanged):
                            if rebuilt:
                                raise
                            rebuilt = True
                            dispatcher.invalidate_pool()
                        gate_steps = [
                            step
                            for step in trace["steps"]
                            if step["kind"] == "final_gate"
                        ]
                        if gate_steps and not gate_steps[-1].get("passed"):
                            gate_steps[-1]["error"] = e.detail.model_dump()
                        if (
                            e.detail.code not in ("STALE_EVIDENCE", "INVALID_PROPOSAL")
                            or attempt == config.controller.repairs
                        ):
                            raise
                        context["repair"] = e.detail.model_dump()
                assembling = slim.Running(trace, "response_assembly", config)
                pool = assembly.pool_summary(effective, dispatcher)
                pool_ref = {**dispatcher.pool_result.reference(), "persisted": dispatcher.pool_result.persisted}
                resolved = assembly.resolution(effective, dispatcher)
                selection = assembly.selection_summary(dispatcher)
                items = assembly.items(effective, dispatcher, ids)
                problems = assembly.criterion_problems(effective, resolved)
                # R8: needs_clarification only for criterion-level problems; unknown data is
                # reported through pool counts, unknown_reasons and a warning.
                status = (
                    "success"
                    if items
                    else ("needs_clarification" if problems else "no_match")
                )
                state = StateReference(
                    state_id=str(uuid.uuid4()),
                    candidate_pool_ref=f"{trace_id}/pool",
                    run_sequence=ctx["sequence"],
                    source_count=pool.source_count,
                    prefilter_excluded=pool.excluded_hard_fail + pool.excluded_hard_unknown,
                    assessed_count=pool.size,
                )
                execution.tool_calls = dispatcher.calls
                execution.model_calls = sum(
                    s["kind"] == "model" for s in trace["steps"]
                )
                execution.duration_ms = (time.monotonic() - started) * 1000
                usages = [
                    s.get("output", {}).get("usage")
                    for s in trace["steps"]
                    if s["kind"] == "model"
                ]
                execution.tokens = (
                    sum(u.get("total_tokens", 0) for u in usages if u)
                    if any(usages)
                    else None
                )
                response = Response(
                    **base,
                    status=status,
                    recommendations=items,
                    evidence=assembly.returned_evidence(dispatcher, items),
                    resolution=resolved,
                    pool=pool,
                    selection=selection,
                    clarifications=problems if status == "needs_clarification" else [],
                    relaxation_suggestions=(
                        ["Submit an explicitly revised full requirements snapshot."]
                        if status == "no_match"
                        else []
                    ),
                    warnings=assembly.warnings(effective, pool, dispatcher.pool_result.warnings),
                    state=state,
                    execution=execution,
                )
                snapshot = {
                    "format": SNAPSHOT_FORMAT,
                    "configuration": config.to_data(),
                    **config.identity(),
                    "state_id": state.state_id,
                    "sequence": ctx["sequence"],
                    "request": request.model_dump(mode="json"),
                    "result_limit": limit,
                    "profile": ctx["profile"],
                    # Compact (spec section 4, R6): the pool is referenced, never embedded.
                    "pool": {k: pool_ref[k] for k in ("pool_key", "pool_version", "data_version", "cache")},
                    "selection_input": dispatcher.selection_input,
                    "resolution": [r.model_dump(mode="json") for r in resolved],
                }
                self.store.commit(request, payload_hash, response, snapshot)
                assembling.done(pool=dispatcher.pool_result, outputs={
                    "status": status, "recommendations": len(items), "evidence": len(response.evidence)})
                trace["pool_ref"] = pool_ref
                trace["pool_stats"] = dispatcher.pool_result.stats
                trace["response"] = response.model_dump(mode="json")
                self.store.save_trace(
                    request.conversation_id, trace_id, request.request_id, slim.finalize(trace)
                )
                return response
        except RecommendationError as e:
            error = e.detail
        except Exception as exc:
            trace["exception_type"] = type(exc).__name__
            error = ErrorDetail(
                code="INTERNAL_ERROR",
                message="Recommendation execution failed; committed state preserved",
            )
        execution.duration_ms = (time.monotonic() - started) * 1000
        execution.model_calls = sum(step["kind"] == "model" for step in trace["steps"])
        usage = [
            step.get("output", {}).get("usage")
            for step in trace["steps"]
            if step["kind"] == "model"
        ]
        execution.tokens = (
            sum(item.get("total_tokens", 0) for item in usage if item)
            if any(usage)
            else None
        )
        if dispatcher:
            execution.tool_calls = dispatcher.calls
            if dispatcher.pool_result:
                # The run transaction rolled back, so its pool rows were not persisted (R13).
                trace["pool_ref"] = {**dispatcher.pool_result.reference(), "persisted": False}
        # Errors locate a stage (P6): the stage that was running, else the stage whose tool or
        # gate error ended the run, else the model / controller event.
        slim.fail_running(trace, error)
        trace["error"] = {"stage": slim.locate_error(trace, error), **slim.error_of(error)}
        response = Response(
            **base,
            status="error",
            error=error,
            state=StateReference(),
            execution=execution,
        )
        trace["response"] = response.model_dump(mode="json")
        with self.store.transaction():
            self.store.save_trace(
                request.conversation_id, trace_id, request.request_id, slim.finalize(trace)
            )
        return response

    def close(self):
        self.listing.close()
        self.geographic.close()
        self.store.close()
