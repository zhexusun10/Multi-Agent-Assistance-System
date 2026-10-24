import time, uuid
from pydantic import ValidationError
from ..models.tool_results import (
    ToolResult,
    CandidatePreview,
    ScorePreview,
)
from ..validation.rules import evaluate, eligible
from ..ranking.scoring_v2 import score_pool
from ..ranking import selection as selection_v1
from .pool_builder import GEO_FIELDS, build_pool, by_listing, normalize_evidence
from ..geography.resolver import key_map, to_contract
from ..tracing.trace import digest
from ..tracing import slim
from ..tracing.stages import tool_stages
from ..config import RecommendationConfig
from .registry import BY_NAME, NAMES, PIPELINE, RESOLUTION_TOOLS  # noqa: F401 (NAMES re-exported)
from .geo_tools import GeoTools, bound_resolution
from recommendation.contracts.errors import RecommendationError, ErrorDetail


# Rejections that never use resolution budget: invalid arguments, stage order / stale
# references, and the budget errors themselves. BIND_REJECTED is charged (the bind was evaluated).
UNCHARGED = frozenset({"INVALID_ARGUMENT", "STALE_REFERENCE", "RESOLUTION_BUDGET_EXHAUSTED",
                       "SEARCH_BUDGET_EXHAUSTED", "UNSUPPORTED_OPERATION"})
RECORD_KEYS = ("criterion_id", "method", "listings", "known", "unknown")


def compact_record(record):
    """Per-criterion geographic summary for the model (no per-listing data)."""
    resolution = record.get("resolution") or {}
    return {**{k: record.get(k) for k in RECORD_KEYS},
            "resolution": {k: resolution.get(k) for k in ("status", "entity_id", "entity_kind", "method", "reason")}}


def _argument_error(exc):
    """INVALID_ARGUMENT naming the offending argument paths (never echoing their values)."""
    problems = sorted({".".join(str(p) for p in e["loc"]) or "arguments" for e in exc.errors()})
    return ErrorDetail(code="INVALID_ARGUMENT",
                       message="Invalid tool arguments: " + ", ".join(problems[:10]))


class Dispatcher:
    """Run-scoped tools (spec section 6, R12; registry in tools/registry.py).

    Pipeline stages, in order: resolve_entities (deterministic native resolution of every
    geographic target) -> build_pool (build or reuse the hard-criteria exploration pool,
    tools/pool_builder.py + state/pool_cache.py; ``rows``/``pool`` are eligible pool rows) ->
    get_geographic_evidence (soft geographic criteria over the pool) -> rank_candidates
    (validation, scoring, selection by code). A stage whose predecessor is not current returns a
    STALE_REFERENCE tool error; running a stage discards every later stage. Query tools
    (tools/geo_tools.py) never change state. bind_entity changes a criterion's resolved entity,
    so pool_key changes and build_pool / get_geographic_evidence / rank_candidates results are
    discarded (R12). Resolved and bound entity IDs feed pool_key and every geographic evidence
    call of the run (pool, soft evidence, final gate)."""

    def __init__(
        self, request, context, listing, geographic, trace, progress=None, config=None, pools=None
    ):
        self.config = config if config is not None else RecommendationConfig.default()
        self.request = request
        self.context = context
        self.listing = listing
        self.geographic = geographic
        self.trace = trace
        self.progress = progress
        self.ref = trace["trace_id"]
        self.pools = pools  # PoolCache of the run's state store (None: build without caching)
        self.pool_result = None  # tools.pool_builder.Pool of this run
        self.rows = None
        self.pool = []
        self.evidence = []
        self.results = {}
        self.ranked = None
        self.selected = None
        self.selection_input = None
        self.selection = None  # ranking.selection.Selection of the latest rank stage (select-v1)
        # Test-only override of the selection seed (terminal set-seed, P6): replaces the R3 base
        # seed and is recorded; never set by the service API.
        self.seed_override = None
        self.scores = {}  # ScoreBreakdown of the selection + preview rows only (R10)
        self.scored = None  # ranking.scoring_v2.PoolScores of the latest rank stage
        self.calls = 0
        self.resolution_calls = 0  # query/bind calls (controller.max_resolution_tool_calls)
        self.started = time.monotonic()
        self.geo_records = []  # compact records of THIS run's soft geographic evidence call
        self.geo_done = False  # get_geographic_evidence ran for the current pool
        self.resolutions = None  # criterion_id -> geography.resolver.Resolution (this run)
        self.resolved = {}  # criterion_id -> (entity_id, entity_kind) for resolved targets
        self.bindings = {}  # criterion_id -> (entity_id, search_no) accepted by bind_entity
        self.seen_entities = set()  # entity IDs returned by any tool of this run
        self.tools = GeoTools(self)

    def check_deadline(self):
        if time.monotonic() - self.started > self.config.controller.timeout_seconds:
            raise RecommendationError(
                "LOOP_EXHAUSTED", "Overall execution time exhausted"
            )

    def completed_stages(self):
        done = []
        for stage, ok in zip(PIPELINE, (self.resolutions is not None, self.rows is not None,
                                        self.geo_done, self.selected is not None)):
            if not ok:
                break
            done.append(stage)
        return done

    def require(self, stage):
        if stage is not None and stage not in self.completed_stages():
            raise RecommendationError(
                "STALE_REFERENCE",
                f"Call {stage} for the current inputs first (pipeline order: {' -> '.join(PIPELINE)})",
            )

    def call(self, name, arguments):
        self.check_deadline()
        if self.calls >= self.config.controller.max_tool_calls:
            raise RecommendationError(
                "LOOP_EXHAUSTED", "Tool execution budget exhausted"
            )
        self.calls += 1
        start = time.monotonic()
        call_id = str(uuid.uuid4())
        before = digest(self.identity())
        err = None
        data = None
        charge = False
        marks = {}  # rank_candidates: duration of each finished sub-stage (slim trace)
        try:
            spec = BY_NAME.get(name)
            if spec is None:
                raise RecommendationError("UNSUPPORTED_OPERATION", "Unknown tool")
            try:
                args = spec.arguments.model_validate(arguments)
            except ValidationError as exc:
                if isinstance(arguments, dict) and arguments.get("context_ref") not in (None, self.ref):
                    raise RecommendationError(
                        "STALE_REFERENCE", "Tool context must reference the current run"
                    ) from None
                raise RecommendationError(**_argument_error(exc).model_dump()) from None
            if args.context_ref != self.ref:
                raise RecommendationError(
                    "STALE_REFERENCE", "Tool context must reference the current run"
                )
            self.require(spec.requires)
            if name in RESOLUTION_TOOLS:
                budget = self.config.controller.max_resolution_tool_calls
                if self.resolution_calls >= budget:
                    raise RecommendationError(
                        "RESOLUTION_BUDGET_EXHAUSTED",
                        f"Resolution/query tool budget exhausted (controller.max_resolution_tool_calls = {budget}); "
                        "continue with the pipeline tools",
                    )
                # Charged once the tool has run (below), so a call rejected for its arguments,
                # its stage or a stale reference never uses resolution budget.
                charge = True
            if self.progress:
                self.progress(name)
            if name == "resolve_entities":
                self.resolve_entities()
                data = {"resolution": self.resolution_view()}
            elif name == "build_pool":
                self.pool_result = build_pool(
                    self.request, self.config, self.listing, self.geographic, self.pools,
                    resolved=self.resolved, resolutions=self.resolutions,
                )
                self.rows = self.pool_result.rows
                self.pool = list(self.rows)
                # Hard-geo evidence comes from the pool (rebuilt with this request's criterion IDs).
                self.evidence = self.pool_result.hard_evidence(self.request, self.resolved)
                self.geo_records = []
                self.geo_done = False
                self.reset_results()
                data = {"pool": self.pool_result.reference(), "partition": self.pool_result.partition}
            elif name == "get_geographic_evidence":
                soft = [
                    c
                    for c in self.request.requirements.criteria
                    if c.field in GEO_FIELDS and c.strength == "soft"
                ]
                fresh = (
                    self.geographic.evidence(
                        self.pool, soft, resolutions=self.resolutions, settings=self.config
                    )
                    if soft
                    else []
                )
                self.geo_records = (
                    list(getattr(self.geographic, "last_snapshot", [])) if soft else []
                )
                self.evidence = self.pool_result.hard_evidence(
                    self.request, self.resolved
                ) + normalize_evidence(
                    fresh, soft, {r.listing_id: r for r in self.pool}, self.resolved
                )
                self.geo_done = True
                self.reset_results()
                # Model-facing summary only: per-listing provenance (e.g. the nearest exit of every
                # pool listing) stays in run memory (geo_records), never in the trace (P6);
                # nearest_mrt / distance_to return it for at most model_result_candidates listings.
                data = {"soft_criteria": [c.criterion_id for c in soft],
                        "records": [compact_record(r) for r in self.geo_records]}
            elif name == "rank_candidates":
                stage_started = time.perf_counter()
                grouped = by_listing(self.evidence)
                self.results = {
                    r.listing_id: evaluate(
                        r,
                        self.request.requirements.criteria,
                        grouped.get(r.listing_id, []),
                    )
                    for r in self.pool
                }
                valid = [
                    r
                    for r in self.pool
                    if eligible(
                        self.results[r.listing_id],
                        self.request.requirements.criteria,
                    )
                ]
                # Full-pool fused scoring (ranking/scoring_v2.py): numpy component arrays for
                # every eligible pool listing; ScoreBreakdown objects only for the selection
                # and the trace preview rows. ``scored`` keeps the components (stability
                # trials, debug trace).
                self.scored = score_pool(
                    valid,
                    self.request.requirements.criteria,
                    self.results,
                    grouped,
                    self.request.behavior_context,
                    self.config,
                )
                self.ranked = self.scored.ranked
                marks["scoring"] = (time.perf_counter() - stage_started) * 1000
                # Cold-start diagnostics + select-v1 (ranking/selection.py) on the full-pool
                # order; scoring always happens before any sampling.
                self.selection = selection_v1.select(
                    self.scored, self.request, self.config, self.pool_result.key,
                    self.pool_result.version, seed_override=self.seed_override, marks=marks,
                )
                self.selected = list(self.selection.ids)
                self.scores = {
                    k: self.scored.breakdown(k)
                    for k in dict.fromkeys(self.selected + self.ranked[: self.config.trace.preview_rows])
                }
                self.selection_input = self.selection.selection_input
                data = {"algorithm_version": self.selection_input["algorithm_version"],
                        "pool_size": len(self.ranked),
                        "scoring": self.scored.summary(self.config.trace.preview_rows),
                        "selection": self.selection.record}
            elif name == "bind_entity":
                bound, search_no = self.tools.bind_entity(args)
                self.bindings[args.criterion_id] = (bound.entity_id, search_no)
                self.resolutions = {**self.resolutions, args.criterion_id: bound}
                self.resolved = key_map(self.resolutions)
                self.discard_stages()  # R12: the bound entity changes pool_key
                data = {"criterion_id": args.criterion_id,
                        "resolution": next(r for r in self.resolution_view() if r["criterion_id"] == args.criterion_id),
                        "invalidated": ["build_pool", "get_geographic_evidence", "rank_candidates"]}
            else:
                data = getattr(self.tools, name)(args)
            self.check_deadline()
        except RecommendationError as e:
            err = e.detail
            if e.detail.code in UNCHARGED:
                charge = False
        except ValueError:
            charge = False
            err = ErrorDetail(
                code="INVALID_ARGUMENT",
                message="Tool arguments or repository values failed validation",
            )
        except Exception as exc:
            charge = False
            err = ErrorDetail(
                code="INTERNAL_TOOL_ERROR",
                message=f"Tool failed ({type(exc).__name__}); no result committed",
            )
        if charge:
            self.resolution_calls += 1
        snap = self.identity()
        ref = f"{self.ref}/tools/{self.calls}"
        preview_rows = self.config.trace.preview_rows
        preview_ids = (
            self.ranked
            if self.ranked is not None
            else [r.listing_id for r in self.pool]
        )[:preview_rows]
        by_id = {r.listing_id: r for r in self.pool}
        result = ToolResult(
            call_id=call_id,
            tool=name,
            input_version=before,
            output_version=digest(snap),
            status="error" if err else "ok",
            result_ref=ref,
            candidate_ids=[
                r.listing_id
                for r in self.pool[: self.config.controller.model_result_candidates]
            ],
            candidate_count=len(self.pool),
            candidate_ids_truncated=len(self.pool)
            > self.config.controller.model_result_candidates,
            ranked_ids_truncated=len(self.ranked or [])
            > self.config.controller.model_result_candidates,
            ranked_ids=(self.ranked or [])[
                : self.config.controller.model_result_candidates
            ],
            selected_ids=list(self.selected or []),
            completed_stages=self.completed_stages(),
            error=err,
            data=None if err else data,
            previews=[
                CandidatePreview(
                    listing=by_id[k],
                    scores=(
                        ScorePreview(
                            **{
                                field: getattr(self.scores[k], field)
                                for field in ScorePreview.model_fields
                            }
                        )
                        if k in self.scores
                        else None
                    ),
                    criterion_results=self.results.get(k, []),
                )
                for k in preview_ids
            ],
            previews_truncated=len(self.pool) > preview_rows,
            verified_count=len(self.ranked or []),
            evidence_count=len(self.evidence),
        )
        duration_ms = (time.monotonic() - start) * 1000
        # Slim trace (tracing/slim.py, P6): stage records + arguments and a compact result; the
        # dispatcher state (pool rows, full-pool evidence/validation, per-listing provenance) is
        # never stored in a tool step. snapshot_ref resolves to this step record.
        stages = tool_stages(self, name, err, duration_ms, marks, ref) if name in slim.TOOL_STAGES else []
        if (name == "rank_candidates" and err is None and self.config.trace.mode == "debug"):
            self.trace["debug"] = slim.debug_rows(self.scored, self.selection, self.config, ref)
        self.trace["steps"].append(
            slim.tool_step(ref, name, arguments, result, duration_ms, self.config,
                           pipeline=name in slim.TOOL_STAGES, stages=stages)
        )
        return result

    def discard_stages(self):
        """Discard build_pool and every later stage (bind_entity, resolve_entities, data change)."""
        self.pool_result = None
        self.rows = None
        self.pool = []
        self.evidence = []
        self.geo_records = []
        self.geo_done = False
        self.reset_results()

    def invalidate_pool(self):
        """Discard the run's pool after a data change: until resolve_entities and build_pool run
        again, every later tool returns STALE_REFERENCE and final_gate rejects any proposal."""
        self.discard_stages()
        # The catalog version is part of data_version: resolve again with the current catalog
        # (accepted bindings are re-applied by resolve_entities while their entity still exists).
        self.resolutions = None
        self.resolved = {}
        self.tools.reset_catalog()

    def resolve_entities(self):
        """Entity-resolution stage: resolve every geographic criterion target (code, not the
        model) against the current catalog, re-apply accepted bindings, and discard the later
        stages. Returns {criterion_id: Resolution}."""
        geo = [c for c in self.request.requirements.criteria if c.field in GEO_FIELDS]
        resolutions = self.geographic.resolve_criteria(geo, self.config) if geo else {}
        if self.bindings:
            catalog = self.tools.catalog()
            for criterion_id, (entity_id, search_no) in self.bindings.items():
                entity = catalog.entity(entity_id)
                if entity is not None:
                    resolutions[criterion_id] = bound_resolution(entity, search_no)
        self.resolutions = resolutions
        self.resolved = key_map(self.resolutions)
        self.tools.seen(
            [r.entity_id for r in resolutions.values() if r.resolved and r.entity_kind != "category"]
            + [c[0] for r in resolutions.values() for c in r.candidates]
        )
        self.discard_stages()
        return self.resolutions

    def resolution_view(self):
        """Model-facing resolution per geographic criterion (status, entity, reason, candidates)."""
        by_id = {c.criterion_id: c for c in self.request.requirements.criteria}
        view = []
        for result in self.resolution_results():
            c = by_id[result.criterion_id]
            view.append({
                **result.model_dump(mode="json"),
                "strength": c.strength,
                # bind_entity only disambiguates a name-ambiguous criterion (geo_tools.bind_problem).
                "bindable": result.status == "ambiguous" and result.method != "id+name" and bool(c.destination_name),
            })
        return view

    def reset_results(self):
        self.results = {}
        self.ranked = None
        self.selected = None
        self.selection_input = None
        self.selection = None
        self.scores = {}
        self.scored = None

    def resolution_results(self):
        """ResolutionResult (contract) per geographic criterion, in request order."""
        resolutions = self.resolutions or {}
        return [
            to_contract(c, resolutions[c.criterion_id])
            for c in self.request.requirements.criteria
            if c.criterion_id in resolutions
        ]

    def identity(self):
        """Compact identity of the dispatcher state: its digest is the ToolResult input/output
        version (P6 review). Stage outputs are named by their identities instead of being
        re-serialised on every call: the pool rows and hard evidence by the pool reference
        (pool_key / pool_version / data_version), the run's evidence by its R4 evidence IDs,
        the criterion results by their count (derived from rows + evidence + criteria), the
        per-listing nearest exits are left out (provenance derived from the pool and the
        resolution). Bounded work per call: no model_dump of pool rows, evidence or results."""
        return {
            "resolution": {k: r.summary() for k, r in (self.resolutions or {}).items()},
            "bindings": {k: v[0] for k, v in self.bindings.items()},
            "stages": self.completed_stages(),
            "searches": self.tools.searches,
            "pool": self.pool_result.reference() if self.pool_result else None,
            "rows": None if self.rows is None else len(self.rows),
            "geographic_records": [{k: v for k, v in r.items() if k != "nearest_exits"} for r in self.geo_records],
            "evidence": [(e.criterion_id, e.evidence_id) for e in self.evidence],
            "validation": len(self.results),
            "scores": {k: s.model_dump() for k, s in self.scores.items()},
            "ranking": self.ranked,
            "selected": self.selected,
        }

    def snapshot(self):
        # Full dispatcher state (tests and debugging; the slim trace (P6) never stores it: pool
        # content is referenced by pool_version and resolved from pool_cache). Tool versions use
        # the compact identity() instead. "rows" are the pool rows of this run, not the source table.
        return {
            "resolution": {k: r.summary() for k, r in (self.resolutions or {}).items()},
            "bindings": {k: v[0] for k, v in self.bindings.items()},
            "stages": self.completed_stages(),
            "searches": self.tools.searches,
            "pool": self.pool_result.reference() if self.pool_result else None,
            "partition": self.pool_result.partition if self.pool_result else None,
            "geographic_records": self.geo_records,
            "rows": [r.model_dump() for r in self.rows or []],
            "pool_ids": [r.listing_id for r in self.pool],
            "evidence": [e.model_dump() for e in self.evidence],
            "validation": {
                k: [v.model_dump() for v in vs] for k, vs in self.results.items()
            },
            "scores": {k: s.model_dump() for k, s in self.scores.items()},
            "ranking": self.ranked,
            "selected": self.selected,
        }
