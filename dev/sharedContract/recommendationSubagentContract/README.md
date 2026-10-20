# Recommendation interface v1.0

This package contains requests, requirements, viewing-duration feedback, evidence, status/error fields, state/trace references and a generic response envelope. It imports independently of database, agent and recommendation implementation initialization.

`request.py` is the input boundary. `response.RecommendationResponse[T]` is the generic output envelope. The concrete item uses internal `ListingSummary` and `ScoreBreakdown` models and is bound in `dev.recommendationSubagent.models.candidate.Response`.

Language-independent consumers should validate against `schemas/request.schema.json` and `schemas/response.schema.json`. All nested definitions resolve inside those documents. Run `python -m dev.recommendationSubagent.schemas --check` to detect drift; omit `--check` to regenerate. Generated schemas use the pinned Pydantic 2.14 minor series.

Use a new request ID for each logical update, a stable conversation ID, a complete requirements snapshot, a monotonically increasing requirements version when changed, and the most recent `state.state_id`. Feedback-only requests retain the requirements version. Only listings from earlier verified recommendations in that conversation are valid feedback targets. Durations are simulated completed observations, not click counts.

Responses use `success`, `needs_clarification`, `no_match`, or `error`. Only success carries verified recommendations; unverified candidates are separate. Never treat an error as an empty successful search. `run_sequence` distinguishes feedback-only updates. The future orchestrator must discard responses superseded by its latest request.

Examples: [`examples/contracts`](../../recommendationSubagent/examples/contracts). Setup and semantics: [service README](../../recommendationSubagent/README.md).
