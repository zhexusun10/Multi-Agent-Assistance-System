# Recommendation v2 migration inventory

Created in phase P1 (spec section 13, Step 1) on branch `feat/recommendation-exploration`, base
commit `4d36507`. It lists every place that a later phase has to change: the 4-result limit,
ranking-prefix assertions, consumers of run snapshots and traces, explicit/implicit names, legacy
scoring constants and settings keys that are not used yet. Each entry gets marked resolved in the
phase that changes it. P7 re-runs the grep commands at the end of this file and must find
nothing open.

Status values: **resolved-P1** = done in this phase · **open-Pn** = planned for phase n ·
**keep** = intentionally unchanged (with the reason) · **retired-P7** = kept only as historical evidence.

**P7 final state:** no entry is open. The re-run greps were executed over tracked and untracked files
(`p7/inventory_check.sh`, output `p7/inventory-grep.txt`); every remaining hit is classified in section F.

Line numbers were re-read after the P1 edits.

## C. Settings keys: who reads them after P1

There is one source: `recommendation/settings/recommendation.yaml`, loaded by
`recommendation/settings/loader.py` and exported under the public name
`recommendation.config.RecommendationConfig`.

The keys marked open below are validated but **not in effect**. Each one (or its section)
carries a `[not in effect until Pn ...]` marker in the YAML, is listed in
`recommendation/settings/pending.py` (`NOT_IN_EFFECT`), and is recorded in every trace under
`context.scoring_policy.settings_not_in_effect`. `test_not_in_effect_markers_match_pending_table`
keeps the YAML markers and the table identical. The phase that wires a key removes both.

| Key | Read in P1 by | Status |
|---|---|---|
| `ranking.soft.numeric_bounds.*` | `ranking/ranker.py:76` (minimize/maximize utility) | resolved-P1 |
| `ranking.similarity.feature_weights.{property_type,bedrooms,floor_area_sqft,geography}` | `ranking/legacy.py similarity_policy` rescales them to sum 1 and rounds to 12 decimals (`WEIGHT_DECIMALS`), which gives back exactly 0.25/0.20/0.25/0.30; `pairwise()` then renormalises bit-identically for every suppression subset (`test_weighted_v1_score_parity_all_pairs_and_suppression_sets`) | resolved-P1 |
| `ranking.similarity.feature_weights.price` | not read: weighted-v1 has no price feature (L4) | resolved-P5 (`ranking/scoring_v2.py`: price is a similarity feature, spread fitted on the pool) |
| `ranking.similarity.geographic_scale_m` | `ranking/legacy.py similarity_policy` (geography scale) | resolved-P1 |
| `ranking.behavior_weight` (only whether it is > 0) | `ranking/legacy.py similarity_policy`: 0 disables the implicit branch | resolved-P1 (full weight use: resolved-P5, `ranking/scoring_v2.py combine`) |
| `controller.max_model_turns` | `agent/controller.py:124` | resolved-P1 |
| `controller.max_tool_calls` | `tools/dispatcher.py:52` | resolved-P1 |
| `controller.timeout_seconds` | `tools/dispatcher.py:45`, `agent/controller.py:126` (`LiveController(timeout=None)` no longer keeps its own 90) | resolved-P1 |
| `controller.repairs` | `service.py:132,147` | resolved-P1 |
| `controller.model_result_candidates` | `tools/dispatcher.py:182-190` (validated `>= recommendation.max_result_limit`) | resolved-P1 |
| whole model (`config_hash`, `selection_hash`, `config_version`, source) | `service.py` trace + run snapshot (`identity()`); `scoring_version = weighted-v1:<selection_hash[:12]>` (P5: `fused-v2:<selection_hash[:12]>`) | resolved-P1 |
| `recommendation.default_result_limit`, `max_result_limit` | `service.py:110` (`request.result_limit or default_result_limit`); contract maximum 10 = `loader.RESULT_LIMIT_CEILING` (derived from `contracts/limits.py`) | resolved-P2 (cross-layer tests: `test_contract_max_equals_config_max`, TS `generated Python examples parse ... max_result_limit == schema maximum`) |
| `ranking.soft_weight`, `ranking.behavior_weight` (values), `ranking.behavior.*` | validated only (weighted-v1 uses L1) | resolved-P5 (`ranking/scoring_v2.py`: fusion, branch weights, in-pool references; missing branch 0 / no redistribution are loader-enforced and implemented by `combine`); `ranking.behavior.counted_appointment_statuses` resolved-P2 (`context/manager.py:41` rejects other statuses with INVALID_FEEDBACK; TS constant cross-checked against the YAML) |
| `ranking.browsing.*` | validated only (weighted-v1 uses L2) | resolved-P5 (`scoring_v2.browsing_weight`: log1p(t / time_scale_seconds), cap only when set) |
| `ranking.similarity.suppression`, `numeric_scaling`, `geography_district_fallback`, `min_comparison_coverage` | validated only (weighted-v1 uses L3/L5) | resolved-P5 (`ranking/scoring_v2.py`, scalar reference `ranking/ranker.py`) |
| `geography.station_distance_method` | `geography/evidence.py measure` (via `GeographicBase.evidence(settings=...)`; `nearest_exit` or `exit_centroid`); part of pool_key through `pool_builder.geography_digest` | resolved-P4 |
| `entity_resolution.max_candidates` | `geography/resolver.py resolve` (via `GeographicBase.resolve_criteria`, called by `Dispatcher.resolve_entities` / `build_pool`) | resolved-P4 |
| `entity_resolution.max_search_attempts`, `controller.max_resolution_tool_calls` | `tools/geo_tools.py search_places` (per-criterion attempts), `tools/dispatcher.py call` (query/bind calls, inside `max_tool_calls`) | resolved-P4 (part 2) |
| `cold_start.*`, `selection.*` | validated only | resolved-P5 part 2 (`ranking/selection.py`: diagnostics C/B/S, thresholds, `enabled`, `reenter_on_pool_rebuild`, stability trials/fraction, tie group epsilon, seed namespace; `ranking/exploration.py`: price bins/target; YAML markers and `pending.py` entries removed) |
| `cache.*` | validated only | resolved-P3 (`state/pool_cache.py`, `tools/pool_builder.py`: `enabled`, `ttl_seconds`, `max_entries_per_conversation`, `pool_retention_per_conversation`, `memory_max_entries`; YAML marker and `pending.py` entry removed) |
| `trace.*` (and the `RECOMMENDATION_TRACE_MODE` toggle) | validated only; the env toggle is implemented in the loader | resolved-P6 part 1 (`trace.mode` / `debug_max_rows` / `preview_rows` read by `recommendation/tracing/slim.py` + `stages.py`; pending entries and YAML markers removed) |

Open question for P3: R2/R13 add `cache.pool_retention_per_conversation` and
`cache.memory_max_entries`, while section 2/5 also list `cache.max_entries_per_conversation`.
P1 keeps all three, with the check pool_retention ≥ max_entries. P3 must decide whether
max_entries (cache index rows) and pool_retention (pool contents) stay separate or merge.
**Decided in P3: they stay separate.** `max_entries_per_conversation` bounds the index of
reusable (pool_key, data_version) entries; `pool_retention_per_conversation` bounds the pool
contents kept for `<trace>/pool` of older runs. The loader keeps retention ≥ max_entries, and
eviction never removes content an index entry still points to (`state/pool_cache.py`
docstring, VALIDATION.md P3). Eviction order (P3 review): the index keeps the newest BUILT
entries (spec section 5; a hit does not refresh an entry), content retention keeps the most
recently USED contents (a hit counts), pinned by `test_index_keeps_the_newest_built_entries`.

## L. Legacy weighted-v1 constants (one place: `recommendation/ranking/legacy.py`)

**Resolved in P5 part 1:** `recommendation/ranking/legacy.py` is deleted; the two parity tests
below were removed (the 4d36507 oracle files stay as historical evidence, no longer asserted).
The transitional P2 mapping ("L7": browsing = whole behaviour branch, appointments no
contribution) went with it.

These constants override YAML keys that hold the v2 values (the overridden keys are marked
not in effect, section C). They keep weighted-v1 scores bit-identical to commit 4d36507 in P1:
`test_weighted_v1_scores_identical_to_baseline_fixtures` replays the four baseline rounds, and
`test_weighted_v1_score_parity_all_pairs_and_suppression_sets` compares `score()` over 12×12
fixture pairs × 9 criteria sets (every suppression subset that weighted-v1 can produce from
them, including none and all) × 5 viewing patterns against `baseline/score-parity.json`
(generated from the 4d36507 tree by `baseline/capture_scores.py`).

| Id | Constant | Why the YAML cannot hold it | Status |
|---|---|---|---|
| L1 | `EXPLICIT_WEIGHT = 0.8`, `IMPLICIT_WEIGHT = 0.2`, plus redistribution to 1.0 when one signal is missing | v2 fuses at `soft_weight` 0.60 / `behavior_weight` 0.40 and forbids redistribution | resolved-P5 (module deleted; fused-v2 in `ranking/scoring_v2.py`) |
| L2 | `DURATION_CAP_SECONDS = 120`, weight `log(1+min(t,120))/log(121)` (also `context/manager.py:81-83` profile) | v2 is `browsing.duration_cap_seconds: null` (uncapped) with `log1p(t/time_scale_seconds)` | resolved-P5 (module deleted; fused-v2 in `ranking/scoring_v2.py`) |
| L3 | `NUMERIC_SCALES = {bedrooms: 3.0, floor_area_sqft: 500.0}` | v2 fits the spread on the pool (`similarity.numeric_scaling`) | resolved-P5 (module deleted; fused-v2 in `ranking/scoring_v2.py`) |
| L4 | `FEATURES` excludes price | v2 includes price (decision 2) | resolved-P5 (module deleted; fused-v2 in `ranking/scoring_v2.py`) |
| L5 | `SUPPRESSION = "all_criteria"` | v2 is `similarity.suppression: hard_criteria` (decision 1); the mode logic is already in `ranking/suppression.py` | resolved-P5 (module deleted; fused-v2 in `ranking/scoring_v2.py`) |
| L6 | `WEIGHT_DECIMALS = 12` (rescale + round of the four weighted-v1 feature weights) | exists only so the 0.8-scaled YAML weights reproduce the old floats exactly | resolved-P5 (module deleted; fused-v2 in `ranking/scoring_v2.py`) |

## R. Removed duplicate hard-coded values (resolved-P1)

| Was | Now |
|---|---|
| `config.py` `DEFAULT_SCALES` / `DEFAULT_WEIGHTS` / `DEFAULT_BOUNDS`, `ScoringPolicy`, `RunLimits` (defaults 8/20/90/100/2) | deleted; `config.py` is a thin re-export of the loader |
| `ranking/ranker.py` module-level `SCALES` / `WEIGHTS` / `BOUNDS`, inline `(0.8, 0.2)`, `min(120, d)`, `log(121)` | settings + `legacy.py` |
| `context/manager.py` `min(v, 120)`, `log(121)`, `"duration_cap_seconds": 120`, `"weighted-v1"` | `legacy.duration_weight`, `legacy.DURATION_CAP_SECONDS`, `legacy.SCORING_VERSION` |
| `agent/controller.py` `LiveController(timeout=90)` | `timeout=None`; only `controller.timeout_seconds` applies |
| `examples/config.json` (old weighted-v1 JSON) | v2-schema experiment file (identical except `cold_start.enabled: false`) |
| `terminal/acceptance.py --config` without an env fallback | defaults to `RECOMMENDATION_CONFIG` |

Proof: `test_no_duplicate_hard_coded_parameters_outside_settings` greps every non-test `.py`
file under `recommendation/` (P5: no exemption left; `ranking/legacy.py` no longer exists).

## A. The 4-result limit (and the app's 5)

| Location | What | Status |
|---|---|---|
| `recommendation/contracts/request.py:15` | `result_limit default=4, le=4` | resolved-P2 (`result_limit: int|None`, None → `recommendation.default_result_limit`, `le=MAX_RESULT_LIMIT`=10) |
| `recommendation/contracts/response.py:47` | `recommendations max_length=4` | resolved-P2 (`max_length=MAX_RESULT_LIMIT`) |
| `recommendation/contracts/response.py:48` | `unverified_candidates max_length=4` (removed from native 2.0) | resolved-P2 (field removed from native 2.0) |
| `recommendation/models/candidate.py:13` | `rank le=4` | resolved-P2 (`le=MAX_RESULT_LIMIT`) |
| `recommendation/agent/controller.py:34` | `Proposal.listing_ids max_length=4` | resolved-P2 (`max_length=MAX_RESULT_LIMIT`) |
| `recommendation/models/tool_results.py:41` | `previews max_length=4` (becomes `trace.preview_rows`) | resolved-P6 part 1 (dispatcher previews ≤ `trace.preview_rows`; the trace's compact result also cuts ID lists to it) |
| `recommendation/tools/dispatcher.py:171,210` | `preview_ids[:4]`, `previews_truncated len(pool) > 4` | resolved-P6 part 1 (`trace.preview_rows`) |
| `recommendation/service.py:231` | `unverified[:4]` | resolved-P2 (no native unverified list) |
| `recommendation/contracts/schemas/request.schema.json:328`; `response.schema.json:545,806,842` | generated `maximum`/`maxItems` 4 | resolved-P2 (regenerated; `--check` exit 0) |
| `recommendation/examples/contracts/*.json` | generated with result_limit 4 and 4 unverified | resolved-P2 (regenerated as contract 2.0; parsed by the strict TS mirror in `tests/recommendation-v2-fixtures.test.ts`) |
| `src/contracts/recommendation-native.ts:9,11` | `rank .max(4)`, `recommendations .max(4)`, `unverified .max(4)` | resolved-P2 (2.0 mirror: `rank .max(10)`, `recommendations .max(10)`, no unverified) |
| `src/adapters/native-recommendation.ts:29` | `result_limit: Math.min(4, …)` | resolved-P2 (`Math.min(NATIVE_MAX_RESULTS, …)`) |
| `src/adapters/native-recommendation.ts:86` | warning text "最多4套" ("at most 4 listings") | resolved-P2 ("最多10套" from `NATIVE_MAX_RESULTS`) |
| `tests/native-recommendation.test.ts:23` | `assert.equal(mapped.request.result_limit,4)` | resolved-P2 (5 → 5, 20 → 10) |
| `tests/native-recommendation-live.test.ts:29` | `exchange.data.request.result_limit` equals 4 | resolved-P2 (10; env-gated, not run in P2) |
| `src/adapters/recommendation.ts:91` | baseline adapter caps unverified at `unverified.slice(0,5)` (shared cap, spec section 10) | resolved-P2 (`unverified.slice(0,req.result_limit)`) |
| `tests/full-data-live.test.ts:55,64,70` | baseline limit 5 for both groups; unverified length 5; recommendations length 5 | resolved-P2 (10; env-gated). P7: not run, it needs the app stack on 8010 running this tree (OPEN_ITEMS live checklist) |
| `tests/location-subject-live.test.ts:40,54` | unverified length 5; recommendations length 5 | resolved-P2 (10; env-gated). P7: not run (app stack on 8010 needed; OPEN_ITEMS) |
| `scripts/test_full_data_ui.py:25`, `scripts/test_location_subject_ui.py:19,37,42` | `.listing-card` count 5 | resolved-P2 (10). P7: browser scripts not run (app stack on 8010 needed; OPEN_ITEMS) |
| `frontend/app.js:56` | `result-count` = recommendations + `unverified_candidates.length` (old-name tolerance, count semantics) | resolved-P2 (`?.length` / `||[]` tolerance) |
| `src/domain/workflow.ts:195` | the app requests `result_limit: 5` | resolved-P2 (10) |
| `src/domain/workflow.ts:157` | `interpretFeedback` matches `第([一二三四五\d])套`: Chinese numerals 六–十 and `第10套` fail | resolved-P2 (`interpretFeedback` moved to `src/domain/selection.ts`, ordinals 一..十 / 1–10) |
| `recommendation/tests/test_recommendation.py:55` | `len(first.recommendations) == 4` | resolved-P2 (`min(10, pool.size) == 9`) |
| `recommendation/tests/test_terminal_repositories.py:35,40` | `feedback_version == 4` (one event per returned item) | resolved-P2 (committed snapshot checks) |
| `tests/test_native_transport.py:22` | `len(presentation.listings) >= 4` | keep (`>= 4` still holds with 9 listings; not a cap) |
| `recommendation/tests/test_policy_controller.py` (`test_model_preview_limit_preserves_full_pool`) | preview limit 4: P1 lowers `recommendation.max_result_limit` to 4 in that test's experiment config, because `model_result_candidates` must be ≥ max_result_limit | resolved-P1 (assertion kept) |
| `tests/native-recommendation-live.test.ts:26,40`, `tests/native-geography-live.test.ts:13,24` | live oracles `slice(0,4)` / limit 5 | `native-geography-live.test.ts:24` resolved-P5 (P5 review: select-v1 oracle, see section P); `native-recommendation-live.test.ts:26,40` resolved-P7 (select-v1 / fused-v2 oracle, see section P) |
| `scripts/test_native_recommendation_ui.py:22,29,41,60` | 8 cards (4+4), `nth(4)` is the first pending card | resolved-P7 (card count read from the API state = verified + hardGap pending; pending card checked only when present; '样例浏览兴趣' replaced by the fused-score / selection-reason labels). Not run (browser against 8010) |
| `evaluation/followup_workflow_browser.py:31` | `#result-count == 5` | resolved-P7 (`#result-count` must equal the rendered cards, 1..20). Not run (browser against 8010) |
| `README.md:11`, `docs/Recommendation_Agent_原生接入说明.md:85,89,150,256`, `contracts/releases/README.md:32` | text "最多4条" (at most 4 items) / "1—4" / "5/4" / 8 pending cards | resolved-P2 for these lines (2.0 limits/feedback wording); full docs rewrite resolved-P7 (README.md, recommendation/README.md, recommendation/contracts/README.md, 原生接入说明, contracts/releases/README.md, MIGRATION.md) |
| `recommendation/settings/recommendation.yaml` `max_result_limit: 10` vs the contract's 4 | `test_contract_max_equals_config_max` is a **strict xfail** until P2 | resolved-P2 (marker removed; `test_contract_max_equals_config_max` and the TS example test pass) |

## P. Ranking-prefix assertions

| Location | What | Status |
|---|---|---|
| `recommendation/validation/gate.py:18` | `final_gate`: ids == `ranked[:result_limit]` | resolved-P5 part 2: ids == `dispatcher.selected` (select-v1 display order), inside the pool, and the recorded selection must replay from its own `selection_input` |
| `recommendation/validation/gate.py:80` | `replay_gate`: ids == `pool.ranking[:result_limit]` | resolved-P5 part 2: `select-v1` registered in `validation/selection_replay.py` (recomputes ids, mode, seed and per-item reasons from the snapshot only); `ranked-prefix-v1` kept for P3–P5 part 1 snapshots |
| `recommendation/agent/controller.py:49` | `ScriptedController` returns `ranked[:result_limit]` | resolved-P5 part 2 (returns `dispatcher.selected`, the select-v1 selection; unchanged code since P4) |
| `recommendation/agent/controller.py:106` | system prompt "ranked prefix up to result_limit" | resolved-P4 (prompt moved to `recommendation/agent/prompt.py`; rule 7 returns `selected_ids` of the latest rank_candidates, which P5 part 2 makes the select-v1 selection) |
| `recommendation/agent/controller.py:117` | repair `recorded_ranked_prefix` | resolved (P4 renamed it `recorded_selection`; P5 part 2 makes it the select-v1 selection) |
| `recommendation/tests/test_policy_controller.py:166` | `RecoveringModel` returns `ranked_ids[:4]` | resolved-P2 (prefix up to the request's effective result_limit) |
| `recommendation/tests/test_fit_controller_regression.py:78` | `RepairModel` returns `ranking[:4]` | resolved-P2 (same) |
| `recommendation/tests/test_recommendation.py:105` | the D21 listing comes first (deterministic order) | resolved-P5 part 2 (reviewed: the pool is not larger than result_limit, so select-v1 is `small_pool` = full score order) |
| `tests/native-geography-live.test.ts:24` | exact top-5 ID oracle | resolved-P5 (P5 review round): select-v1 oracle — leaders == O prefix, every `score_rank` == position in the independently computed O, drawn items directly after the leaders within \|G\| (near_tie_sampling) or \|R\| (cold_start_exploration) with matching `selection_reason`s, exact prefix only for `ranked`/`small_pool`. Env-gated (`NATIVE_GEO_LIVE=1`), not run in P5 (needs the data API on 8089 and a native service running this code); run in the P7 live smoke |
| `tests/native-recommendation-live.test.ts:26,40` | exact top-10 ID oracles (initial round and feedback round) | resolved-P7: rewritten on the shared `tests/helpers/select-v1-oracle.ts` (checked offline by `tests/select-v1-oracle.test.ts`): round 1 all scores 0 → O = listing-ID order, cold start with `no_soft_requirements`; round 2 complete snapshot asserted (shown = native ranking, one record per shown ID, 90 s recorded) and fused-v2 single-reference scores within 1e-9; round 3 pool rebuilt; `feedback_version`, `behavioral` and the `feedback: []` body removed. The full test needs the app on 8010 running this tree: not run; its oracle logic ran live against a separate 8192 service (`p7/live_oracle_check.ts`, passed) |
| `tests/advanced-live.test.ts:31` | top-5 prefix `deepEqual(…, expected.slice(0,5))` | resolved-P7: mode-aware. Native: select-v1 oracle over the exchange response with fused-v2 scores `soft_weight × 800/(800+d)` over ACTIVE supermarkets; baseline: exact top-10 with the price tie-break (the app requests 10 since P2). Not run (app on 8010) |
| `tests/native-geography-live.test.ts:25` | scoring-formula oracle `combined_score == 1/(1+d/1000)` (soft-only weighted-v1; v2 fuses at `soft_weight`) | resolved-P5 (oracle is `soft_weight × 1/(1+d/1000)`; env-gated, not run) |

## S. Run-snapshot and trace consumers

| Location | Reads | Status |
|---|---|---|
| `recommendation/state/store.py:54-66` `current()` | json-loads every snapshot of the conversation | resolved-P2 (direct lookup by state id over `conversations_v2`/`runs_v2`) |
| `recommendation/service.py:85,95` | replay: `replay_gate(response, cached[2])`, copies `cached[2]["pool"]` into the trace | resolved-P3 (the replay trace stores `pool_ref`, the compact pool reference; P2-era full snapshots keep the old replay check) |
| `recommendation/service.py:103` + `context/manager.py:9-75` | `previous[state_id, request, events, sequence]` | resolved-P2 (no accumulation; `check_state`/`check_feedback`/`prepare`) |
| `recommendation/service.py:250-261` | the snapshot stores `dispatcher.snapshot()` (full pool) twice (snapshot + trace) | resolved-P3 (run snapshot = `pool` {pool_key, pool_version, data_version, cache} + `selection_input`; trace `pool_ref` + `pool_stats`; pool content only in `pool_cache`) |
| `recommendation/tools/dispatcher.py:59,165,219` | every tool step embeds the full `snapshot()` | resolved-P6 part 1 (tool steps store arguments + compact result); P6 review: the input/output version digests use the compact `Dispatcher.identity()`, `snapshot()` is kept for tests/debugging only |
| `recommendation/validation/gate.py:76` | `snapshot["pool"]` rows/evidence/ranking/scores | resolved-P3 (compact snapshots use `selection_input`; `_replay_full_snapshot` reads P2-era snapshots only) |
| `recommendation/app.py:61-63,79-92` | `previous["state_id"]`; presentation from `cached[2]["pool"]` validation/rows | resolved-P2 (v2 head rewrite; legacy/unknown state → null; counts from `response.pool`; tested in `tests/test_native_transport.py`); pool content resolved-P3 (P7 check: `presentation.listings` are the returned items' rows, `counts` come from `response.pool`, app.py:94-98; the pool rows themselves are never read by app.py) |
| `recommendation/state/store.py:106-125` `resolve_reference` | trace, `<trace>/pool`, step `snapshot_ref` | P3 part resolved: `<trace>/pool` resolves from `pool_cache` by the trace's `pool_ref.pool_version` (STALE_REFERENCE "pool evicted" / "not persisted"; P2-era traces return their embedded pool); step `snapshot_ref` resolved-P6 part 1 (compact step record; context_ref reconstructs messages; older v2 steps return their stored snapshot; legacy v1 content carries `legacy_note`) |
| `recommendation/terminal/__main__.py:111-138,261,280-293` | `current[request, state_id, profile]`, `store.trace` | P2 part resolved (snapshot feedback, legacy v1 note); P6 part 1: show-trace reads slim traces, legacy note tested (`test_trace_v2.py`); resolved-P6 part 2 (debug commands in `terminal/debug.py`; show-trace moved there with the legacy note and `--debug`; `test_terminal_debug.py`) |
| `recommendation/terminal/acceptance.py:286,298-301` | `resolve_reference(trace_ref)` with a `final_gate` step; `current()` before/after restart | P2 part resolved (v2 flow); resolved-P6 part 1 (reviewed: `trace_inspection` reads the final_gate step and `scoring_summary` the rank step `result.data`, both kept in the slim trace) |
| `recommendation/examples/check_export.py:43` | `trace.steps[-1]` is the gate | resolved-P6 part 1 (stage records live in `trace.stages`; the last step is still the gate; module re-run) |
| `scripts/compare-recommendation-upstream.py:36-38` | `current().profile`, trace gate | retired-P7 (907cfa8 baseline-only evidence, R15): exits 2 with a retirement notice by default; the historical comparison can be re-run only against an archived contract-1.0 checkout (`--legacy-root`, `--output`), never overwriting `evaluation/reports/native-upstream-parity.json`; test `test_compare_upstream_script_is_retired` |
| `src/server.ts:138-143` | native sample-feedback guard: `current.recommendation?.native?.ranking.includes(listing_id)` (:141) and membership in both V1 groups (:143); spec section 10 names it | resolved-P2 (reviewed, unchanged: `native.ranking` is the native response's ID list, the same shown set the sidecar uses; V1 membership check keeps both groups because hardGap shows native items as unverified) |
| `recommendation/examples/generate_contract_examples.py:42-67`, `recommendation/examples/run.py:31-46` | build v1 `ViewingEvent` feedback from `feedback_example()` and replay; R15 consumers | resolved-P2 (feedback snapshot) |
| `recommendation/tests/test_recommendation.py:79,81-83,119-120,304,311-318` | events, `steps[0].snapshot.rows`, pool ref with 12 rows, snapshot_ref | P2 part resolved (snapshot feedback); resolved-P6 part 1 (build step references the pool version, rows via `<trace>/pool`, snapshot_ref returns the step record) |
| `recommendation/tests/test_policy_controller.py:101-105,182,200,314-325` | `trace.configuration` (now the full settings + `config_hash`, resolved-P1), model steps, tool `snapshot.pool_ids/scores` | resolved-P6 part 1 (`messages_added`; pool count via `<trace>/pool`; contributions from the response) |
| `recommendation/tests/test_fit_controller_regression.py:40,86` | gate steps | resolved-P5 part 2 (reviewed: gate step keys unchanged; P5 adds `selection` {algorithm_version, mode, selected_ids}) |
| `recommendation/tests/test_terminal_repositories.py:33,41-48` | `current()` events/state_id | resolved-P2 |
| P1 additions: trace/snapshot `configuration`, `config_version`, `config_hash`, `selection_hash`, `config_source` | `service.py:54-55,251-252` | resolved-P1 (format 2 snapshot in P3) |

## N. explicit/implicit names (renamed to soft/behaviour in P2/P5)

| Location | Status |
|---|---|
| `recommendation/models/score_breakdown.py:17-21` (`explicit_score`, `implicit_score`, `explicit_weight`, `implicit_weight`, `evidence_coverage`) | resolved-P2 (soft/behaviour names + appointment/browsing branches) |
| `recommendation/models/tool_results.py:14-18` (`ScorePreview`) | resolved-P2 |
| `recommendation/ranking/ranker.py:154-158` | resolved-P5 (ranker.py rewritten as the scalar fused-v2 reference) |
| `recommendation/contracts/schemas/response.schema.json` (6), `recommendation/examples/contracts/success.json` (16) | resolved-P2 (regenerated) |
| `src/contracts/recommendation-native.ts:8` `nativeScoresSchema` | resolved-P2 (2.0 names; `legacyNativeScoresSchema` kept only to read archived V1 projections) |
| `src/adapters/native-recommendation.ts:71-72,88` (`native:explicit`/`native:implicit`, `native_explicit_implicit_weighted_fit_not_probability`) | resolved-P2 (`native:soft`/`native:behavior`, `native_soft_behavior_weighted_fit_not_probability`) |
| `src/domain/explanation.ts:8` ("显式偏好" = stated preference, "样例浏览兴趣" = sampled browsing interest) | resolved-P2 (软需求匹配 / 行为相似度 / 选择原因; old names still rendered for archives) |
| `frontend/native-ranking.js:7-8` | resolved-P2 (same, tolerant of old names) |
| `src/contracts/recommendation-v1.ts` origin enum `stated_preference`/`inferred_interest`, `src/adapters/recommendation.ts`, `tests/fixtures/recommendation-response.json`, `contracts/examples/current-v1/*`, `contracts/releases/recommendation-response-1.0.schema.json` | keep (V1 origins stay, spec section 10) |
| `recommendation/tests/test_recommendation.py:57-58,69,246-249`, `recommendation/tests/test_policy_controller.py:124,340` | resolved-P5 (assertions changed one at a time, VALIDATION.md P5) |
| `scripts/test_native_recommendation_ui.py` ("样例浏览兴趣"), `README.md:56`, docs 原生接入说明 / 双向接口 | resolved-P7 (UI script checks 原生融合匹配分 / 选择原因; README and 原生接入说明 rewritten; 双向接口 keeps its P2 2.0 wording, validated by `scripts/validate-recommendation-examples.ts`) |
| `docs/Recommendation_Agent_原生接入说明.md:116` (`combined = 0.8 × explicit + 0.2 × implicit`), `recommendation/README.md:94,98` (weighted-v1 utility/duration text) | resolved-P7 (fused-v2 / select-v1 text; the weighted-v1 figures remain only in the doc's labelled 2026-10-20 history table) |
| `recommendation/examples/contracts/{success,clarification,error,no-match}.json` `execution.scoring_version: "weighted-v1"`: the code now emits `weighted-v1:<selection_hash[:12]>`; no check compares the examples with fresh output | resolved-P2 (regenerated) |

## M. Manifest-tracked files modified in P1 (register in P7)

`recommendation/agent/controller.py` (patch already declared, so re-hash it),
`recommendation/config.py`, `recommendation/context/manager.py`,
`recommendation/examples/config.json`, `recommendation/ranking/ranker.py`,
`recommendation/requirements.txt`, `recommendation/service.py`,
`recommendation/terminal/__main__.py`, `recommendation/terminal/acceptance.py`,
`recommendation/tests/test_policy_controller.py`, `recommendation/tools/dispatcher.py`,
`recommendation/README.md`.
Not in the manifest: `recommendation/app.py`, `.env.example`, and the new modules
`recommendation/settings/*`, `recommendation/ranking/{legacy,suppression}.py`,
`recommendation/tests/test_settings.py`.
`scripts/import-recommendation-upstream.py --check` is expected to fail until P7. **Resolved in P7:** every changed manifest file (sections M and the P2–P7 additions below, plus the P7 docs edits of `recommendation/README.md` and `recommendation/contracts/README.md`) is declared in `contracts/releases/recommendation-downstream-patches.json`; `--check` passes.

P3 additions (manifest-tracked, register in P7): `recommendation/repositories/listing_repository.py`
(first edit: shared alias-free column list, fingerprint, `retrieve_versioned`, by-id
`lookup`/`lookup_versioned` with `ANY(CAST(:ids AS BIGINT[]))`), and further edits to
`recommendation/service.py`, `recommendation/tools/dispatcher.py`,
`recommendation/validation/gate.py`, `recommendation/state/store.py`,
`recommendation/tests/test_recommendation.py`, `recommendation/tests/test_policy_controller.py`
and the regenerated `recommendation/examples/contracts/*.json`. New P3 modules outside the
manifest: `recommendation/state/{pool_cache,data_version}.py`,
`recommendation/tools/pool_builder.py`, `recommendation/validation/selection_replay.py`,
`recommendation/tests/test_pool_cache.py`.

P4 part 1 additions (manifest-tracked, register in P7): `recommendation/repositories/geographic_repository.py`
(rewritten: native catalog + resolver + Python distances; NEAR_PLACE relations and the 1 m
tolerance path removed), `recommendation/tests/test_terminal_repositories.py`
(`test_neo4j_schema_mapping_and_missing_edges` mock/assertions), `recommendation/README.md`
(Neo4j/geography paragraph), and further edits to `recommendation/tools/dispatcher.py`,
`recommendation/validation/gate.py` and the regenerated `recommendation/examples/contracts/*.json`.
New P4 modules outside the manifest: `recommendation/geography/{__init__,catalog,normalize,resolver,distance,evidence}.py`,
`recommendation/tests/test_geography.py`; edits outside the manifest: `recommendation/assembly.py`,
`recommendation/tools/pool_builder.py`, `recommendation/state/data_version.py`,
`recommendation/settings/{pending.py,recommendation.yaml}`, `recommendation/tests/{test_pool_cache,test_settings}.py`.

P4 part 2 additions (manifest-tracked, register in P7): `recommendation/agent/controller.py`
(ScriptedController = the fixed pipeline, LiveController tool list from the registry, prompt and
repair text from `agent/prompt.py`), `recommendation/tools/dispatcher.py` (pipeline stages with
dependency checks and invalidation, typed per-tool arguments, resolution budget, bind
application, tool `data`), `recommendation/models/tool_results.py` (`ToolResult.selected_ids`,
`completed_stages`, `data`), `recommendation/service.py` (one comment),
`recommendation/README.md` (controller paragraph, diagram label, settings paragraph),
`recommendation/tests/test_policy_controller.py` and `recommendation/tests/test_recommendation.py`
(tool names / build_pool step lookup). New modules outside the manifest:
`recommendation/tools/{arguments,registry,geo_tools}.py`, `recommendation/agent/prompt.py`,
`recommendation/geography/search.py`, `recommendation/repositories/platform_transit.py`,
`recommendation/tests/test_geo_tools.py`; edits outside the manifest:
`recommendation/geography/evidence.py` (tool helpers), `recommendation/settings/{pending.py,recommendation.yaml}`,
`recommendation/tests/{test_fit_controller_regression,test_pool_cache}.py`,
`docs/Recommendation_Agent_原生接入说明.md` (tool table).

P4 review round additions (manifest-tracked, register in P7): `recommendation/contracts/requirements.py`
(Criterion accepts destination_id together with destination_name; a category stands alone),
further edits to `recommendation/tools/dispatcher.py` (resolution budget charged after the
stage check and only for calls that ran; compact get_geographic_evidence data; bindable flag) and
`recommendation/README.md` (resolution and bind paragraphs). Outside the manifest:
`recommendation/geography/{normalize,resolver,evidence}.py`, `recommendation/tools/{geo_tools,pool_builder,registry}.py`,
`recommendation/agent/prompt.py`, `recommendation/tests/{test_geography,test_geo_tools,test_pool_cache}.py`,
`tests/fixtures/place-mention-rules.json` (new), `tests/native-geography-parity.test.ts`,
`tests/native-recommendation.test.ts`, `src/adapters/{native-recommendation,locations}.ts`,
`src/domain/geo-intake.ts`, `docs/Recommendation_Agent_原生接入说明.md`, regenerated
`recommendation/examples/contracts/*.json`.

P5 part 1 additions (manifest-tracked, register in P7): `recommendation/ranking/ranker.py`
(rewritten: scalar fused-v2 reference `soft_utility` / `feature_similarity` / `pair_similarity`),
`recommendation/context/manager.py` (no weighted-v1 path; profile records the v2 browsing weights),
`recommendation/service.py` (scoring identifier / policy from `ranking/scoring_v2.py`),
`recommendation/tools/dispatcher.py` (full-pool `score_pool`, breakdowns only for selection +
preview rows, `scoring` summary in the rank_candidates data, `scored` components kept),
`recommendation/config.py` (docstring), `recommendation/tests/test_policy_controller.py`,
`recommendation/tests/test_recommendation.py`, regenerated `recommendation/examples/contracts/*.json`.
Deleted (never in the manifest): `recommendation/ranking/legacy.py`. New outside the manifest:
`recommendation/ranking/scoring_v2.py`, `recommendation/tests/test_scoring_v2.py`; edits outside
the manifest: `recommendation/assembly.py` (transitional appointment warning removed),
`recommendation/settings/{loader.py,pending.py,recommendation.yaml}`,
`recommendation/tests/{test_settings,test_contract_v2_fixtures}.py`,
`tests/fixtures/recommendation-v2/{flow,ten-results}.json`.

P5 part 2 additions (manifest-tracked, register in P7): `recommendation/tools/dispatcher.py`
(rank_candidates runs `ranking.selection.select`, keeps `selection`, `seed_override` extension
point, `selection` record in the tool data), `recommendation/validation/gate.py` (final_gate:
pool membership + selection replays from its input, `selection` in the gate step; replay_gate:
select-v1 mode/seed/reasons check), `recommendation/service.py` (assembly API,
`context.selection_policy`), regenerated `recommendation/examples/contracts/*.json`. New outside
the manifest: `recommendation/ranking/{selection,exploration}.py`,
`recommendation/tests/test_selection_v1.py`,
`evaluation/recommendation-exploration-2026-10-23/p5/live_selection.py`; edits outside the
manifest: `recommendation/assembly.py` (selection summary / reasons / notes from the selection),
`recommendation/validation/selection_replay.py` (select-v1 replayer, `Replayed`),
`recommendation/settings/{pending.py,recommendation.yaml}` (cold_start / selection markers
removed), `recommendation/tests/{test_geo_tools,test_pool_cache}.py`,
`tests/fixtures/recommendation-v2/{ten-results,response-cases}.json`.

P5 review round additions (manifest-tracked, register in P7): `recommendation/terminal/acceptance.py`
(duplicate_feedback compares request-independent outputs, `resent_feedback_differences`,
`scoring_summary`), `recommendation/service.py` (`execution.strategy` label), regenerated
`recommendation/examples/contracts/*.json`. Edits outside the manifest:
`recommendation/ranking/selection.py` (tie_group docstring pin), `recommendation/tests/test_selection_v1.py`,
`tests/native-geography-live.test.ts`, `tests/fixtures/recommendation-v2/ten-results.json` (regenerated),
`evaluation/recommendation-exploration-2026-10-23/p5/{live_selection.py,live-selection.json}`.

P6 part 1 additions (manifest-tracked, register in P7): `recommendation/service.py` (stage records
input_validation / final_gate / response_assembly, error location, `slim.finalize`),
`recommendation/tools/dispatcher.py` (slim tool steps, rank sub-stage marks, debug rows),
`recommendation/agent/controller.py` (model steps store added messages), `recommendation/models/tool_results.py`
(comment), `recommendation/state/store.py`
(`resolve_reference` via `tracing/slim.py`, legacy note), `recommendation/README.md` (trace sentences),
`recommendation/tests/{test_recommendation,test_policy_controller}.py` (assertions on the new trace shape).
New outside the manifest: `recommendation/tracing/{slim,stages}.py`, `recommendation/tests/test_trace_v2.py`.
Edits outside the manifest: `recommendation/ranking/selection.py` (`marks`, `Selection.outcome`),
`recommendation/settings/{pending.py,recommendation.yaml}` (trace markers removed),
`recommendation/tests/{test_geo_tools,test_scoring_v2,test_settings}.py`.

P6 part 2 additions (manifest-tracked, register in P7): `recommendation/terminal/__main__.py`
(debug command dispatch, staged cold_start / result_limit, observed controller + retained run
dispatcher, `input_changed` / step session wiring, response summary, `--script` / `--brief`,
extended help), `recommendation/README.md` (terminal debugging paragraph + TERMINAL.md link).
New outside the manifest: `recommendation/terminal/debug.py`, `recommendation/tests/test_terminal_debug.py`,
`evaluation/recommendation-exploration-2026-10-23/{TERMINAL.md,p6/terminal_transcript.py,p6/fixture-terminal-transcript.txt,p6/live-terminal-transcript.txt}`.

P6 review round additions (manifest-tracked, register in P7): `recommendation/tools/dispatcher.py`
(`identity()` for the ToolResult version digests), `recommendation/validation/gate.py`
(final_gate step `geographic_provenance`: the nearest exit of the returned listings).
Edits outside the manifest: `recommendation/tracing/{slim,stages}.py` (shared debug budget,
structural scrub of JSON strings, older-v2 `<trace>/messages`, explicit `preview_total`),
`recommendation/terminal/debug.py` (run trace by `execution.trace_id`, `show-trace --latest`,
step-mode `persisted`), `recommendation/tests/{test_trace_v2,test_terminal_debug,test_geo_tools}.py`,
`evaluation/recommendation-exploration-2026-10-23/p6/{terminal_transcript.py,fixture-terminal-transcript.txt,live-terminal-transcript.txt}`.

## F. P7 final grep check (classification of every remaining hit)

Command: `bash evaluation/recommendation-exploration-2026-10-23/p7/inventory_check.sh` (the re-run commands below,
over tracked and untracked files); output saved in `p7/inventory-grep.txt`.

| Grep | Hit | Status |
|---|---|---|
| 1 | `recommendation/tests/test_terminal_debug.py:134` `first_ranked[:4]` | keep: the terminal command under test is `show-scores --top 4` (argument, not a result cap) |
| 2 | none | – |
| 3 | `src/adapters/locations.ts:67` `slice(0,4)` | keep: place-ambiguity message (already noted below) |
| 3 | `src/agents/orchestrator/intent-recognizer.ts:7`, `src/domain/knowledge.ts:11,39`, `src/types.ts:13` (`max(4)` / `slice(0,4)`) | keep: intent tasks, RAG evidence / query rewrites and the A–D role assignments; unrelated to recommendation results |
| 3 | `tests/business.test.ts:111,133` `result_limit:5` | keep: V1 schema/baseline unit tests that send 5 (V1 allows 1–20); not a cap |
| 3 | `tests/native-geography-live.test.ts:15` `result_limit:5` | keep: the request asks for 5; the select-v1 oracle checks K = min(5, pool) |
| 4 | none | – |
| 5 | `frontend/native-ranking.js:16-17`, `src/domain/explanation.ts:18-19`, `src/contracts/recommendation-native.ts:50` | keep: old-name readers for archived contract-1.0 exchanges (spec section 0, `legacyNativeScoresSchema`) |
| 6 | `src/adapters/locations.ts:67`, `src/domain/knowledge.ts:39` | keep (see grep 3) |
| 6 | `tests/select-v1-oracle.test.ts:61` `slice(0,5)` | keep: negative case of the oracle test (a truncated small pool must be rejected) |
| 6 (found in P7) | `tests/intake-live.test.ts:34` exact top-5 nearest oracle (`slice(0,5)`, not in the P1 inventory) | resolved-P7: mode-aware like advanced-live (native: select-v1 oracle with `soft_weight / (1 + d / r)` over the nearest recorded exit; baseline: exact top-10, the app requests 10 since P2). The rest of that test (unverified counts) asserts baseline semantics; not run (app on 8010; OPEN_ITEMS) |
| 7 | `native.ranking` in `src/server.ts:141`, `src/domain/feedback-sidecar.ts:22-23`, the live tests and the 双向接口 field table | keep: `native.ranking` is the native response's ID list (the shown set used by the sample guard and the sidecar, spec R7) |
| 7 | `tests/native-geography-live.test.ts:30,33` `1/(1+p.d/1000)` | resolved-P5: fused-v2 oracle `soft_weight × 1/(1+d/1000)` |
| 8 | `recommendation/examples/{generate_contract_examples,run}.py` `feedback_example` | keep: the terminal helper's name; since P2 it returns a contract-2.0 `FeedbackSnapshot` (no `ViewingEvent`, no `"weighted-v1"`) |
| 9 | `legacy.py` absent; no import | resolved-P5 |
| 10 | none (the YAML header no longer quotes the marker format) | resolved-P6/P7 |

## Re-run commands (P7 final check)

```bash
git grep -nE "le=4\b|max_length=4|\[:4\]|\[: ?4\]|len\(pool\) > 4|self.pool\) > 4" -- 'recommendation/*.py' 'tests/*.py'
git grep -nE '"(maximum|maxItems)": 4' -- recommendation/contracts/schemas
git grep -nE "max\(4\)|Math\.min\(4|result_limit:\s*5|slice\(0,\s*4\)|最多4" -- 'src/*' 'tests/*.ts' 'frontend/*'
git grep -nE "ranked\[: ?request|ranking.\]\[: ?request|ranked or \[\]\)\[:|recorded_ranked_prefix|ranked prefix" -- recommendation
git grep -nE "explicit_(score|weight)|implicit_(score|weight)|native:explicit|native:implicit" -- recommendation src frontend
git grep -nE "result_limit,\s*4|slice\(0,\s*[45]\)|,\s*5\]|to_have_count\((5|8)|\.length,\s*5\)" -- src tests frontend scripts evaluation/followup_workflow_browser.py
git grep -nE "native\??\.ranking|1/\(1\+p\.d|0\.8 ×|\.8 explicit" -- src tests frontend docs README.md recommendation/README.md
git grep -nE "ViewingEvent|feedback_example|\"weighted-v1\"" -- recommendation/examples
test ! -e recommendation/ranking/legacy.py && grep -rnE 'ranking[./]legacy|from \.legacy|import legacy|legacy\.[A-Za-z_]+|"weighted-v1' recommendation --include='*.py' --exclude-dir=tests   # must be empty after P5 (weighted-v1 module, its constants and its scoring id gone)
# (amended in the P5 review: the plain `grep -rn legacy recommendation` cannot be empty, because the
#  contract-1.0 readers stay on purpose: state/store.py legacy_* / is_legacy_trace, terminal
#  legacy notes, app.py legacy-head handling, spec section 4)
grep -n "not in effect until" recommendation/settings/recommendation.yaml   # must be empty after P6
```
(The `slice(0,4)` hit in `src/adapters/locations.ts:65` is the place-ambiguity message, not a
result cap: keep.)
