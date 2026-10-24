# Recommendation subagent: migration from native contract 1.0 to 2.0

Branch `feat/recommendation-exploration`, base commit `4d36507`. This file is for people who call the native
service, read its state database, or maintain the app-side adapter. It covers the changes from the
907cfa8-derived contract 1.0 (weighted-v1 scoring, ranked prefix of up to 4) to contract 2.0 (full-pool
fused-v2 scoring, select-v1 selection, up to 10 results). The authoritative definitions are the Python models
(`recommendation/contracts/*.py`, `recommendation/models/*.py`), the generated schemas
(`recommendation/contracts/schemas/*.json`, checked by `python -m recommendation.schemas --check`) and the strict
TS mirror (`src/contracts/recommendation-native.ts`). Shared fixtures live in `tests/fixtures/recommendation-v2/`.

## 0. Merge with main c7dc536 (2026-10-23) — supersedes the request side of sections 1–5

main added its own orchestrator→recommendation handoff ([docs/Recommendation_Contract_浏览时长与看房意向.md](../../docs/Recommendation_Contract_浏览时长与看房意向.md)).
Decisions (user, 2026-10-23): the **request follows main's contract and md**; the recommendation keeps **10 results**,
its **response contract 2.0** and its **`cold_start`** request field managed by the orchestrator. Sections 1–5 below
describe the branch's pre-merge request design (`FeedbackSnapshot` / `previous_round`); where they conflict, this
section wins.

### 0.1 Request (native `/recommend` and app `/app/recommend` accept the same body)

| Field | Meaning after the merge |
|---|---|
| `contract_version` | `"1.0"` (main's request version; default). A `"2.0"` request body is rejected with `UNSUPPORTED_VERSION` (HTTP 422). The **response** is still `"2.0"`. |
| `request_id`, `conversation_id`, `requirements_version`, `requirements`, `previous_state_id` | unchanged (app: `requirements_version` + 1, `native:` prefix stripped, `/app/recommend` rewrites `previous_state_id` to the native v2 head; legacy heads start a fresh chain). |
| `result_limit` | 1–10, `null` = `recommendation.default_result_limit` (10). The app adapter clamps to 10 (main clamped to 4) and the workflow asks for 10. |
| `feedback` | main's list of `ViewingEvent` (`event_id`, `conversation_id`, `recommendation_request_id`, `listing_id`, `event_type` `listing_view_duration`, `source` `sample`/`web_detail`, `occurred_at`, `payload` = `ViewPayload` {`view_session_id`, `duration_seconds`}). **Validated, not scored** (conversation match, unique `event_id`): the md says the cumulative `behavior_context` already holds the same time, so adding the events would double count. Recorded in the profile as `feedback_events`. |
| `behavior_context` | main's `BehaviorContext` {`version` "1.0", `as_of`, `listing_view_durations`, `book_viewing_listings`}, one summary per listing. **Scored natively** (section 0.2). Part of the request identity: a changed snapshot under the same request ID is a 409 / `IDEMPOTENCY_CONFLICT`. |
| `cold_start` | `ColdStartInput` {`exited`, `exited_pool_key`} — recommendation-owned extension. The orchestrator stores it in `session_domain_state.recommendation_cold_start` (migration `migrations/007-recommendation-cold-start.sql`) and updates it only when an adopted native response has `selection.cold_start.evaluated && exit_conditions_met`. Since 2026-10-24 (section 0.5) no new exits are recorded and native ignores the field for the decision; it is still accepted. |

`ListingViewDuration` = {`listing_id`, `duration_seconds` (session-cumulative, ≥ 0), `view_count`, `last_viewed_at`,
`source_request_ids` (UUIDs), `interest_eligible`}. `BookViewingListing` = {`listing_id`, `action_id`, `source_request_id`,
`requirements_version`, `starts_at`, `ends_at` (≤ 4 h), `time_zone` Asia/Singapore, `recorded_at`, `signal`
`viewing_draft_saved`, `appointment_confirmed` false}. The app sends numeric listing IDs; the native models accept any
native listing ID (1–128 chars) so fixture data mode works — a superset of every app body.

### 0.2 Scoring inputs (fused-v2 unchanged otherwise)

- **Browsing references** = `listing_view_durations` entries with `interest_eligible = true`
  (`ranking.behavior.browsing_requires_interest_eligible: true`, main's 15 s / 2 listings / 60 s gate) and
  `duration_seconds > 0`, inside the current pool. Weight `log1p(t / time_scale_seconds)`, uncapped. The time is
  **session-cumulative** (main's contract has no per-round split), so the plan's "previous round only" rule is replaced
  by main's semantics (user decision). Not-eligible and 0-second summaries are counted in the trace, not used.
- **Appointment references** = `book_viewing_listings` (saved timed viewing drafts) with a counted signal
  (`ranking.behavior.counted_appointment_signals: [viewing_draft_saved]`, replacing `counted_appointment_statuses`
  MANUAL_SENT/MANUAL_REPLIED/CONFIRMED). One entry per listing (duplicates are rejected by the contract), in pool only.
  It is a saved interest, not a confirmed appointment (`appointment_confirmed` is always false).
- Self pairs stay excluded; out-of-pool references are ignored and counted; missing branches contribute 0.
- Until 2026-10-24 the stability seed digested `behavior_context` (not `request_id`); mpd-v1 runs no perturbation trials at runtime (section 0.5).

### 0.3 Removed / replaced by the merge

| Removed | Replacement |
|---|---|
| `FeedbackSnapshot`, `PreviousRound` (`previous_round`), `BrowsingRecord`, `AppointmentSignal` request models | main's `feedback` events + `behavior_context` (`recommendation/contracts/feedback.py`, `recommendation/contracts/behavior.py`; `recommendation/app_contract.py` re-exports) |
| `src/domain/feedback-sidecar.ts`, `tests/feedback-sidecar-postgres.test.ts`, `tests/fixtures/recommendation-v2/sidecar-cases.json` | main's `src/domain/behavior-context.ts` (`collectBehaviorContext`) and `src/domain/cold-start.ts` (`coldStartAfterAdoption`) |
| `migrations/004-feedback-snapshot.sql` (number collided with main's `004-browser-interest.sql`) | main's 004–006; `007-recommendation-cold-start.sql` adds only `session_domain_state.recommendation_cold_start` |
| `counted_appointment_statuses` setting | `counted_appointment_signals`, `browsing_requires_interest_eligible` |
| main's `ApplicationRecommendationRequest.native_request()` stripping `behavior_context` | behavior_context is part of the native request; `native_request()` keeps it |
| main's `app_transport_requests` table | the branch's `app_transport_requests_v2` (same role; also covers `behavior_context`) |

### 0.4 Other merge adaptations

- `config/recommendation-full.json` (main: `limits.timeout_seconds` 300) is now an **overlay** of the committed YAML:
  `{"extends": "committed", "overrides": {"controller": {"timeout_seconds": 300}}}` — still a single parameter
  source; `.env.example` keeps `RECOMMENDATION_CONFIG=config/recommendation-full.json` and the 450 s HTTP wait.
- `recommendation/app_controller.py` (main's output protocol) keeps the single-JSON normalisation and audit, but its
  contract text and run state now refer to the code-made selection (`selected_ids`, `current_selected_ids`;
  `app-recommendation-output-v2`), not to "four tools" or a ranked prefix.
- Main's readers of the old score names (`src/domain/behavior.ts`, `src/agents/explanation/evidence.ts`,
  `frontend/native-ranking.js`) read `behavior_score` / `soft_weight` with a fallback to archived `implicit_*` /
  `explicit_*` (`statedWeight`, `interestWeight`, `interestScore` in `src/contracts/recommendation-native.ts`).
- `contracts/releases/recommendation-app-request-1.0.schema.json` was regenerated from the merged model (adds
  `cold_start`, `result_limit` ≤ 10, native listing IDs). main's `recommendation-web-feedback-extension.json` is kept;
  its protected kernels now change only through declared downstream patches (tests/test_browser_interest_contract.py).
- The shared fixtures (`tests/fixtures/recommendation-v2/*.json`) were regenerated in the main request shape by
  `evaluation/recommendation-exploration-2026-10-23/merge/gen_fixtures.py`.

### 0.5 Cold-start detector mpd-v1 (2026-10-24; supersedes the cold-start parts of sections 0–5)

- **Decision** (user): cold start measures score differentiation directly. D = 2/(n(n−1)) · Σ_{i<j}|s_i − s_j|
  (Gini's mean difference) over the official combined scores of the overall top
  `cold_start.detector.candidate_window` (30) eligible candidates in O, leaders included, all when fewer, no
  per-list rescaling. Active when D < `cold_start.detector.min_mean_pairwise_difference` (0.01, a starting
  value, not calibrated); D equal to it is inactive; exact float comparison. D is a practical score tolerance,
  not a significance test, and does not show that the requirements are sufficient or the ranking correct.
- **Short circuits kept**: disabled → `cold_start_disabled`; empty pool / pool ≤ result_limit → not evaluated
  (`empty_pool` / `small_pool`); fewer than two scores → not evaluated (`insufficient_scores`). Evaluated reasons:
  `mean_difference_below_threshold` / `mean_difference_at_or_above_threshold`.
- **Request**: `cold_start` {`exited`, `exited_pool_key`} is still accepted (same validation) but never bypasses the
  check; the trace records it with `request_cold_start_used: false`. `coldStartAfterAdoption` records no new exits;
  `session_domain_state.recommendation_cold_start` (`007-recommendation-cold-start.sql`) and its rows are kept.
- **Response** (contract_version stays 2.0): `selection.cold_start` is a union. New runs:
  {`evaluated`, `active`, `reason`, `detector` "mean_pairwise_difference", `detector_version` "mpd-v1",
  `candidate_window`, `evaluated_count`, `mean_pairwise_difference`, `threshold`, `pool_key`} (`detector` always
  present). Stored responses keep the legacy {`coverage_c`, `boundary_group_b`, `stability_s`, `thresholds`,
  `exit_conditions_met`} shape; both Python and the TS mirror accept it as stored and never reinterpret it as D.
- **Settings**: removed `cold_start.reenter_on_pool_rebuild`, `min_evidence_coverage`, `max_boundary_tie_group_size`,
  `min_top_k_stability`, `stability.*` (the loader rejects them by name); added `cold_start.detector.*` (required when
  enabled). The weight-perturbation functions moved to `recommendation/analysis/stability.py` (calibration only).
- **Unchanged**: select-v1 (tie group G, leaders, near-tie sampling, price bins, stage A/B sampling), seed formula,
  ranking formulas, result limits, eligibility and `selection_input` (replay reads only `cold_start_active`, so stored
  runs replay as recorded). `cold_start` is part of `selection_hash`, so new-request seeds and `scoring_version` change.

## 1. Summary of breaking changes

| Area | 1.0 | 2.0 |
|---|---|---|
| Version | `contract_version: "1.0"` | `"2.0"`. A 1.0 body is rejected at validation with `UNSUPPORTED_VERSION: native contract 2.0 required` (HTTP 422 at the transport). |
| Result count | `result_limit` 1–4, default 4 | 1–10, `null` means `recommendation.default_result_limit` (10). Fewer when the pool is smaller. |
| Feedback | list of `ViewingEvent`s, accumulated natively across requests | one complete `FeedbackSnapshot` per request (previous round + all appointment signals). Nothing is accumulated natively. |
| Selection | ranked prefix of the score order | select-v1: leaders kept, near-tie group sampled uniformly without replacement, price-stratified exploration only during cold start |
| Pool | every request re-read all RENT rows; up to 4 unverified candidates returned | exploration pool = listings that pass all hard criteria; cached per conversation; hard-unknown listings are excluded and only counted |
| Unverified | `unverified_candidates` (max 4) | removed from the native response. The app's V1 contract keeps its own unverified group (section 8). |
| Scores | explicit/implicit, 0.8/0.2 with redistribution | soft/behaviour, 0.60/0.40, behaviour = 0.60 appointments + 0.40 browsing, missing branches contribute 0 (no redistribution) |
| Geography | Neo4j `NEAR_PLACE` edges with a 1 m tolerance check; OSM places only | native catalog (LTA stations + exits, URA areas/regions, OSM places), Python haversine, entity resolution reported per criterion |

## 2. Request, field by field

| Field | 1.0 | 2.0 |
|---|---|---|
| `contract_version` | `"1.0"` | `"2.0"` (default; any other value is rejected) |
| `request_id`, `conversation_id` | 1–128 chars | unchanged |
| `requirements_version` | ≥ 1 | unchanged (the app maps its own version + 1) |
| `requirements.listing_type`, `requirements.criteria[]` | | unchanged, except one criterion rule: a `distance`/`commute` criterion now accepts a `category` alone, or a `destination_id` and/or a `destination_name` together (1.0 required exactly one of the three). An ID and a name are both resolved: an invalid or unsupported-namespace ID falls back to the name (reason recorded); an ID and a name that resolve to different entities are `ambiguous` (`id_name_conflict`). |
| `previous_state_id` | latest native state | unchanged (must match the conversation's v2 head, else `STALE_REFERENCE`) |
| `result_limit` | int, default 4, max 4 | `int | null`, default `null` → config default 10, max 10 (`MAX_RESULT_LIMIT`, equal to `recommendation.max_result_limit`, cross-layer tested) |
| `feedback` | `list[ViewingEvent]` (`event_id`, `conversation_id`, `recommendation_request_id`, `listing_id`, `event_type`, `source`, `occurred_at`, `payload{view_session_id, duration_seconds}`) | `FeedbackSnapshot` object (section 4); default `{previous_round: null, appointments: []}` |
| `cold_start` | – | new `ColdStartInput {exited: bool = false, exited_pool_key: str | null}`; `exited_pool_key` is required exactly when `exited` is true. Managed by the orchestrator. Since 0.5: accepted for compatibility, ignored by the cold-start decision. |

## 3. Response, field by field

| Field | 1.0 | 2.0 |
|---|---|---|
| `contract_version` | `"1.0"` | `"2.0"` |
| `status` | success / needs_clarification / no_match / error | same values. Success iff recommendations are non-empty. `needs_clarification` only for criterion-level problems (a hard criterion that is unsupported, unresolved or ambiguous, R8). An empty pool caused by unknown data (for example missing coordinates) is `no_match`, with counts in `pool` and a warning. |
| `recommendations` | max 4 | max 10 (and ≤ `selection.result_limit`, ≤ `pool.size`) |
| `recommendations[].rank` | 1–4, ranking order | 1–10, **display** order (leaders in score order, then the drawn items in score order) |
| `recommendations[].listing`, `criterion_results`, `evidence_refs`, `strengths`, `trade_offs`, `unknowns` | | unchanged |
| `recommendations[].score_rank` | – | new: 1-based position in the full-pool score order O (score descending, then listing_id) |
| `recommendations[].selection_reason` | – | new: `ranked` / `leader` / `tie_group_sample` / `price_exploration_sample` / `small_pool` |
| `recommendations[].selection_note` | – | new: short human text for the reason |
| `recommendations[].scores` | `explicit_score`, `implicit_score`, `explicit_weight`, `implicit_weight`, `evidence_coverage` | renamed and extended (section 3.1) |
| `unverified_candidates` | max 4 (ID + criterion results) | **removed**. Hard-unknown listings are excluded from the pool and counted in `pool.excluded_hard_unknown` / `pool.unknown_reasons`. |
| `evidence` | evidence of the pool | only evidence referenced by the returned items. One entry per **(evidence_id, criterion_id)**: R4 evidence IDs carry no criterion ID, so two criteria with the same canonical key (for example a hard and a soft distance to the same target) share an `evidence_id`, and each of them gets its own entry. An `evidence_id` can therefore appear more than once, each time with a different `criterion_id`. A reference in `criterion_results` always resolves to the entry carrying that result's `criterion_id`. Unreferenced evidence is rejected by the model validator. |
| `resolution` | – | new: one `ResolutionResult` per geographic criterion: `criterion_id`, `field`, `original{destination_id, destination_name, category}`, `status` (resolved / ambiguous / unresolved / not_applicable), `entity_id`, `entity_kind` (lta_station / ura_area / ura_region / osm_place / category), `method`, `reason`, `candidates` (≤ `entity_resolution.max_candidates`) |
| `pool` | – | new `PoolSummary`: `pool_key`, `pool_version`, `data_version`, `cache` (built / reused / bypassed), `size`, `source_count`, `excluded_hard_fail`, `excluded_hard_unknown`, `unknown_reasons` (≤ 10 keys + "other"). Invariant: `source_count = size + excluded_hard_fail + excluded_hard_unknown`. |
| `selection` | – | new `SelectionSummary`: `mode` (ranked / near_tie_sampling / cold_start_exploration / small_pool / empty), `algorithm_version` (`select-v1`), `seed` (64 hex), `result_limit`, `leaders`, `tie_group{size, min_score, max_score, span, epsilon}`, `exploration{candidate_count, sample_size, bins[{lower, upper, count, sampled, quota}]}`, `cold_start` = mpd-v1 `{evaluated, active, reason, detector, detector_version, candidate_window, evaluated_count, mean_pairwise_difference, threshold, pool_key}` (section 0.5) or, in stored historical records only, the legacy `{evaluated, active, reason, coverage_c, boundary_group_b, stability_s, thresholds{c, b, s}, exit_conditions_met, pool_key}` |
| `clarifications`, `relaxation_suggestions`, `warnings` | | unchanged (strings) |
| `state` | `state_id`, `candidate_pool_ref`, `run_sequence`, `feedback_version`, `scope`, `truncated`, `source_count`, `prefilter_excluded`, `assessed_count` | `feedback_version` **removed** (no accumulated events); the rest unchanged |
| `execution` | `trace_id`, `trace_ref`, `strategy`, `scoring_version` (`weighted-v1`), `model_identifier`, `model_calls`, `tool_calls`, `duration_ms`, `tokens`, `data_mode`, `controller_mode` | all kept; adds `selection_algorithm` (`select-v1`), `config_version`, `config_hash`, `selection_hash`; `scoring_version` is `fused-v2:<selection_hash[:12]>` |
| `error` | | unchanged |

### 3.1 ScoreBreakdown and Contribution

| 1.0 | 2.0 |
|---|---|
| `explicit_score` | `soft_score` (null without soft criteria; contributes 0) |
| `implicit_score` | `behavior_score` = `appointment_weight · (appointment_score or 0) + browsing_weight · (browsing_score or 0)`, null when both branches are unavailable |
| – | `appointment_score`, `browsing_score` (new branches, null when unavailable) |
| `combined_score` | `soft_weight · (soft or 0) + behavior_weight · (behavior or 0)`, clamped to [0, 1]; never redistributed |
| `explicit_weight`, `implicit_weight` | `soft_weight`, `behavior_weight` (the configured 0.60 / 0.40, not the 1.0 redistribution) |
| – | `appointment_weight`, `browsing_weight` |
| `evidence_coverage` | `soft_evidence_coverage` (sum of normalised soft weights with a known utility) |
| – | `comparison_coverage` (reference-weighted mean pair coverage, null when no pair is available) |
| `contributions[]` (`feature`, `reference`, `raw_difference`, `scale`, `similarity`, `weight`, `contribution`, `unknown`) | unchanged fields; adds `branch` (soft / appointment / browsing) and `method` (for example `district_fallback`) |
| `suppressed_features` | now only features constrained by a **hard** criterion (`similarity.suppression: hard_criteria`; 1.0 suppressed for any criterion) |
| `ordering` | still a string label |

Behaviour similarity in 2.0: features price, property_type, bedrooms, floor_area_sqft, geography; numeric spreads fitted
on the whole pool (`p05_p95`); self pairs excluded; references only inside the current pool; a single browsing
reference cancels the absolute duration. Full rules: spec section 7, `recommendation/ranking/scoring_v2.py`.

## 4. Feedback snapshot semantics

Every request carries the orchestrator's complete snapshot. The native side never stores or merges feedback
between requests, so an older round's browsing has no effect.

- `previous_round` (or null): `request_id` of the previous round the user saw, `shown_listing_ids` (1–10, unique)
  and `browsing` with **exactly one** record per shown ID (no extras, no duplicates). A record is
  `{status: "recorded", duration_seconds ≥ 0}` (0 = shown but not browsed) or `{status: "missing", duration_seconds: null}`.
- Service checks (`recommendation/context/manager.py`): `previous_round.request_id` must be a committed v2 run of this
  conversation, and the ID **set** of its response must equal `shown_listing_ids`; otherwise `INVALID_FEEDBACK`. It need
  not be the native head (the orchestrator may have adopted an earlier round).
- `appointments`: one entry per listing, `{listing_id, statuses[], action_ids[]}` (non-empty, unique). Duplicate listings are
  rejected. Statuses outside `ranking.behavior.counted_appointment_statuses` (MANUAL_SENT, MANUAL_REPLIED, CONFIRMED) are
  rejected with `INVALID_FEEDBACK`. A status upgrade adds nothing (one reference per listing).
- Scoring uses browsing references with `recorded` and duration > 0, weight `log1p(t / time_scale_seconds)` (uncapped),
  and appointment references, both only when the listing is in the current pool. Out-of-pool, missing and 0-second
  records are counted in the rank_candidates trace step.
- Idempotency and checks are unchanged: same request_id + same body → stored response (replay); same request_id +
  different body → rejected; `STALE_REFERENCE`, `STALE_REQUIREMENTS` and `run_sequence` as before.
- Save, shortlist and attribute feedback are ignored in this version.

## 5. Sidecar construction (orchestrator, `src/domain/feedback-sidecar.ts`) — retired by the merge (section 0)

The app builds the snapshot ("sidecar") once, inside the agent-A apply transaction, stores it in
`recommendation_calls.feedback_snapshot` and in agent B's queued body, and never rebuilds it on retry, so a retried
call sends an identical body. `nativeRequest(req, sidecar)` is a pure function. Cases (fixture
`tests/fixtures/recommendation-v2/sidecar-cases.json`, all yield a valid body and never `INVALID_FEEDBACK`):

| Previous adopted round | `previous_round` |
|---|---|
| none (first search) | null |
| native contract 2.0 round with a non-empty native ranking | `request_id` = native response request_id (same as the V1 request id), `shown_listing_ids` = native ranking, browsing = summed sample `active_view` durations of that round per shown ID (recorded), else missing |
| native round where hardGap moved the items into the V1 unverified group | same as above: shown IDs are the native ranking, because the user still saw them |
| baseline round (no native block) | null |
| legacy contract-1.0 native round (no `native.contract: "2.0"`) | null |
| native round with an empty ranking | null |

Appointments: all VIEWING actions of the session with a counted status, one entry per listing (statuses and action
IDs sorted). Cold-start state: `session_domain_state.recommendation_cold_start` (migration 004) is set to
`{exited: true, exited_pool_key}` only in the B-apply transaction of an **adopted** native response whose
`selection.cold_start.evaluated && exit_conditions_met`. Re-entry after a pool rebuild (new pool_key) is decided
natively (`cold_start.reenter_on_pool_rebuild`).

Conversions (round-trip tested): native `requirements_version` = V1 + 1; `previous_state_id` strips `native:`;
`app.py /app/recommend` rewrites `previous_state_id` to the native v2 head when no cached run exists; a state ID that
resolves only to a legacy v1 run (or nothing) becomes null, so the request starts a fresh v2 chain instead of failing.

## 6. State database (SQLite, `RECOMMENDATION_STATE_PATH`)

| Table | Content |
|---|---|
| `request_identities_v2(conversation_id, request_id, payload_hash)` | idempotency binding over the whole 2.0 body |
| `conversations_v2(id, state_id, sequence)` | v2 head per conversation; `store.current()` reads it by state id directly (no snapshot scan) |
| `runs_v2(conversation_id, request_id, state_id, sequence, payload_hash, response, snapshot)` | committed runs. The snapshot is compact (`format: 2`): settings + `config_hash` / `selection_hash`, request, `pool{pool_key, pool_version, data_version, cache}`, `selection_input` (R6: head `[[id, score, price]]`, K, epsilon, seeds, bin edges, …) and resolution. No pool rows. |
| `traces(conversation_id, trace_id, request_id, content)` | shared with 1.0. v2 content carries `"format": 2` (slim traces also `"layout": "slim-v1"`); legacy rows lack it. |
| `pool_cache(conversation_id, pool_version, pool_key, data_version, created_at, used_seq, content)` | pool content by version (retention `cache.pool_retention_per_conversation`) |
| `pool_cache_index(conversation_id, pool_key, data_version, pool_version, created_at, used_seq)` | reusable entries (`cache.max_entries_per_conversation`, newest built kept) |
| `app_transport_requests_v2(conversation_id, request_id, input_hash, request)` | app.py transport binding (inbound body vs the body actually run) |

Legacy handling: the 1.0 tables (`request_identities`, `conversations`, `runs`) are untouched, never written and still
readable. A legacy v1 head is ignored by v2: the first 2.0 request of such a conversation starts a fresh v2 chain.
`terminal show-trace` and `StateStore.resolve_reference` print a "legacy v1 trace" note for legacy rows. An old
`state.sqlite` is not deleted or migrated in place. `<trace>/pool` resolves from `pool_cache` while the content is
retained, else `STALE_REFERENCE` ("pool evicted" / "not persisted").

## 7. Configuration

One production source: `recommendation/settings/recommendation.yaml` (loader `recommendation/settings/loader.py`, public
name `recommendation.config.RecommendationConfig`). `RECOMMENDATION_CONFIG` / `--config` name an alternative file of the
same schema for tests and experiments; `RECOMMENDATION_TRACE_MODE` toggles only `trace.mode`. The old hard-coded
`DEFAULT_*` dictionaries, `RunLimits` and the weighted-v1 constants are gone. `recommendation/examples/config.json` is an
experiment file (identical except `cold_start.enabled: false`). Each value's source and evidence: TUNING.md. A request
runs on a frozen copy; a new request is needed to see new settings; replaying an old request returns its stored
response. Changing a result-affecting section changes `selection_hash` and therefore the selection seeds.

## 8. What stays

- The app's V1 contract (`src/contracts/recommendation-v1.ts`, `contracts/releases/recommendation-*-1.0.schema.json`) keeps
  `unverified_candidates`: the baseline adapter, the hardGap presentation guard (native items move to the V1 unverified
  group when an app-only hard criterion cannot be expressed natively) and pending-listing inquiries use it. Its native
  block gains optional `contract`, `pool`, `selection`, `resolution`, `config_hash` and `selection_reasons`; archived
  contract-1.0 projections still parse (`legacyNativeScoresSchema`, optional fields, `?.` / `|| []` readers).
- V1 score origins `stated_preference` / `inferred_interest` are kept.
- The baseline adapter is unchanged except the shared caps (`result_limit` 10).

## 9. Removed in native 2.0

- `unverified_candidates` and the `Unverified` model.
- `state.feedback_version`, `ViewingEvent`, natively accumulated feedback and the interest profile built from it.
- `ranking/legacy.py` (weighted-v1): 0.8/0.2 fusion with redistribution, the 120 s cap, fixed numeric scales, the
  missing price feature and all-criteria suppression.
- The `NATIVE_GEOGRAPHY_GAP` warning in the adapter (native resolves LTA/URA itself). The frontend still renders it for
  archived exchanges.

## 10. Upstream provenance

The 907cfa8 manifest (`contracts/releases/recommendation-upstream-907cfa8.json`) is unchanged. Every manifest-tracked file
changed on this branch is declared in `contracts/releases/recommendation-downstream-patches.json` (target,
`base_ported_sha256` from the manifest, `patched_sha256` of the final LF bytes, issue, changes), and
`scripts/import-recommendation-upstream.py --check` verifies both. `scripts/compare-recommendation-upstream.py` is retired
as 907cfa8-baseline-only evidence: by default it exits 2 with a retirement notice, and
`evaluation/reports/native-upstream-parity.json` is kept as the historical record and not regenerated.
`evaluation/acceptance-history/EVIDENCE_MANIFEST.json` (publication 2026-10-22) records the patch file as byte-preserved
at that publication; that historical record is not edited, and the archived original stays in the acceptance zips.

## 11. Rollout order

The app now sends 2.0 bodies, and a pre-2.0 native service rejects them (and the reverse). Deploy them together:

1. Restart the native service (8092) on this tree. This needs the user's OK; the P7 smoke ran a separate instance on
   port 8192 instead.
2. Restart the app (8010). `DomainStore.setup` applies main's `migrations/004`–`006` and
   `migrations/007-recommendation-cold-start.sql` (`session_domain_state.recommendation_cold_start`; section 0).
3. Keep `data/.runtime/recommendation/state.sqlite`. Legacy rows stay readable; new requests start v2 chains.
