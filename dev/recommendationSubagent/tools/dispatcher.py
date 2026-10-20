import time, uuid
from ..models.tool_results import (
    ToolArguments,
    ToolResult,
    CandidatePreview,
    ScorePreview,
)
from ..validation.rules import evaluate, eligible
from ..ranking.ranker import score
from ..tracing.trace import digest
from ..config import RecommendationConfig
from dev.sharedContract.recommendationSubagentContract.errors import RecommendationError

NAMES = (
    "retrieve_listings",
    "get_geographic_evidence",
    "validate_hard_constraints",
    "rank_candidates",
)


class Dispatcher:
    def __init__(
        self, request, context, listing, geographic, trace, progress=None, config=None
    ):
        self.config = config or RecommendationConfig()
        self.request = request
        self.context = context
        self.listing = listing
        self.geographic = geographic
        self.trace = trace
        self.progress = progress
        self.ref = trace["trace_id"]
        self.rows = None
        self.pool = []
        self.evidence = []
        self.results = {}
        self.ranked = None
        self.scores = {}
        self.calls = 0
        self.started = time.monotonic()
        self.excluded = {}

    def check_deadline(self):
        if time.monotonic() - self.started > self.config.limits.timeout_seconds:
            raise RecommendationError(
                "LOOP_EXHAUSTED", "Overall execution time exhausted"
            )

    def call(self, name, arguments):
        self.check_deadline()
        if self.calls >= self.config.limits.tool_calls:
            raise RecommendationError(
                "LOOP_EXHAUSTED", "Tool execution budget exhausted"
            )
        self.calls += 1
        start = time.monotonic()
        call_id = str(uuid.uuid4())
        before = digest(self.snapshot())
        err = None
        try:
            args = ToolArguments.model_validate(arguments)
            if args.context_ref != self.ref:
                raise RecommendationError(
                    "STALE_REFERENCE", "Tool context must reference the current run"
                )
            if name not in NAMES:
                raise RecommendationError("UNSUPPORTED_OPERATION", "Unknown tool")
            if self.progress:
                self.progress(name)
            if name == "retrieve_listings":
                self.rows = self.listing.retrieve()
                self.evidence = []
                self.results = {}
                self.ranked = None
                self.scores = {}
                cheap = [
                    c
                    for c in self.request.requirements.criteria
                    if c.strength == "hard" and c.field not in ("distance", "commute")
                ]
                self.excluded = {
                    r.listing_id: [
                        v.model_dump()
                        for v in evaluate(r, cheap, [])
                        if v.outcome == "fail"
                    ]
                    for r in self.rows
                    if any(v.outcome == "fail" for v in evaluate(r, cheap, []))
                }
                self.pool = [r for r in self.rows if r.listing_id not in self.excluded]
            else:
                if self.rows is None:
                    raise RecommendationError(
                        "STALE_REFERENCE", "Retrieve candidates first"
                    )
                if name == "get_geographic_evidence":
                    self.evidence = self.geographic.evidence(
                        self.pool,
                        [
                            c
                            for c in self.request.requirements.criteria
                            if c.field in ("distance", "commute")
                        ],
                    )
                    self.results = {}
                    self.ranked = None
                    self.scores = {}
                elif name == "validate_hard_constraints":
                    self.results = {
                        r.listing_id: evaluate(
                            r, self.request.requirements.criteria, self.evidence
                        )
                        for r in self.pool
                    }
                    self.ranked = None
                    self.scores = {}
                elif name == "rank_candidates":
                    if not self.results and self.pool:
                        raise RecommendationError(
                            "STALE_REFERENCE", "Validate current pool first"
                        )
                    valid = [
                        r
                        for r in self.pool
                        if eligible(
                            self.results[r.listing_id],
                            self.request.requirements.criteria,
                        )
                    ]
                    self.scores = {
                        r.listing_id: score(
                            r,
                            self.request.requirements.criteria,
                            self.evidence,
                            self.context["viewed"],
                            self.config.scoring,
                        )
                        for r in valid
                    }
                    self.ranked = sorted(
                        self.scores, key=lambda k: (-self.scores[k].combined_score, k)
                    )
            self.check_deadline()
        except RecommendationError as e:
            err = e.detail
        except ValueError:
            from dev.sharedContract.recommendationSubagentContract.errors import (
                ErrorDetail,
            )

            err = ErrorDetail(
                code="INVALID_ARGUMENT",
                message="Tool arguments or repository values failed validation",
            )
        except Exception as exc:
            from dev.sharedContract.recommendationSubagentContract.errors import (
                ErrorDetail,
            )

            err = ErrorDetail(
                code="INTERNAL_TOOL_ERROR",
                message=f"Tool failed ({type(exc).__name__}); no result committed",
            )
        snap = self.snapshot()
        ref = f"{self.ref}/tools/{self.calls}"
        preview_ids = (
            self.ranked
            if self.ranked is not None
            else [r.listing_id for r in self.pool]
        )[:4]
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
                for r in self.pool[: self.config.limits.model_result_candidates]
            ],
            candidate_count=len(self.pool),
            candidate_ids_truncated=len(self.pool)
            > self.config.limits.model_result_candidates,
            ranked_ids_truncated=len(self.ranked or [])
            > self.config.limits.model_result_candidates,
            ranked_ids=(self.ranked or [])[
                : self.config.limits.model_result_candidates
            ],
            error=err,
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
            previews_truncated=len(self.pool) > 4,
            verified_count=len(self.ranked or []),
            evidence_count=len(self.evidence),
        )
        self.trace["steps"].append(
            {
                "kind": "tool",
                "arguments": arguments,
                "result": result.model_dump(),
                "snapshot_ref": ref,
                "snapshot": snap,
                "duration_ms": (time.monotonic() - start) * 1000,
            }
        )
        return result

    def snapshot(self):
        return {
            "prefilter_exclusions": self.excluded,
            "geographic_records": getattr(self.geographic, "last_snapshot", []),
            "rows": [r.model_dump() for r in self.rows or []],
            "pool_ids": [r.listing_id for r in self.pool],
            "evidence": [e.model_dump() for e in self.evidence],
            "validation": {
                k: [v.model_dump() for v in vs] for k, vs in self.results.items()
            },
            "scores": {k: s.model_dump() for k, s in self.scores.items()},
            "ranking": self.ranked,
        }
