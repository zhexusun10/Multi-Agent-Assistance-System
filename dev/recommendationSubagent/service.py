import time, uuid
from dev.sharedContract.recommendationSubagentContract.request import (
    RecommendationRequest,
)
from dev.sharedContract.recommendationSubagentContract.response import (
    StateReference,
    Execution,
    Unverified,
)
from dev.sharedContract.recommendationSubagentContract.errors import (
    RecommendationError,
    ErrorDetail,
)
from .models.candidate import Response, RecommendationItem
from .context.manager import prepare
from .tools.dispatcher import Dispatcher
from .validation.gate import final_gate, replay_gate
from .validation.rules import eligible
from .tracing.trace import digest, redact
from .config import RecommendationConfig


class RecommendationService:
    def __init__(
        self, listing, geographic, store, controller, progress=None, config=None
    ):
        self.config = RecommendationConfig.model_validate(
            (config or RecommendationConfig()).model_dump()
        )
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
        trace_id = str(uuid.uuid4())
        payload_hash = digest(request.model_dump(mode="json"))
        trace = {
            "trace_id": trace_id,
            "request": request.model_dump(mode="json"),
            "data_mode": self.listing.mode,
            "controller_mode": self.controller.mode,
            "steps": [],
            "configuration": self.config.model_dump(),
        }
        execution = Execution(
            trace_id=trace_id,
            trace_ref=f"sqlite:{trace_id}",
            strategy="complete-pool weighted ranking",
            data_mode=self.listing.mode,
            controller_mode=self.controller.mode,
            model_identifier=self.controller.model_identifier,
            scoring_version=self.config.scoring.identifier,
        )
        base = {
            "request_id": request.request_id,
            "conversation_id": request.conversation_id,
            "requirements_version": request.requirements_version,
        }
        dispatcher = None
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
                    response = Response.model_validate(cached[1])
                    replay_gate(response, cached[2])
                    trace["steps"].append(
                        {
                            "kind": "final_gate",
                            "mode": "immutable_replay",
                            "passed": True,
                            "original_trace_ref": response.execution.trace_ref,
                        }
                    )
                    trace["response"] = response.model_dump(mode="json")
                    trace["pool"] = cached[2]["pool"]
                    self.store.save_trace(
                        request.conversation_id,
                        trace_id,
                        request.request_id,
                        redact(trace),
                    )
                    return response
                previous = self.store.current(request.conversation_id)
                ctx = prepare(request, previous, self.store)
                ctx["profile"]["scoring_version"] = self.config.scoring.identifier
                context = {
                    "context_ref": trace_id,
                    "request": request.model_dump(mode="json"),
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
                    "scoring_policy": self.config.scoring.model_dump(),
                    "freshness": "per-run snapshots with final re-read",
                }
                trace["context"] = context
                dispatcher = Dispatcher(
                    request,
                    ctx,
                    self.listing,
                    self.geographic,
                    trace,
                    self.progress,
                    self.config,
                )
                for attempt in range(self.config.limits.repairs + 1):
                    proposal = self.controller.propose(dispatcher, context, trace)
                    try:
                        ids = final_gate(proposal, dispatcher, trace)
                        break
                    except RecommendationError as e:
                        gate_steps = [
                            step
                            for step in trace["steps"]
                            if step["kind"] == "final_gate"
                        ]
                        if gate_steps and not gate_steps[-1].get("passed"):
                            gate_steps[-1]["error"] = e.detail.model_dump()
                        if (
                            e.detail.code not in ("STALE_EVIDENCE", "INVALID_PROPOSAL")
                            or attempt == self.config.limits.repairs
                        ):
                            raise
                        context["repair"] = e.detail.model_dump()
                rows = {r.listing_id: r for r in dispatcher.pool}
                items = []
                for rank, id in enumerate(ids, 1):
                    results = dispatcher.results[id]
                    items.append(
                        RecommendationItem(
                            listing=rows[id].model_copy(deep=True),
                            rank=rank,
                            scores=dispatcher.scores[id],
                            criterion_results=results,
                            evidence_refs=[
                                ref for r in results for ref in r.evidence_refs
                            ],
                            strengths=[
                                r.criterion_id for r in results if r.outcome == "pass"
                            ],
                            trade_offs=[
                                r.criterion_id for r in results if r.outcome == "fail"
                            ],
                            unknowns=[
                                r.criterion_id
                                for r in results
                                if r.outcome in ("unknown", "unsupported")
                            ],
                        )
                    )
                hard = {
                    c.criterion_id
                    for c in request.requirements.criteria
                    if c.strength == "hard"
                }
                unverified = [
                    Unverified(listing_id=k, criterion_results=v)
                    for k, v in dispatcher.results.items()
                    if not eligible(v, request.requirements.criteria)
                    and not any(
                        r.outcome == "fail" and r.criterion_id in hard for r in v
                    )
                ]
                status = (
                    "success"
                    if items
                    else (
                        "needs_clarification"
                        if unverified
                        or any(
                            c.field in context["unsupported"] and c.strength == "hard"
                            for c in request.requirements.criteria
                        )
                        else "no_match"
                    )
                )
                state = StateReference(
                    state_id=str(uuid.uuid4()),
                    candidate_pool_ref=f"{trace_id}/pool",
                    run_sequence=ctx["sequence"],
                    feedback_version=len(ctx["events"]),
                    source_count=len(dispatcher.rows),
                    prefilter_excluded=len(dispatcher.rows) - len(dispatcher.pool),
                    assessed_count=len(dispatcher.pool),
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
                    unverified_candidates=unverified[:4],
                    evidence=dispatcher.evidence,
                    clarifications=(
                        ["Resolve missing or unsupported hard-constraint evidence."]
                        if status == "needs_clarification"
                        else []
                    ),
                    relaxation_suggestions=(
                        ["Submit an explicitly revised full requirements snapshot."]
                        if status == "no_match"
                        else []
                    ),
                    warnings=[
                        "Sample viewing durations are simulated observations.",
                        "Posting time does not establish current availability.",
                    ],
                    state=state,
                    execution=execution,
                )
                snapshot = {
                    "configuration": self.config.model_dump(),
                    "state_id": state.state_id,
                    "sequence": ctx["sequence"],
                    "request": request.model_dump(mode="json"),
                    "events": ctx["events"],
                    "profile": ctx["profile"],
                    "pool": dispatcher.snapshot(),
                }
                self.store.commit(request, payload_hash, response, snapshot)
                trace["pool"] = dispatcher.snapshot()
                trace["response"] = response.model_dump(mode="json")
                self.store.save_trace(
                    request.conversation_id, trace_id, request.request_id, redact(trace)
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
                request.conversation_id, trace_id, request.request_id, redact(trace)
            )
        return response

    def close(self):
        self.listing.close()
        self.geographic.close()
        self.store.close()
