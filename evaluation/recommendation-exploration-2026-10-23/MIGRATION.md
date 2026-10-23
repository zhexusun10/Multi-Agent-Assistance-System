# Recommendation subagent: migration from native contract 1.0 to 2.0

Branch `feat/recommendation-exploration`, base commit `4d36507`. This file is for people who call the native
service, read its state database, or maintain the app-side adapter. It covers the changes from the
907cfa8-derived contract 1.0 (weighted-v1 scoring, ranked prefix of up to 4) to contract 2.0 (full-pool
fused-v2 scoring, select-v1 selection, up to 10 results). The authoritative definitions are the Python models
(`recommendation/contracts/*.py`, `recommendation/models/*.py`), the generated schemas
(`recommendation/contracts/schemas/*.json`, checked by `python -m recommendation.schemas --check`) and the strict
TS mirror (`src/contracts/recommendation-native.ts`). Shared fixtures live in `tests/fixtures/recommendation-v2/`.

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
| `cold_start` | – | new `ColdStartInput {exited: bool = false, exited_pool_key: str | null}`; `exited_pool_key` is required exactly when `exited` is true. Managed by the orchestrator. |

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
| `selection` | – | new `SelectionSummary`: `mode` (ranked / near_tie_sampling / cold_start_exploration / small_pool / empty), `algorithm_version` (`select-v1`), `seed` (64 hex), `result_limit`, `leaders`, `tie_group{size, min_score, max_score, span, epsilon}`, `exploration{candidate_count, sample_size, bins[{lower, upper, count, sampled, quota}]}`, `cold_start{evaluated, active, reason, coverage_c, boundary_group_b, stability_s, thresholds{c, b, s}, exit_conditions_met, pool_key}` |
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

## 5. Sidecar construction (orchestrator, `src/domain/feedback-sidecar.ts`)

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
2. Restart the app (8010). `DomainStore.setup` applies `migrations/004-feedback-snapshot.sql` (`feedback_snapshot`,
   `recommendation_cold_start`, indexes).
3. Keep `data/.runtime/recommendation/state.sqlite`. Legacy rows stay readable; new requests start v2 chains.
