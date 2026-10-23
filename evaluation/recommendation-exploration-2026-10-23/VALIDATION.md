# Recommendation v2: validation log

One section per phase (spec section 13). Failures that already existed before a phase started are
listed apart from failures the phase introduced. Commands run from the repository root with
`.venv/bin/python` (CPython 3.11.16). PyYAML is 6.0.3 and numpy is 1.26.4.

## P1

Scope: baseline fixtures, INVENTORY.md, `recommendation/settings/recommendation.yaml`,
`recommendation/settings/loader.py`, the `RecommendationConfig` adapter in
`recommendation/config.py`, and the ranker, dispatcher, controller and service reading the new
settings. Terminal, acceptance and app.py load the YAML by default, and `RECOMMENDATION_CONFIG` /
`--config` (YAML or JSON) are accepted. Also: `examples/config.json` rewritten per R2, and
PyYAML + numpy declared.

### Baseline before any change (commit 4d36507, clean tree)

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | 48 passed |
| `python -m recommendation.schemas --check` | exit 0 |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 193 tests: 174 passed, 0 failed, 19 skipped (env-gated live tests) |
| `scripts/import-recommendation-upstream.py --check` | exit 0 (71 files, 1 declared patch) |

No gate was failing before P1.

### P1 validation items

| Item (spec section 13, P1) | Proof | Outcome |
|---|---|---|
| Baseline fixtures saved | `baseline/capture.py`, run at 4d36507 before any edit → `baseline/{initial,feedback,preference-update,geography}.{request,response,trace}.json`, `baseline/examples-contracts/*.json` (copies of `recommendation/examples/contracts`), `baseline/MANIFEST.json` (sha256 per file, `recommendation_tree_clean: true`) | pass |
| Grep inventory (4-limits, ranking-prefix assertions, snapshot/trace consumers, explicit/implicit names) | `INVENTORY.md` sections A, P, S, N, plus C (settings key consumers), L (legacy constants), M (manifest files to register) and the P7 re-run commands. After review: added the TS/live/UI sites the first pass missed (`tests/native-recommendation.test.ts:23`, `native-recommendation-live.test.ts:29`, `native-geography-live.test.ts:25`, `advanced-live.test.ts:31`, `src/server.ts:138-143`, `src/adapters/recommendation.ts:91`, the baseline-5 limits in `full-data-live`, `location-subject-live`, `scripts/test_full_data_ui.py`, `scripts/test_location_subject_ui.py`, `frontend/app.js:56`, docs 0.8/0.2 text, the R15 example consumers and the stale `scoring_version` in the examples), and re-run greps that match them | pass (entries are open for P2–P7 by design) |
| Valid config loads | `test_settings.py::test_committed_yaml_loads_with_identity`, `test_yaml_and_json_alternates_share_schema_and_hash`, `test_examples_config_is_v2_experiment_with_cold_start_disabled`, `test_every_yaml_value_is_annotated_with_its_source` | pass |
| Bad weights rejected, key named | `test_bad_weights_rejected` (9 cases: layer sums soft/behaviour, appointment/browsing, feature weights; negative, NaN, inf, string; missing-branch contribution; redistribution flag) | pass |
| Illegal ranges rejected, key named | `test_illegal_ranges_enums_and_unknown_keys_rejected` (39 cases: thresholds outside [0,1], sizes < 1, bool as int, ε < 0, target < max_result_limit, max_result_limit > 10, max_model_turns > 16, max_tool_calls > 40, resolution calls > total, model_result_candidates < max_result_limit, bounds not increasing, every enum, config_version, unknown keys) | pass |
| Missing required parameters rejected, key named | `test_missing_required_parameter_rejected_with_key` (23 reachable keys from the R2 table; each message names the key and the reason it is reachable), `test_absent_required_key_rejected` (4 absent keys/sections); and the inverse, `test_unreachable_parameters_may_be_null` (behaviour off / browsing off / cold start off / cache off accept null) | pass |
| Secret-like keys rejected | `test_secret_like_keys_rejected` (14 cases: password, api_token, database_url, dsn, secret_key, credentials, a URL-valued string; after review also camelCase/run-together `databaseUrl`, `accessKey`, `privatekey`, `neo4jPass`, `pwd`, `authUri` and a scheme-less `user:pass@host:5432/db` value), `test_committed_settings_contain_no_secret_like_keys` (YAML and examples/config.json; no schema key trips the substring check, so no allow-list is needed) | pass |
| Duplicate hard-coded values removed (grep) | `test_no_duplicate_hard_coded_parameters_outside_settings` greps every non-test `recommendation/**/*.py` for the old literals (DEFAULT_* dicts, ScoringPolicy/RunLimits, 20000, 3000, 500.0, 0.8, 0.2, 0.25, 0.3, 120, log(121), timeout=90, le=8, le=20, ge=4/le=500). Only `ranking/legacy.py` may contain them. A manual `grep -rnE` found nothing else (latitude ±90 and the sample feedback 90/30 s durations are not algorithm parameters) | pass |
| Contract max == config max test | `test_native_contract_layers_agree_on_result_maximum` passes and can fail: it reads the result cap from every layer (Python request `result_limit`, response `recommendations`, item `rank`, `Proposal.listing_ids`; generated `request.schema.json` / `response.schema.json` `maximum`/`maxItems`; the strict TS zod mirror `rank .max(N)` and `recommendations .max(N)`) and requires one value. Tool-result previews are excluded on purpose: they become `trace.preview_rows` (plan Step 8, P6). `test_contract_max_equals_config_max` runs the same extraction against `recommendation.max_result_limit` and is a **strict xfail**: every layer is still 4 and the YAML is 10 (confirmed value). The old `..._within_ceiling` assertion was removed (the loader already guarantees it, so it could not fail) | **deferred to P2** (documented carry-over): the equality needs the native contract raised to 10, which spec section 13 assigns to P2 ("result_limit 10 across layers") together with contract 2.0, the TS mirror and the regenerated schemas. P2 removes the marker, derives `loader.RESULT_LIMIT_CEILING` from the contract maximum, and adds the TS-side example-parse test of spec section 3 |
| Ranker reads config; weighted-v1 behaviour unchanged | (1) `test_weighted_v1_scores_identical_to_baseline_fixtures` re-runs the 4 baseline rounds: recommendations, unverified_candidates and evidence (except the volatile `observed_at`) are bit-identical, including the feedback round with implicit scores. These rounds only exercise the suppression set {bedrooms, geography}. (2) Added after review: `test_weighted_v1_score_parity_all_pairs_and_suppression_sets` compares `score()` over 12×12 fixture pairs × 9 criteria sets (suppression none, each single feature, pairs, three features, all four; a minimize utility; a hard price criterion) × 5 viewing patterns (0.5/30/90/200 s, two references) = 540 groups against `baseline/score-parity.json`, generated from the 4d36507 tree by `baseline/capture_scores.py`. All 540 group digests match exactly. Before the review fix 360 of 540 differed at the ulp level (max 2.2e-16): `0.16/0.8` renormalises to 0.19999999999999998, not 0.2. `legacy.similarity_policy` now rescales the four weighted-v1 weights to sum 1 and rounds to 12 decimals (`WEIGHT_DECIMALS`, legacy L6), which gives back 0.25/0.20/0.25/0.30 exactly. The only response difference is `execution.scoring_version`, now `weighted-v1:d0b84d8a3d2c` (settings digest) instead of `weighted-v1` | pass (bit-identical for every suppression subset of the fixtures, not only the baseline rounds) |
| Legacy constants that override YAML keys | Kept in one place, `recommendation/ranking/legacy.py`: L1 0.8/0.2 fusion, L2 120 s cap, L3 bedroom/floor-area scales, L4 no price feature, L5 all-criteria suppression, L6 weight rounding. Listed in INVENTORY.md section L; removal planned for P5. After review: these constants override YAML keys that hold the target v2 values (`ranking.soft_weight`/`behavior_weight` values, `ranking.behavior.*`, `ranking.browsing.*`, `similarity.suppression`, `numeric_scaling`, `feature_weights.price`, `geography_district_fallback`, `min_comparison_coverage`), and other sections have no runtime consumer yet (`recommendation.*` P2, `cache.*` P3, `geography.*`/`entity_resolution.*`/`controller.max_resolution_tool_calls` P4, `cold_start.*`/`selection.*` P5, `trace.*` P6). Each such key or section now carries a `[not in effect until Pn ...]` marker in the YAML, is listed in `recommendation/settings/pending.py`, and is recorded in every trace (`context.scoring_policy.settings_not_in_effect`). `test_not_in_effect_markers_match_pending_table` keeps the markers and the table identical; `test_trace_records_settings_not_in_effect` checks the trace. The loader/`config.py` docstrings and `recommendation/README.md` no longer claim that code keeps no numbers for keys in the file | pass (intentional, documented; see "Interpretation" below) |
| Terminal/app/acceptance load the YAML by default; RECOMMENDATION_CONFIG / --config alternates (YAML or JSON) | `test_terminal_loads_yaml_by_default_and_alternate_file`, `test_app_loads_yaml_by_default_and_env_alternate` (trace `config_hash`, `configuration`, snapshot `selection_hash`), `test_acceptance_env_alternate_recorded` (subprocess `python -m recommendation.terminal.acceptance` with `RECOMMENDATION_CONFIG`). Manual terminal smoke (before the review fixes): the banner showed `Settings: …/recommendation.yaml (config_hash 89d4d041d84e)`, and `config_hash b1a2811cb528` with `RECOMMENDATION_CONFIG=recommendation/examples/config.json`. The review fixes changed the canonical model (sorted appointment statuses), so the hashes are now `603ed132b7b4` and `dfd329479a5a` (computed with the loader) | pass |
| Config frozen per request; config change never alters an old request's replay | `test_settings_are_frozen_per_request`, `test_config_change_never_alters_replay_of_old_request`, `test_service_rejects_untyped_config` | pass |
| config_hash / selection_hash identity; RECOMMENDATION_TRACE_MODE toggles only trace.mode | `test_hash_identity_and_trace_mode_env`; `test_counted_statuses_order_never_changes_identity` (after review: `counted_appointment_statuses` is a set stored sorted, so reordering the YAML list leaves both hashes unchanged). An autouse fixture clears `RECOMMENDATION_TRACE_MODE` / `RECOMMENDATION_CONFIG` for `test_settings.py`; the suite also passes with `RECOMMENDATION_TRACE_MODE=debug` exported (159 passed, 1 xfailed in `recommendation/tests`) | pass |
| PyYAML and numpy in recommendation/requirements.txt | `test_requirements_declare_yaml_and_numpy` (`PyYAML>=6,<7`, `numpy>=1.26,<3`) | pass |

Illegal-range cases are now 45 (after review the single DRAFT case became six uncountable
statuses, DRAFT, PENDING_REVIEW, APPROVED, CANCELLED, FAILED and an unknown one, plus a
lowercase status).

### Interpretation of "behaviour unchanged except via config" (review finding, recorded)

P1 keeps weighted-v1 scores bit-identical to commit 4d36507 and does not wire YAML keys whose
v2 semantics weighted-v1 cannot represent: soft/behaviour fusion without redistribution,
uncapped `log1p(t/time_scale)` browsing weights, hard-criteria-only suppression, the price
feature and pool-fitted numeric scaling. Wiring them into weighted-v1 would produce a hybrid
that is neither the baseline nor the v2 algorithm, and P1 parity could not be verified. These
keys are honestly marked as not in effect (above) and are wired by P5 (fused full-pool
scoring), where the P5 validation items (>120 s still increases weight, suppression only by
hard criteria, missing branches not redistributed) test them. If the orchestrator or user
prefers wiring the expressible keys in P1 instead, that is a ruling still to make; the YAML
values themselves do not change either way.

### Validation rules the implementer decided (not literal in the R2 table)

| Rule | Reason |
|---|---|
| `ranking.browsing.time_scale_seconds` is required when `behavior_weight > 0` **and** `browsing_weight > 0` (R2 says "when browsing_weight > 0") | R2's principle is "required when reachable"; with `behavior_weight` 0 the browsing branch is unreachable, as for the `similarity.*` keys |
| `ranking.behavior.missing_branch_contribution` must be exactly 0 | spec section 7 "missing branches contribute 0"; the key exists only to state that, like `redistribute_missing_branch_weight: false` |
| `trace.debug_max_rows >= trace.preview_rows` | debug mode adds rows to the summary preview (spec section 9); a smaller debug cap would truncate below the summary |
| `cache.ttl_seconds` required when `cache.enabled` | R2 lists `cache.ttl_seconds: 0` in the provisional set and requires every reachable parameter to be non-null; the table omits `cache.*`, so it was read as "reachable when the cache is enabled" |
| `cold_start.stability.weight_perturbation_fraction < 1` (and > 0) | weights are multiplied by `1+u`, `u ~ U[-f, f]` (spec section 8); `f >= 1` allows zero or negative weights |
| `cache.pool_retention_per_conversation >= cache.max_entries_per_conversation` | an index entry must not outlive its pool content (spec R13: `<trace>/pool` resolves while retained); INVENTORY section C records the open P3 question whether the two keys merge |
| `ranking.behavior.counted_appointment_statuses` is stored sorted and must be a subset of {CONFIRMED, MANUAL_REPLIED, MANUAL_SENT} | it is a set (order must not change `selection_hash`, which seeds selection, R2/R3); spec section 1 / R1b / R7 define the countable statuses and exclude DRAFT, PENDING_REVIEW, APPROVED, CANCELLED and FAILED |
| Secret-like key check matches the R2 tokens as substrings of the lowercased key (plus `pass`, `pwd`, `credential`, whole-word `uri`) and rejects URL values and scheme-less `user:pass@host` values | R2 "secret-like keys (password, token, key, secret, dsn, url) are rejected"; substring matching catches camelCase names |

### Test assertions changed on purpose (one at a time, intent kept)

All in `recommendation/tests/test_policy_controller.py`. The old `ScoringPolicy` / `RunLimits`
models, with their code defaults, were removed, as spec section 2 requires.
- `test_invalid_policy`: the same five cases rewritten as settings overrides. Zero/inf geography scale (the bedroom scale is now legacy constant L3), all-zero feature weights, decreasing price bounds, unknown feature. It now expects `ConfigError` and checks that the message names the key.
- `test_versioned_policy_and_global_disabled_features`: `default.identifier == "weighted-v1"` became `scoring_identifier(default) == "weighted-v1:<selection_hash[:12]>"`. With no code defaults left, the identifier always names the settings digest. `trace["configuration"]["scoring"] == custom` became `trace["configuration"] == custom.to_data()`, plus a new assertion `trace["config_hash"]`. The custom policy changes the geography scale (2000) instead of three scales. The "only bedrooms enabled" check now sets price 0 as well.
- `test_model_and_tool_budgets`, `test_final_refresh_timeout_does_not_commit`: `RunLimits(...)` became `controller.*` overrides. With `max_tool_calls: 2`, the test also sets `max_resolution_tool_calls: 2`, because resolution calls are counted inside the total.
- `test_model_preview_limit_preserves_full_pool`: `model_result_candidates=4` must now be ≥ `recommendation.max_result_limit`, so the experiment config also lowers both result limits to 4. The assertions are unchanged.
- `test_configured_scales_change_similarity_without_pool_normalization`: only the geography scale (6000) is configurable now. The assertions are unchanged.

### Generic gates after P1

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **164 passed, 1 xfailed** (48 pre-existing + 116 new; the strict xfail is `test_contract_max_equals_config_max`). Before the review fixes: 147 passed, 1 xfailed |
| `python -m recommendation.schemas --check` | exit 0 (no contract change in P1) |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 193 tests: 174 passed, 0 failed, 19 skipped (same as baseline) |
| `scripts/import-recommendation-upstream.py --check` | **fails as expected** (`Upstream port fingerprint changed`): 12 manifest-tracked files were edited (INVENTORY.md section M). Registration is P7; nothing was done to work around it |

New failures introduced by P1: none, apart from the expected upstream fingerprint check.

## P2

Scope (spec section 13, Steps 2+3): native contract 2.0 in Python (request/feedback snapshot/
response/summary models, generated schemas, examples), v2 SQLite tables, run-consistency checks
without accumulation, feedback-snapshot consumption (transitional: positive browsing durations
feed the weighted-v1 behaviour path until P5; appointments are validated but carry no score),
result_limit 10 across layers; and on the TS side the strict 2.0 zod mirror
(`src/contracts/recommendation-native.ts`), `nativeRequest(req, sidecar)` / `projectNative(...,
sidecar)` (`src/adapters/native-recommendation.ts`), the optional V1 native-block fields
(`contract: "2.0"`, `pool`, `selection`, `resolution`, `config_hash`, `selection_reasons`), the
orchestrator feedback sidecar (`src/domain/feedback-sidecar.ts`, built inside the agent-A apply
transaction in `src/domain/workflow.ts`, persisted in `recommendation_calls.feedback_snapshot`
and agent B's queued body), `migrations/004-feedback-snapshot.sql` (registered in
`DomainStore.setup`), the `recommendation_cold_start` session column (`saveState`, B-apply exit
update), `interpretFeedback` ordinals 1–10, and the shared fixtures
`tests/fixtures/recommendation-v2/{contract-cases,flow,sidecar-cases}.json`.

### State when P2 entered review (first implementation, recorded separately)

| Gate | Result |
|---|---|
| pytest (generic gate) | **4 failed**, 164 passed: `test_native_contract_layers_agree_on_result_maximum`, `test_contract_max_equals_config_max` (TS mirror still 4), `test_weighted_v1_score_parity_all_pairs_and_suppression_sets` (test helper bug), `test_weighted_v1_scores_identical_to_baseline_fixtures` (preference-update expectation) |
| `npm test` | 193 tests: 171 passed, **3 failed**, 19 skipped (the three `tests/native-recommendation.test.ts` projections parsed 2.0 examples with the 1.0 zod schema) |
| schemas `--check`, both typechecks | exit 0 |

All seven failures were introduced by P2 (the P1 baseline had none). They are fixed below; none
was fixed by weakening an oracle or deleting an assertion.

### P2 validation items

Python and TS read the SAME files in `tests/fixtures/recommendation-v2/`:
`recommendation/tests/test_contract_v2_fixtures.py` (74 tests after the second review round) and
`tests/recommendation-v2-fixtures.test.ts` (9 tests). Service-level Python cases run through
`recommendation.app.execute(..., app_view=True)` (the `/app/recommend` path) in fixture data
mode with the scripted controller and a private state file. Since the second review round every
test under `recommendation/tests` (and `tests/test_native_transport.py`) runs behind the autouse
isolation guard `recommendation/tests/isolation.py` (see "Second review round" below).

| Item (spec section 13, P2) | Proof | Outcome |
|---|---|---|
| Initial request | `contract-cases.json` `initial` (Python schema + service success; TS `nativeRequestSchema`); `flow.json` round `initial` (shown IDs pinned) | pass |
| Complete previous-round feedback | `flow.json` round `complete-previous-round`: TS `nativeRequest(v1_request, sidecar)` deep-equals `native_body`; Python: the run's profile keeps all 9 browsing records with their status/duration (no loss) | pass |
| 0 s, missing, empty appointments | `contract-cases.json` `zero-seconds`, `all-missing`, `empty-appointments` at schema and service level: only positive durations become behaviour weights; with none, every item has `behavior_score` null. Flow round 2 mixes 90 s / 0 s / missing / 30 s | pass |
| Appointment dedup | one entry per listing (`appointment-dedup-one-entry-per-listing` valid; `duplicate-appointment-listing`, `duplicate-appointment-action-id`, `empty-appointment-statuses` rejected on both sides); TS `buildFeedbackSidecar` merges several actions of one listing and explodes multi-listing actions (`sidecar-cases.json` `native-adopted`) | pass |
| Illegal seconds, duplicate browsing, wrong round | rejected on both sides: `negative-seconds`, `recorded-without-seconds`, `missing-with-seconds`, `duplicate-browsing-record` (non-empty list), `browsing-missing-a-shown-listing`, `browsing-for-unshown-listing`, `duplicate-shown-listing`. Service level (Python, INVALID_FEEDBACK, nothing committed): `wrong-round-unknown-request`, `wrong-round-other-conversation`, `shown-set-differs-from-round`, `draft-appointment-status` (status outside `counted_appointment_statuses`; TS `feedbackSidecarSchema` rejects it too) | pass |
| 10 results | `test_exactly_ten_results_when_pool_is_larger`: soft-only requirements, pool 12, `result_limit` null and 10 → exactly 10, ranks 1..10, `selection.result_limit` 10. Shared fixture `ten-results.json` (an `/app/recommend` envelope with exactly 10 items): Python validates it with the response model + JSON Schema and a fresh run reproduces its IDs, scores and reasons; TS parses it with the strict mirror, checks `nativeRequest(v1_request)` == `native_body`, and projects it with `projectNative` → V1 ranks 1..10, `validateResponse` with `result_limit` 10. TS: `result_limit` 20 → 10; `result-limit-11` rejected on both sides | pass |
| Schemas `--check` | `python -m recommendation.schemas --check` exit 0; TS parses every `recommendation/examples/contracts/*.json` with the strict 2.0 mirror and checks YAML `max_result_limit` == zod `rank` max == `recommendations` maxItems == request `result_limit` max == 10; `test_native_contract_layers_agree_on_result_maximum` and `test_contract_max_equals_config_max` (P1 carry-over, marker removed) pass | pass |
| Adapter round trip without field loss | `tests/native-recommendation.test.ts`: archive keeps the native response byte-equivalent plus `contract` and `feedback_sidecar`; V1 native block carries `pool`, `selection`, `resolution`, `config_hash` and per-listing `selection_reasons` {reason, note, score_rank} equal to the native items; scores lossless; flow rounds request side equal | pass |
| Conversions | version+1 (TS flow test; `projectNative` rejects a native `requirements_version` that is not V1+1); `native:` prefix stripped (flow + adapter test); app.py rewrite to the v2 head (`flow` test checks `request.previous_state_id` == head and `transport_request` keeps the TS value); legacy head / unknown state → fresh chain (`tests/test_native_transport.py::test_contract_2_transport_rejects_1_0_and_maps_legacy_or_unknown_state_to_fresh_chain`; service level `test_legacy_v1_head_starts_fresh_v2_chain`) | pass |
| Three consecutive rounds, no loss, no accumulation of old browsing | `test_three_rounds_no_loss_no_accumulation_appointments_only_via_input`: round 3 sends an all-missing snapshot of round 2 → every item has `behavior_score` null and the combined scores equal round 1's, although round 2 had browsing | pass |
| Appointments only via input | same test: round 2 sends `fixture-07`, round 3 sends none → profile `appointment_listing_ids` empty in round 3 (native does not look appointments up) | pass |
| Same request same input replay / different input rejected | flow test (stored response; changed body → 409 IDEMPOTENCY_CONFLICT at the transport); `test_full_flow_and_restart`, `test_conflicts_atomicity_and_stale`, `test_session_conflict` | pass |
| Stale state and illegal version | STALE_REFERENCE: `test_conflicts_atomicity_and_stale`, transport test (`/recommend` keeps the native check); STALE_REQUIREMENTS, both branches of `context/manager.check_state`: `test_stale_requirements_version_backwards_or_changed_without_newer_version` (version moved backwards; changed requirements at the same version; nothing committed; unchanged-same-version and changed-newer-version accepted) and the shared case `stale-requirements-changed-same-version` at service level (the backwards branch needs a head above version 1, so it is only in the Python test); contract 1.0 body: `contract-1.0-body` case (UNSUPPORTED_VERSION) and HTTP 422 on `/app/recommend`; `v1-event-array-feedback` rejected | pass |
| Multiple processes / terminal + service sharing the DB | `test_multiple_processes_terminal_and_service_share_db_without_cross_talk`: two service subprocesses (3 rounds each with snapshots) and one `python -m recommendation.terminal` subprocess (requirements, recommend, feedback-example, feedback, recommend) write one SQLite file concurrently; every conversation keeps sequence 1..n, its own head and previous rounds from its own chain; 8 runs, unique state IDs | pass |
| Sidecar cases never INVALID_FEEDBACK | `sidecar-cases.json`: native adopted, hardGap projection (shown = native ranking), baseline round, legacy contract-1.0 round, empty native round, no previous round with cold-start exit. TS: `buildFeedbackSidecar` == expected, order-independent, always a valid native body; Python: each expected sidecar is accepted after round `v2-r1`. Also `projectNative` hardGap → `buildFeedbackSidecar` uses the native ranking (adapter test) | pass |
| Orchestrator persistence (migration 004, cold-start column) | `tests/feedback-sidecar-postgres.test.ts` (skipped by `npm test` without `TEST_DATABASE_URL`, like `postgres-session`; its own gate line below): `node --env-file=.env --import tsx --test tests/feedback-sidecar-postgres.test.ts` in a private schema of the test database (dropped afterwards): migrations 001–004 apply, 004 twice (idempotent), both JSONB columns and the three indexes exist, `loadFeedbackSidecar` over real rows equals the fixture expectation for **all six** sidecar cases (native adopted, hardGap, baseline, legacy, empty, no previous round with a stored cold-start exit; previous_round, appointments and cold_start compared), `saveState` writes and reads back `recommendation_cold_start` → **1 passed** (re-run in the second review round). `coldStartAfterAdoption` unit test: stored only when the adopted native response has `evaluated && exit_conditions_met` | pass |
| Response invariants: strict TS mirror == Python model | shared `response-cases.json` (15 cases over the `ten-results.json` response): resolved ⇔ `entity_id`, recommendations ≤ `pool.size`, `unknown_reasons` only with unknown exclusions and at most 10 reasons + `other`, unique `score_rank`, evidence closure (no unresolved ref, no unreferenced evidence), cold start active ⇒ evaluated, rank ≤ `NATIVE_MAX_RESULTS`; 5 valid controls. Python `test_response_cases` and the TS test accept/reject exactly the same cases | pass |
| Config identities in the response | `response.execution` carries `config_hash` and `selection_hash` (R2) in Python, the generated schema and the strict TS mirror; app test asserts both equal the loaded settings and the run snapshot's `selection_hash`; TS checks both are 64-hex in every generated example | pass |
| Cross-layer counted statuses | `test_ts_counted_appointment_statuses_equal_yaml` (Python reads the TS constant) and the TS test reading the YAML | pass |
| interpretFeedback 1–10 | TS test: 第十套 / 第10套 / 第六套 / 第 7 套 / 第一套 resolve; 第11套 and a stale source ID are ignored | pass |

Not run in P2 (need the live services or a browser): the env-gated live tests whose limits were
edited without being run — `native-recommendation-live` 4→10, `native-geography-live` prefix 4→5
(= its request's limit), `full-data-live` / `location-subject-live` 5→10 — are on the P7 live
checklist in `OPEN_ITEMS.md`, each to be recorded with an explicit pass/fail; likewise the browser
scripts (`scripts/test_full_data_ui.py`, `scripts/test_location_subject_ui.py` 5→10;
`scripts/test_native_recommendation_ui.py`, `evaluation/followup_workflow_browser.py` left for
P7 because their card counts depend on live data once native returns no unverified group).
The running 8092 service was not restarted; it still runs the pre-P2 code, so the TS side of this
tree must not be deployed against it until it is restarted on this tree.

### Test assertions changed on purpose (P2)

- `test_empty_verified_ranking_keeps_unknown_candidates_unverified`
  (`recommendation/tests/test_fit_controller_regression.py`): unknown hard data (`bedrooms` null on
  every row) → `no_match` instead of `needs_clarification` (R8: only criterion-level problems
  clarify). The `unverified_candidates` assertions (non-empty, each with an `unknown` result) were
  replaced by `pool.size == 0`, `pool.excluded_hard_unknown > 0`, fail + unknown == all rows,
  `pool.unknown_reasons == {"bedrooms: Missing listing attribute": n}` and the unknown-data warning.
  (The first version of this log named `test_no_match_unknown_unsupported` in
  `test_recommendation.py`; that test is unchanged.)
- `test_session_conflict`: `FEEDBACK_CONFLICT` → `IDEMPOTENCY_CONFLICT` (contract 2.0 has no
  per-event sessions; a changed observation under the same request ID is a different body).
- `test_full_flow_and_restart`: `len(recommendations) == 4` → `== min(10, pool.size) == 9`.
- `RecoveringModel` / `RepairModel` test models return the prefix up to the request's effective
  `result_limit` instead of `[:4]`.
- `test_weighted_v1_score_parity_all_pairs_and_suppression_sets`: the `legacy_dump` helper now
  wraps `evidence_coverage` in `float(...)`. `min(1, x)` returned the int `1` where the old pydantic
  float field serialised `1.0`, so 470 of 540 canonical-JSON digests differed although every score
  was identical. The oracle `baseline/score-parity.json` is unchanged; all 540 digests match.
- `test_weighted_v1_scores_identical_to_baseline_fixtures`, preference-update round only: contract
  1.0 accumulated the feedback round's browsing of `baseline-initial` into this run (its request
  had `feedback: []`). Contract 2.0 never accumulates (spec section 3), so the test now resends that
  round's snapshot as `previous_round` (what the orchestrator does; a previous round need not be
  the head). All four rounds are again bit-identical to the baseline, including the implicit
  scores; the comparison was not dropped.
- `tests/native-recommendation.test.ts` (rewritten for 2.0): the fixture no longer clears
  `unverified_candidates` (native 2.0 has none); V1 `result_limit` 5 → 10 (9 results);
  `nativeRequest` limit assertion 4 → 5 (request's own limit) plus 20 → 10; the feedback test now
  asserts that the sidecar, not the V1 event array, is forwarded; the missing-proof case drops a
  presentation listing instead of using a native unverified item.
- First review round: `recommendation/tests/test_settings.py` and the new fixture test imported
  `recommendation.app` at module import, because the app loaded the project `.env` with
  `override=True`. Second review round: the root cause is fixed in `recommendation/app.py` and the
  tests are guarded structurally (see "Second review round"); the imports stay but no longer carry
  the safety.
- `tests/native-recommendation.test.ts`: the round-trip test's `!NATIVE_GEOGRAPHY_GAP` assertion is
  kept (that request has no `LTA:`/`URA:` target); a new test asserts the warning IS present for
  `LTA:` and `URA:` targets (the warning stays until P4, see rules).
- `test_settings.py::TS_PATTERNS`: the zod `rank` / `recommendations` maxima now name
  `NATIVE_MAX_RESULTS` instead of the literal 10; the patterns accept the constant name and resolve
  it from `export const NATIVE_MAX_RESULTS=<n>` (the four-layer equality check is unchanged).
- `test_settings.py::test_app_loads_yaml_by_default_and_env_alternate` additionally asserts
  `response.execution.config_hash` / `selection_hash`.

### Rules decided in P2 (not literal in the spec)

| Rule | Reason |
|---|---|
| A request body without `contract_version` is read as contract 2.0; an explicit `"1.0"` is rejected (UNSUPPORTED_VERSION) | Same convention as contract 1.0, whose `contract_version` also defaulted; every orchestrator body (old and new) carries the version explicitly (`nativeRequest` always sent `'1.0'` and now sends `'2.0'`), so archived exchanges are not affected. Tested: `contract-version-omitted` case and `test_unversioned_body_is_read_as_current_contract_but_explicit_1_0_is_rejected` |
| The TS sidecar records browsing as `recorded` only when sample durations exist; otherwise `missing` (never `recorded 0`) | The UI has no dwell timer (plan: no real frontend timing in this phase), so "shown but not browsed" cannot be observed; the native contract still accepts `recorded 0` (terminal / fixtures) |
| The V1 native block keeps `pool`, `selection` and `resolution` as opaque records (lossless) | They are validated by the strict 2.0 schema at projection time; typing them again in the V1 schema would duplicate the native contract in the exported V1 schema |
| `projectNative` requires a presentation listing proof equal to each returned native item | Native 2.0 has no unverified list any more; the proof check now covers the returned items (`/app/recommend` derives the proofs from the same response) |
| Baseline adapter returns at most `result_limit` unverified candidates (was 5) | spec section 10 "baseline adapter unchanged except shared caps" |
| The `NATIVE_GEOGRAPHY_GAP` warning for `LTA:`/`URA:` place targets is KEPT until P4 (spec section 10 removes it "because native now supports them") | Native LTA/URA support arrives with the P4 catalog/resolver; in P2 resolution is still the OSM repository lookup, so without the warning a soft `LTA:`/`URA:` criterion would silently become unknown. P4 removes it together with its test (`OPEN_ITEMS.md`) |
| Transitional resolution of a name-only destination with several matches is `ambiguous` and the evidence is `unknown` ("ambiguous destination; supply place ID") | Already the repository behaviour (`GeographicBase.evidence`), so the resolution block and the scoring agree: hard → empty pool, `needs_clarification`; soft → unknown contribution. Pinned by `test_ambiguous_name_resolution_matches_scoring` |

### Incident during review fixing (recorded; amended in the second review round)

Ad-hoc runs that imported `recommendation.app` after setting `RECOMMENDATION_*` variables were
silently switched to the live settings, because the app loaded the project `.env` with
`override=True`: they used the **live controller** (`openai:gpt-4.1-mini`) and **database mode**
(read-only PostgreSQL retrieval of all 21,431 RENT rows and Neo4j geography lookups) and wrote
contract-2.0 rows into the live `data/.runtime/recommendation/state.sqlite`. Complete list from a
read-only query of that file (`runs_v2`, `request_identities_v2`, `app_transport_requests_v2`,
`traces`; every one is `data_mode=database`, `controller_mode=live`, `config_hash 603ed132…`,
pool 306 of 21,431):

| Conversation | Request | Committed run | Traces (rowid) | Live attempts (model calls / tokens) |
|---|---|---|---|---|
| `c1` | `r1` | sequence 1, success, 10 items | 8 (run), 11 (replay) | 1 (5 / 42,749) |
| `c1` | `r2` | sequence 2, success, 10 items | 9 (run), 10, 12, 13 (replays) | 1 (4 / 34,624) |
| `v2-fixture` | `v2-r1` | sequence 1, success, 10 items | 14 (run), 17, 20 (replays) | 1 (5 / 41,555) |
| `v2-other` | `v2-other-r1` | sequence 1, success, 10 items | 15 (run), 18 (replay) | 1 (5 / 41,438) |
| `v2-fixture` | `v2-case` | sequence 2, success, 10 items | 16, 19 (`INVALID_PROPOSAL` errors), 21 (run) | 3 (6+6+5 / 52,345+52,288+41,855) |

Total: 5 committed runs, 7 live attempts, 36 model calls, about 306,900 model tokens; the 7
replay traces made no model or database calls (they return the stored response). `c1`/`r1`/`r2`
are the `req()` identifiers of `recommendation/tests/test_recommendation.py` (budget lte 3000,
bedrooms gte 2, soft D05 — the same requirements as the fixture `setup_requirements`); the probable
source is an ad-hoc probe of the `/app/recommend` path with those bodies before the fixture test
existed. The first version of this record listed only the `v2-fixture` / `v2-other` rows and called
them "three ad-hoc runs". The fixture sidecar case `no-previous-round-cold-start-exited` uses the
pool key `c953b122…`, which equals these live rows' key only because the P2 transitional `pool_key`
is a digest of the hard requirements alone (a fixture-mode run of `setup_requirements` produces the
same key); it does not depend on live data.

The rows are isolated test conversations and stay in place (rule: do not delete runtime state); the
file was not otherwise changed (md5 `247b2503…` before and after every gate run of the second review
round). Cause removed in the second review round: `recommendation/app.py` keeps caller-set runtime
selections and an autouse guard refuses the live state and live connections in tests (below).

### Second review round (fixes)

| Finding | Outcome |
|---|---|
| `response.execution` lacked `selection_hash` (R2) | fixed: Python `Execution.selection_hash` (set from the frozen config), generated schema, strict TS mirror, regenerated examples; asserted in the app test and the TS example test |
| Importing `recommendation.app` replaced caller `RECOMMENDATION_*` settings with live `.env` values | fixed: `load_project_environment()` keeps caller-set `RECOMMENDATION_*` runtime selections (state path, data mode, controller, settings file, trace mode); the project file stays authoritative for credentials (incl. `RECOMMENDATION_SERVICE_TOKEN`). The 8092 service passes `--env-file .env`, so its values are unchanged. Guard: `recommendation/tests/conftest.py` + `isolation.py` (also used by `tests/test_native_transport.py`) force fixture data mode, the scripted controller and a tmp state file, drop credentials from the test environment, and fail on a `StateStore` under `data/.runtime` or `recommendation/.runtime` and on any database/graph connection; subprocesses get `subprocess_env()` (isolated values, no credentials). Regression tests: `test_app_import_keeps_caller_runtime_selection` (subprocess: state path, data mode, controller, settings file survive the import) and `test_isolation_guard_refuses_live_state_and_connections` |
| Incident record incomplete | fixed (section above) |
| STALE_REQUIREMENTS untested | fixed (validation table) |
| `NATIVE_GEOGRAPHY_GAP` removed before native LTA/URA support | fixed: warning restored until P4 (rules table, `OPEN_ITEMS.md`) |
| No TS proof of a 10-item native response | fixed: shared `ten-results.json` (validation table) |
| Strict TS mirror weaker than the Python model | fixed: zod refinements + shared `response-cases.json`; maxima use `NATIVE_MAX_RESULTS` |
| Wrong test named for the R8 assertion change | fixed (assertion list) |
| Five TS/JS files converted CRLF → LF | fixed: CRLF restored in `frontend/app.js`, `src/adapters/recommendation.ts`, `src/domain/explanation.ts`, `src/domain/workflow.ts`, `src/stores/domain.ts`; `git diff --numstat` now equals `--ignore-cr-at-eol` (3/3, 6/3, 22/9, 15/15, 3/3) |
| Postgres test and live-test edits not re-run in the gate | fixed: the postgres test now covers all six sidecar cases and has its own gate line; the edited live tests are on the P7 checklist (`OPEN_ITEMS.md`) |
| Ambiguous resolution vs. scoring | rejected: the repository already evaluates a name-only destination with several matches as `unknown` (not the nearest match), so resolution and status agree; pinned by a test (rules table) |

### Generic gates after P2 (second review round)

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **245 passed** (222 after the first review round + 23 new) |
| `python -m recommendation.schemas --check` | exit 0 |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 204 tests: **184 passed, 0 failed**, 20 skipped (19 env-gated live tests as before + `feedback-sidecar-postgres`, gated by `TEST_DATABASE_URL`) |
| `node --env-file=.env --import tsx --test tests/feedback-sidecar-postgres.test.ts` | **1 passed** (private schema, dropped; all six sidecar cases) |
| `npx tsx scripts/export-contracts.ts` + `scripts/validate-recommendation-examples.ts` | exports unchanged; validation passed (187 schema properties documented) |
| `scripts/import-recommendation-upstream.py --check` | fails as expected (`Upstream port fingerprint changed`); registration is P7 |
| live state `data/.runtime/recommendation/state.sqlite` | md5 unchanged (`247b2503…`) across all runs above |

New failures introduced by P2 after the second review round: none, apart from the expected upstream
fingerprint check.

## P3

Scope (spec section 13, Step 4; sections 4/5, R4, R5, R6 storage part, R13): pool cache, data_version,
gates and the compact run snapshot. New modules: `recommendation/state/pool_cache.py` (SQLite
`pool_cache` + `pool_cache_index`, per-process LRU, retention, TTL),
`recommendation/state/data_version.py` (catalog version, data_version),
`recommendation/tools/pool_builder.py` (canonical hard-criterion keys, `pool_key`,
`POOL_FORMAT_VERSION`, pool content/partition, R4 evidence IDs, rebuild with current criterion IDs)
and `recommendation/validation/selection_replay.py` (stored `selection_input` + replay dispatch
on `algorithm_version`). Edited: `repositories/listing_repository.py` (alias-free shared
`COLUMNS`, `FINGERPRINT_SQL`, `retrieve_versioned`, by-id `lookup`/`lookup_versioned` with
`listing_id = ANY(CAST(:ids AS BIGINT[]))`, fingerprint in the same REPEATABLE READ
transaction; fixture repository: deterministic row-digest fingerprint), `tools/dispatcher.py`
(`retrieve_listings` builds/reuses the pool; hard-geo evidence from the pool; soft geo only in
`get_geographic_evidence`; `selected` + `selection_input`), `validation/gate.py`,
`service.py`, `state/store.py`, `assembly.py`, `settings/pending.py` + YAML (`cache.*` now in
effect). Not started (later phases): resolver/catalog (P4), select-v1/cold start (P5), slim trace (P6).

### State before P3 (pre-existing failures)

| Gate | Result |
|---|---|
| pytest (generic gate) | 245 passed |
| `python -m recommendation.schemas --check` | exit 0 |
| both typechecks | exit 0 / exit 0 |
| `npm test` | 204 tests: 184 passed, 0 failed, 20 skipped |
| `scripts/import-recommendation-upstream.py --check` | fails as expected (registration is P7) |

No gate was failing before P3, apart from the expected upstream check.

### P3 validation items

All P3 tests are in `recommendation/tests/test_pool_cache.py` (33 tests after the review round:
28 + 5, fixture data mode, scripted controller, private state file behind the autouse isolation
guard).

| Item (spec section 13, P3) | Proof | Outcome |
|---|---|---|
| Hit/miss: a soft or feedback change reuses the pool; a hard change rebuilds | `test_soft_or_feedback_change_reuses_and_hard_change_rebuilds`: a soft change (new requirements version) gives `cache: "reused"` with the same pool_key/pool_version/data_version and a different ranking; a feedback snapshot gives `reused`; a tighter hard budget gives a new pool_key, `built` and a smaller pool; going back to the first hard criteria gives `reused` again (still among the 2 newest index entries) | pass |
| Data update and deletion trigger refresh | `test_data_update_and_deletion_trigger_refresh`: updating a row outside the pool changes data_version (`built`, same pool_version); a price change that affects eligibility removes the row from the pool; deleting a row gives `built`, `source_count` 11 and the row is gone; `test_fixture_fingerprint_is_deterministic` | pass |
| Conversations isolated | `test_conversations_are_isolated`: identical requirements in two conversations are both `built` (same pool_key and pool_version, separate index entries), a repeat in one conversation is `reused`, the LRU keys carry the conversation, and `<trace>/pool` does not resolve from another conversation; `test_state_files_do_not_leak_through_the_process_memory` (the LRU is consulted only after the SQLite index confirms an entry) | pass |
| Cached path reduces rows transferred / time (fixture, structural) | `test_cache_hit_reads_no_full_pool_and_evaluates_no_hard_geography`: over a miss and then a hit, `retrieve_versioned` is called once (the miss), the gate never calls a full `retrieve()`, there is one by-id `lookup_versioned` per run, hard geography is evaluated once (miss, 12 rows), the gate recomputes geography for the selected rows only (hard + soft criteria), and the trace `pool_stats.rows_transferred` is 12 on the miss and 0 on the hit | pass |
| Cached path reduces rows transferred / time (LIVE, read-only) | `PYTHONPATH=. .venv/bin/python evaluation/recommendation-exploration-2026-10-23/p3/measure_read_reduction.py` → `p3/read-reduction.json` (5 runs per mode; database mode, ScriptedController, no LLM; temp state file; PostgreSQL READ ONLY REPEATABLE READ, Neo4j READ sessions). Results in the table below | pass |
| Outputs belong to the pool version | `test_outputs_belong_to_the_pool_version_and_partition_invariant` (5 scenarios incl. hard geography, an empty pool and an unsupported hard criterion): `digest(<trace>/pool content) == response.pool.pool_version`; every returned listing is a pool row and equal to it; snapshot `pool.pool_version` equals the response's. `replay_gate` rejects a stored response whose `pool.pool_version` differs from the snapshot (`test_compact_snapshot_and_replay_gate`) | pass |
| Partition invariant (fail > unknown > eligible) | same test: `source_count == size + excluded_hard_fail + excluded_hard_unknown == 12` in every scenario; fixture-12 (no coordinates, so its distance is unknown, AND price 3200 > 3100) counts as `fail`, not `unknown`; a geography-only hard criterion gives exactly 1 unknown with its reason; `test_unknown_reasons_are_capped` (10 keys + `other`); the P2 test `test_empty_verified_ranking_keeps_unknown_candidates_unverified` still passes | pass |
| Criterion-ID rebuild on hit | `test_criterion_ids_rebuilt_on_hit`: renaming the hard distance criterion (and changing its weight) reuses the pool (same pool_key); criterion results and evidence carry the NEW id (the hard reference resolves to evidence of exactly `school-within-1500`, tightened in the review round); the old id is gone; evidence IDs per listing are identical across both runs (no criterion_id, no timestamp, R4); rebuilt evidence has one `observed_at` (the pool build time) | pass |
| Canonical keys and pool_key (R4) | `test_canonical_criterion_keys_and_pool_key`: 'in' sorted and deduplicated, property_type casefolded, 3000 == 3000.0; criterion_id, weight, order and soft criteria do not change the key; the catalog version, the geography settings digest and duplicate hard criteria are handled; resolved entities (P4 hook) replace the target; `test_pool_format_version_is_part_of_the_key` | pass |
| pool_version without volatile timestamps | `test_pool_version_has_no_volatile_timestamps`: two independent state files, same pool_version; cached facts contain no `observed_at` and no `criterion_id` | pass |
| data_version SQL (R5): alias-free, epoch updated_at, same REPEATABLE READ transaction as the by-id lookup | `test_fingerprint_sql_is_alias_free_and_covers_every_mapped_column` (every ListingSummary column + currency + dataset_scope, `extract(epoch from updated_at)`, ordered `string_agg`, RENT filter); `test_sql_by_id_lookup_and_fingerprint_share_one_repeatable_read` (one connection: READ ONLY → timeout → REPEATABLE READ → `ANY(CAST(:ids AS BIGINT[]))` with numeric IDs only → fingerprint; no keyset/full read). Live: data_version is identical between the standalone fingerprint (lookup) and the in-transaction one (build), since every hit in the live run matched (`3c9362c8a21a…` across all 40 runs) | pass |
| Fingerprint/catalog failure → cache bypassed + warning, never served unverified | `test_fingerprint_failure_bypasses_the_cache_with_a_warning` (`cache: "bypassed"`, `data_version: "unavailable"`, warning, full rebuild with 12 rows transferred, no `unavailable` index entry; after recovery the earlier verified entry is `reused`); `test_catalog_version_failure_bypasses_the_cache`; `test_cache_disabled_builds_every_time` | pass |
| TTL (0 = none) | `test_ttl_zero_means_no_expiry_and_positive_ttl_expires`: committed `ttl_seconds: 0` → still a hit after 10^7 s; `ttl_seconds: 60` → a hit at 59 s, a rebuild at 61 s (`outcome: expired`; a hit does not extend the TTL), then a hit again | pass |
| Retention per conversation; `<trace>/pool` while retained, else STALE_REFERENCE | `test_retention_index_bound_and_evicted_pool_reference`: 6 hard pools in one conversation → 2 index entries, the 4 newest contents retained; the oldest run's `<trace>/pool` → STALE_REFERENCE "Pool evicted"; the newest resolves; replay of the evicted run's request still returns the stored response (replay_gate needs no pool); its hard criteria are rebuilt (`built`) | pass |
| Bounded in-memory LRU (`cache.memory_max_entries`), filled only after commit | `test_memory_lru_is_bounded` (unit + service with bound 2); `test_memory_is_filled_only_after_commit`: a run that fails the gate on every repair attempt leaves the LRU, `pool_cache` and the index empty; its error trace has `pool_ref.persisted: false` and `<trace>/pool` → "not persisted" | pass |
| final_gate: proposal == selection, data_version re-query, by-id re-read, hard criteria + geo recomputed for the selected rows only | `test_final_gate_requires_exact_selection_and_unchanged_selected_rows` (a reordered proposal is rejected); `test_final_gate_detects_data_change_and_rebuilds_once` (data changes between build and gate → STALE_EVIDENCE retryable, the pool is rebuilt, the second gate passes with the new data_version); `test_final_gate_rebuilds_at_most_once` (data changing at every gate → error after exactly one rebuild, nothing committed); `test_data_change_discards_the_pool_for_a_controller_that_does_not_retrieve` (review round: the service discards the stale pool on a data change, so a controller that re-proposes the old ids or only re-ranks gets STALE_REFERENCE / INVALID_PROPOSAL and nothing is committed); geo recompute for the selected rows only, in the hit test above; `test_final_refresh_timeout_does_not_commit` (retargeted, see below) | pass |
| Compact run snapshot; replay_gate keeps working for the current (prefix) selection | `test_compact_snapshot_and_replay_gate`: the snapshot `pool` holds only {pool_key, pool_version, data_version, cache}, there are no rows/evidence/scores/ranking/validation keys outside `configuration`, run data < 8 kB, and `selection_input` (`ranked-prefix-v1`, K, head [id, score, price]) replays to the returned IDs; replay rejects a changed score, swapped listings, a reordered head, another pool_version and an unknown algorithm; `test_replay_gate_checks_geographic_evidence_of_items` (stored evidence that no longer satisfies a hard distance criterion is rejected); `test_p2_era_full_snapshots_still_replay` | pass |
| app.py presentation from the pool summary; PoolSummary populated for real | `test_app_presentation_counts_from_pool_summary`: counts {assessed 12, eligible 9, unverified 1, excluded 2} == the response pool; state source/assessed/prefilter = 12/9/3; a second request is `reused` with identical counts | pass |
| Index eviction keeps the newest built entries (spec section 5) | `test_index_keeps_the_newest_built_entries` (review round): budgets 2000 built, 2200 built, 2000 reused, 2400 built leave the index {2400, 2200}; 2200 is then reused and 2000 rebuilt (an LRU index would have kept 2000); content retention keeps the reused content | pass |
| Run transaction keeps its cross-process write lock | `test_pool_cache_keeps_the_run_transaction_and_its_write_lock` (review round): `store.db.in_transaction` stays true after `PoolCache()` inside `store.transaction()`, and a second connection's `BEGIN IMMEDIATE` on the same file fails with "database is locked" while a run is in flight | pass |
| Criteria sharing a canonical key | `test_criteria_sharing_a_canonical_key_each_get_their_own_evidence` (review round): a hard and a soft distance to the same target share the R4 evidence ID, the response has one Evidence per (evidence_id, criterion_id), every reference resolves to evidence of its own criterion, replay_gate passes | pass |
| Destination-name variants never share a pool (transitional key) | `test_destination_name_variants_never_share_a_pool` (review round): in one conversation 'fixture school' → 'Fixture School', the reverse, and 'Fixture School' → ' Fixture School ' are each `built` with a different pool_key and give the same status/pool size/ids as an uncached run; an identical name is still `reused` | pass |
| `cache.max_entries_per_conversation` vs `pool_retention_per_conversation` (P1 open item) | Decided: **kept separate**. The index bounds which pools may be REUSED; retention bounds which older runs' pools stay INSPECTABLE via `<trace>/pool`. The loader keeps retention ≥ max entries, and eviction never deletes content an index entry points to. Documented in the `state/pool_cache.py` docstring, the YAML comment and INVENTORY section C; exercised by the retention test (2 vs 4) | decided |

### Live read-reduction measurement (read-only; database mode, ScriptedController, temp state)

Live data: 21,431 RENT rows, data_version `3c9362c8a21a…`. Rows = listing rows transferred to
the process (pool build + final-gate re-read). Wall = one request end to end, including
per-request repository construction (as in app.py). Median of 5 runs, [min, max].

| Scenario (pool size) | Mode | Rows transferred | Wall ms |
|---|---|---|---|
| listing attributes: price ≤ 3000, bedrooms ≥ 2 (306) | uncached (`cache.enabled: false`) | 21,441 | 927 [916, 1240] |
| | built (miss: build + store) | 21,441 | 1,070 [1,055, 1,099] |
| | reused (hit, warm LRU after the first) | 10 | 374 [362, 388] |
| | reused, LRU cleared (SQLite content) | 10 | 375 [368, 387] |
| hard geography: price ≤ 4000, ≤ 1500 m to a Hospital (4,684) | uncached | 21,441 | 7,734 [7,637, 8,674] |
| | built | 21,441 | 8,001 [7,973, 8,276] |
| | reused | 10 | 1,442 [1,436, 1,511] |
| | reused, LRU cleared | 10 | 1,282 [1,270, 1,292] |

Reading: a hit transfers 0 pool rows (only the 10 selected rows are re-read by ID in the gate)
and does no hard-geo evaluation (the miss spends 5.5–6.7 s on it). The hit is 2.5× faster for
listing attributes and 5.4× faster for hard geography. A miss costs more than the uncached path
(+15 % listing attributes, +3 % hard geography: one extra fingerprint query for the lookup plus the content write; the build-transaction and gate fingerprints run in both paths). Each fingerprint query costs 147–173 ms
server-side (two per hit: lookup + gate; documented limit, spec R5). The rest of the hard-geography
hit time was profiled on one live hit (2.23 s under cProfile): trace redaction ≈ 1.0 s and
per-tool snapshot digests, because the trace format still embeds the 4,684 pool rows in every
tool step (slim trace is P6); fingerprints 0.35 s; final gate 0.2 s. The live state file
`data/.runtime/recommendation/state.sqlite` kept md5 `247b2503…` before and after.

### Test assertions changed on purpose (P3)

- `test_recommendation.py::test_reference_resolution_and_gate_replay`: `len(pool["rows"]) == 12`
  → `len(pool["rows"]) == first.pool.size and pool["partition"]["source_count"] == 12`.
  `<trace>/pool` now resolves the pool content from `pool_cache` (eligible rows only; spec
  section 9/R13); the 12 source rows are in its partition.
- `test_settings.py::test_weighted_v1_scores_identical_to_baseline_fixtures`: evidence IDs
  follow R4 (digest of canonical key + listing + value + method; no criterion_id/timestamp), so
  the 4d36507 IDs cannot match. The new helper `_remap_evidence_ids` first asserts that every
  current evidence_id follows the R4 formula, then maps baseline IDs to current IDs by
  (listing_id, criterion_id). Every other field (scores, contributions, criterion results,
  evidence values/methods) is still compared exactly, for all four baseline rounds.
- `test_policy_controller.py::test_final_refresh_timeout_does_not_commit`: the clock jump moved
  from the second `retrieve()` (formerly the gate's full re-read, now the pool build's internal
  read) to the gate's by-id refresh `lookup_versioned`, plus `calls == 1` so the timeout provably
  happens in the final refresh. The assertions LOOP_EXHAUSTED / nothing committed are unchanged.
- Regenerated `recommendation/examples/contracts/*.json`: only `pool.{pool_key, pool_version,
  data_version}`, `selection.cold_start.pool_key`, `state.{assessed_count, prefilter_excluded}`
  (now pool size / fail + unknown) and volatile IDs/durations changed (field-path diff against
  the previous files). `scripts/export-contracts.ts`: exports unchanged;
  `scripts/validate-recommendation-examples.ts`: passed.

### Rules decided in P3 (not literal in the spec)

| Rule | Reason |
|---|---|
| Two SQLite tables: `pool_cache` (content by conversation + pool_version, with the pool_key/data_version it was built for) and `pool_cache_index` ((conversation, pool_key, data_version) → pool_version) instead of one table | R13 separates content retention from the index lookup; one content row may serve several index entries (e.g. a data-only change that leaves the pool identical) |
| pool_version = digest of the content WITHOUT pool_key and data_version | A data change outside the pool keeps the same pool_version (seed stability for P5); the content still lists its canonical hard keys |
| Pool content is retained even when the cache is disabled/bypassed (never indexed then) | `<trace>/pool` keeps working (R13); "never serve a cached pool unverified" concerns the index only |
| Any fingerprint or catalog-version failure in a run → `bypassed` (even if the in-transaction fingerprint later succeeds) | Conservative reading of R5; the bypass gate falls back to a full re-read compared with the digest of every source row |
| "Rebuild once" = at most one STALE_EVIDENCE-by-data-change repair per request (`gate.DataChanged`), still inside `controller.repairs` | spec section 5 "on rebuild path once" |
| Duplicate identical hard criteria count once in pool_key | Same constraint, same pool |
| Until P4, the geo target in the canonical key is the request's destination_id / destination_name / category (verbatim since the review round, see below); the catalog version comes from Neo4j `Dataset` (dataset_id, snapshot_sha256) or the fixture places | The task allows the current repository's version source until P4; both have explicit extension points (`criterion_key(c, resolved)`, `catalog_version()` method) |
| Run snapshot keeps `format: 2` and `profile`; replay dispatches on the presence of `selection_input` (P2-era v2 snapshots with an embedded pool still replay) | Spec section 4 names format 2; replays of already committed v2 runs must not break |
| `state.assessed_count` = pool size and `prefilter_excluded` = fail + unknown (was: rows after the cheap prefilter) | The pool is the scored set; hard geography is now part of the partition |
| `retrieve_listings` keeps its name and now builds or reuses the pool | Tool renaming and the prompt belong to P4 |
| Index eviction = newest BUILT entries (a hit does not refresh the index position; `pool_cache_index.used_seq` holds the build sequence); content retention = most recently USED contents (a hit counts) | Review round. Spec section 5 says "keep max_entries_per_conversation newest" for the index; R13 does not fix the retention order, and by-use keeps the pools of the latest runs inspectable via `<trace>/pool`. YAML comments and INVENTORY section C updated |
| The transitional geo target in the canonical key is verbatim (no strip/casefold); `POOL_FORMAT_VERSION` bumped to `pool-v2` | Review round (major). The repositories resolve names with their own rules (fixture exact match, Neo4j `toLower` without trim), so a normalised key let two differently resolving names share a pool. Verbatim can only cost an extra miss. The bump keeps old `pool-v1` index entries (built under casefolded names) from being hit |
| On a data change (`DataChanged`) the service discards the dispatcher's pool (`Dispatcher.invalidate_pool`) before the repair attempt; the live repair message then tells the model to call retrieve_listings again | Review round. "Rebuild at most once" no longer depends on the controller choosing to re-retrieve: without a new retrieve_listings every other tool returns STALE_REFERENCE and the gate rejects the proposal |
| Response evidence: one Evidence per (evidence_id, criterion_id) | Review round. R4 evidence IDs are criterion-free, so criteria with one canonical key share an ID; each referencing criterion gets its own entry (TS `projectNative` looks evidence up by (listing_id, criterion_id)). Contract comment in `contracts/response.py`; OPEN_ITEMS notes it for MIGRATION.md. replay_gate resolves by (id, criterion), falling back to id for responses stored before the change |

### P3 review round (fixes)

The reviewer's probe scripts (session scratchpad, not committed) were re-run after the fixes: `probe_lock.py` leaves only
`r1` committed (the interleaved writer cannot get in while run A holds the lock);
`probe_name.py`/`probe_name2.py` give `built` for the second name variant with the uncached
outcome (success with 11 pool rows, or needs_clarification); `probe_dupkey.py` gives response
evidence for both `hard-school` and `near-school` (20 entries). Each new test was mutation-checked:
re-introducing the old code (DDL in `PoolCache.__init__`, index bump on hit, casefolded name,
id-only evidence dedup, no pool invalidation) makes exactly that test fail.

| Finding | Outcome |
|---|---|
| blocker: `PoolCache.__init__` ran `executescript` (implicit COMMIT) inside the run's `BEGIN IMMEDIATE` | fixed: no DDL in `PoolCache` (tables come from `StateStore.__init__`); test above |
| major: casefolded/trimmed destination_name in the key vs repository resolution rules | fixed: verbatim target, `pool-v2`; test above |
| minor: index eviction was LRU, spec says newest | fixed: index by build order; retention by use recorded as a decided rule; test above |
| minor: hedged assertion in `test_criterion_ids_rebuilt_on_hit` | fixed: asserts exactly `school-within-1500` |
| minor (two findings): rebuild-once depended on the controller re-retrieving | fixed: service invalidates the pool on `DataChanged`; live repair instruction adjusted; test above |
| minor (two findings): shared evidence ID listed once under the first criterion | fixed: one Evidence per (evidence_id, criterion_id); contract comment; test above |

Regenerated `recommendation/examples/contracts/*.json` again: compared field by field with a
regeneration under the pre-fix code, only `pool.pool_key`, `pool.pool_version`,
`selection.cold_start.pool_key` (format bump) and volatile IDs/durations changed; evidence is
unchanged (no example has two criteria with one key). `scripts/validate-recommendation-examples.ts`:
passed; `scripts/export-contracts.ts`: exit 0 (TS contracts untouched).

### Generic gates after P3

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **278 passed** after the review round (245 before + 28 new in P3 + 5 new in the review round; 273 at first review) |
| `python -m recommendation.schemas --check` | exit 0 (no contract change in P3) |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 204 tests: **184 passed, 0 failed**, 20 skipped (unchanged) |
| `npx tsx scripts/export-contracts.ts`, `scripts/validate-recommendation-examples.ts` | exports unchanged; validation passed |
| `scripts/import-recommendation-upstream.py --check` | fails as expected (`Upstream port fingerprint changed`); P3 adds `repositories/listing_repository.py` to the files to register in P7 (INVENTORY section M) |
| live state `data/.runtime/recommendation/state.sqlite` | md5 `247b2503…` unchanged |

New failures introduced by P3: none, apart from the expected upstream fingerprint check.
Side note: the sidecar fixture case `no-previous-round-cold-start-exited` uses the P2-format
pool key `c953b122…` as an opaque value. It no longer equals any real key (the key format
changed); the test only needs an opaque value. pyflakes was installed temporarily into
`.venv` for linting and then uninstalled.

## P4

### P4 part 1: geography core (catalog, normalisation, resolver, distances)

Scope (spec section 6, R1c, R10 URA part, R12; section 13 P4 items that do not need the model
tools): new package `recommendation/geography/` — `catalog.py` (process-level catalog: 188 LTA
stations / 613 exits from `knowledge/locations/lta-stations.json`, 55 URA areas from
`ura-areas.json` + polygons from `data/datasets/ura_planning_areas.geojson`, both datasets
hash-checked like `src/adapters/locations.ts`, 5 regions = union of their areas, `aliases.json`
+ `area-aliases.json`, OSM places from Neo4j `MATCH (p:Place) WHERE p.active = true` or the
fixture places; static part cached by file stat, places cached by (source, Neo4j
`Dataset.snapshot_sha256` / fixture digest); `version_of` = catalog version for pool_key and
data_version), `normalize.py` (port of TS `normalizePlace` with explicit JS regex semantics, TS
NUS/NTU map, explicit-station/mode detection), `resolver.py` (deterministic resolution, R12
reasons, `ResolutionResult` mapping), `distance.py` (haversine R = 6,371,008.8 m everywhere,
numpy nearest-point matrix, even-odd point-in-polygon over all rings, nearest-edge distance in a
local equirectangular projection, bbox + block pruning, ~1e6-pair chunks), `evidence.py` (facts
per criterion and listing; station = nearest recorded exit or exit centroid per
`geography.station_distance_method`, the exit recorded as provenance only).
Wired in: `repositories/geographic_repository.py` (rewritten on the native catalog; NEAR_PLACE
relations and the 1 m tolerance path removed), `tools/dispatcher.py` (`resolve_entities` stage
before the pool; resolutions passed to the pool build, soft evidence and the gate),
`tools/pool_builder.py` (pool_key / canonical keys use the resolved entity id + kind;
`POOL_FORMAT_VERSION` → `pool-v3`), `validation/gate.py`, `assembly.py` (response `resolution`
from the native stage; clarifications name the criterion and explain the reason),
`state/data_version.py` (catalog version from the native catalog), settings markers
(`geography.*` and `entity_resolution.max_candidates` now in effect). TS: the
`NATIVE_GEOGRAPHY_GAP` warning is removed from `src/adapters/native-recommendation.ts`.
Not in this part (P4 part 2): `search_places` / `describe_fields` / `nearest_mrt` /
`distance_to` / `nearby_amenities` / `district_membership` tools, `bind_entity`, pipeline tool
renaming and stage invalidation, controller prompt/budgets, terminal resolution commands.

#### State before P4 (pre-existing failures)

Taken from the P3 record above (not re-run on the pre-P4 tree, which this phase edits in
place): pytest generic gate 278 passed; `schemas --check` exit 0; both typechecks exit 0;
`npm test` 204 tests, 184 passed, 0 failed, 20 skipped; `scripts/import-recommendation-upstream.py
--check` fails as expected (registration is P7). The first pytest run after the P4 code change
showed 275 passed + exactly the 3 intended rule-change failures listed under "Test assertions
changed on purpose" below; no other test failed.

#### P4 validation items covered by part 1

Python tests are in `recommendation/tests/test_geography.py` (21 tests, fixture mode where a
service is involved). Live checks: `PYTHONPATH=. .venv/bin/python
evaluation/recommendation-exploration-2026-10-23/p4/live_geography.py` →
`p4/live-geography.json` (read-only: PostgreSQL READ ONLY REPEATABLE READ, Neo4j READ sessions,
ScriptedController, temporary state file; live state `data/.runtime/recommendation/state.sqlite`
md5 `247b2503…` before and after).

| Item (spec section 13, P4) | Proof | Outcome |
|---|---|---|
| Exact match | `test_exact_match_every_area_region_and_station`: all 55 areas by name and by first alias, all 5 regions by name and alias, all 188 stations by their names (`unique_exact_station_match`), BUKIT PANJANG / CHOA CHU KANG / PUNGGOL `… MRT` → `LTA:MRT:*`, `… LRT` → `LTA:LRT:*` (TS `tests/locations.test.ts` parity); a valid `URA:AREA:CLEMENTI` ID is verified and reused (`method id`, `verified_id`). Live: Clementi → `URA:AREA:CLEMENTI`, VivoCity → `OSM:relation:2353588`, `OSM:way:54519165` verified | pass |
| Alias | `test_alias_resolution_area_vs_station`: 金文泰 / Clementi / 克莱门特 / 金文台 → `URA:AREA:CLEMENTI`; 'Clementi MRT' / ClementiMRT / 金文泰地铁站 / 克莱门特MRT → `LTA:MRT:CLEMENTI`; 西区 → `URA:REGION:WEST`; place alt_names / full_name / name_local resolve. Service: `test_service_resolves_lta_and_ura_targets…` (武吉知马 reuses the pool of `URA:AREA:BUKIT_TIMAH`). Live: same results, 新加坡国立大学 → NUS | pass |
| Multiple candidates (never first-pick) | *(P4 review round: superseded, see below)* `test_multiple_candidates_are_ambiguous_never_first_pick`: 'Bukit Panjang Station' → ambiguous, candidates `LTA:LRT:BUKIT_PANJANG`, `LTA:MRT:BUKIT_PANJANG`, reason `multiple_exact_matches (2)`; `max_candidates=1` keeps 1 candidate; duplicate OSM names ambiguous; 'Bukit Panjang' (no station word) → the URA area (TS order). Service hard → needs_clarification naming the criterion and listing both candidates (`test_service_unresolved_hard_target…`); `test_geography_and_resolution_settings_are_in_effect` (YAML `max_candidates` bounds the service response). Live: 'Cold Storage' → ambiguous (38 matches, 5 candidates listed) | pass |
| No match | `test_no_match_and_absent_place_are_unresolved_not_invented`: Atlantis, 'National University' (a substring is never accepted) → unresolved `no_exact_match`, no candidates | pass |
| NUS ISS-like absent place → unresolved (not invented) | same test: 'NUS ISS', 'NUS-ISS' → unresolved while 'NUS' / 'nus' / 新加坡国立大学 / 新国立大学 resolve through the TS name map to `OSM:way:54519165`. Service: hard 'NUS ISS' → `needs_clarification`, pool 0, all 12 listings hard-unknown, clarification "Hard criterion 'geo' (distance): destination unresolved (no catalog entity matches the name exactly)."; soft 'NUS ISS' → success, criterion unknown on every item. Live: 'NUS ISS' / 'NUS-ISS' unresolved; the end-to-end hard request → needs_clarification, 21,431 hard-unknown. (The live graph does contain 'Institute of Systems Science' = `OSM:relation:4548824`; it resolves only by that exact name.) | pass |
| Missing coordinates | `test_area_and_region_distances` (a listing without coordinates → value None + reason, never 0); `test_service_missing_coordinates_are_unknown_not_fail`: fixture-12 is `excluded_hard_unknown` (not fail), `unknown_reasons` names it, status success (R8: no criterion-level problem); P3 `test_outputs_belong_to_the_pool_version_and_partition_invariant` still passes | pass |
| Unsupported namespace | `test_id_namespaces_invalid_ids_and_conflicts`: `GOOGLE:place:1` → unresolved `unsupported_namespace`; with a name → name search (`id_then_name`, reason `unsupported_namespace; unique_exact_area_match`). Service: hard `GOOGLE:1` → needs_clarification, reason `unsupported_namespace` | pass |
| Invalid ID → name search; ID vs name conflict → ambiguous (spec section 6 / task list) | same test: `URA:AREA:ATLANTIS` → `id_not_found` (with a name: resolved by name, reason recorded); `LTA:EXIT:24671` → `exit_not_destination` (an exit is never the entity, R1c); ID `URA:AREA:CLEMENTI` + name 'Bedok' → ambiguous `id_name_conflict` with both candidates; agreeing name → resolved `id+name`; an ID that is one of an ambiguous name's candidates → resolved. Resolver API only: contract 2.0 still allowed one target per criterion (OPEN_ITEMS) | partial at part 1 (resolver API only; unreachable end-to-end) → **pass end to end** after the P4 review round (below) |
| Generic MRT category resolves | *(P4 review round adds the name path, see below)* `test_categories` (MRTStation, LRTStation, OSM categories resolved; Park/Clinic without located places → `category_has_no_entities`; Gym / BusStop → `unsupported_category`); service `MRTStation ≤ 600 m` succeeds with `LTA:MRT:*` destinations. Live: `category:MRTStation` → "nearest of 147 LTA MRT stations"; end-to-end hard MRTStation ≤ 600 m → success, pool 3,698 | pass |
| Nearest MRT uses station + nearest exit | `test_generic_mrt_category_uses_nearest_exit_and_excludes_lrt`: value == brute-force minimum over every MRT exit (scalar haversine), destination = the station, the exit used only in the record's `nearest_exits` (provenance); a point at an LRT-only exit (Bakau) gets an MRT station; `LRTStation` gives Bakau at 0 m. `test_station_nearest_exit_and_centroid_alternative`: specific station = min over its exits; `exit_centroid` alternative = distance to the exit mean; invalid method rejected. The `nearest_mrt` TOOL is part 2 | pass (distance semantics); tool: not_done (part 2) |
| Normalization parity fixture | `tests/fixtures/place-normalization.json` (390 cases: hand-written edge cases for JS-vs-Python regex semantics — U+0085, U+001F, U+FEFF, trailing newline, final sigma, dotted İ, full-width — plus every station/area name and every alias; expected values generated from TS). TS: `tests/native-geography-parity.test.ts` (normalizePlace); Python: `test_normalization_matches_shared_ts_fixture` — 390/390 equal | pass |
| Region distance performance | `test_region_distance_performance_8631_points` (synthetic 8,631 points over Singapore): CENTRAL 0.129 s, EAST 0.068 s, NORTH 0.058 s, NORTH-EAST 0.060 s, WEST 0.054 s (assert < 5 s; pruned == brute force on 300 points). Live, the 8,631 real listing coordinates (SQL): CENTRAL 120 ms, EAST 53 ms, NORTH 59 ms, NORTH-EAST 49 ms, WEST 23 ms; area `URA:AREA:CLEMENTI` 22.5 ms (259 inside, the same 259 the graph lists); nearest MRT exit over 541 MRT exits 34 ms; nearest Hospital 4 ms | pass |
| Polygon holes / MultiPolygon correctness (R10) | `test_polygon_holes_and_multipolygons_on_synthetic_shapes`: a point in the ring → 0, points in the hole → outside with exact distance to the hole edge, MultiPolygon members → 0 and the gap point → distance to the nearer square; north of an edge equals the haversine to the foot point (rel 1e-6); pruned == unpruned | pass |
| Distance accuracy vs TS (documented in `distance.py`) | `tests/fixtures/geography-distance-parity.json` (80 points × 4 URA shapes + 2 stations, from TS `distanceToEntity` / turf): `test_distances_match_ts_fixture` (same inside/outside verdict; outside ≤ 1e-4 relative and ≤ 2 m; stations equal to 1e-9) and the TS test re-checks the fixture. Ad-hoc over 600 random points × 8 shapes (scratchpad `p4/turf_cmp.mts`): inside/outside agreement 100 %; max abs difference 1.04 m at up to 42 km; max relative 5.4e-5; stations ≤ 6e-13 relative | pass |
| URA membership vs the graph (live) | `live_geography.py`: Python point-in-polygon over the 8,631 coordinate listings agrees with Neo4j `IN_PLANNING_AREA` for 8,631/8,631 (8,630 inside exactly one area, the same one listing outside every polygon) | pass |
| Hard unresolved/ambiguous → unknown → excluded, needs_clarification naming the criterion (R8); soft → unknown | `test_service_unresolved_hard_target…`, P2/P3 `test_ambiguous_name_resolution_matches_scoring` (hard + soft) | pass |
| pool_key uses the resolved entity; catalog version | `test_service_resolves_lta_and_ura_targets_and_uses_resolved_entity_in_pool_key` (ID and Chinese alias share one pool: `reused`); `test_destination_name_variants_share_a_pool_only_when_they_resolve_alike`; `test_place_catalog_cache_and_catalog_version` (place change → new catalog version; Neo4j snapshot change → new version and a reload; no Dataset → VersionUnavailable); `test_geography_and_resolution_settings_are_in_effect` (station method changes pool_key via the geography digest) | pass |
| ResolutionResult populated in the response | service tests above (`status`, `entity_id`, `entity_kind`, `method`, `reason`, `candidates`); live end-to-end responses carry the resolution of every geographic criterion | pass |
| `NATIVE_GEOGRAPHY_GAP` removed | `src/adapters/native-recommendation.ts`; TS test "LTA:/URA: place targets go to native as destination IDs and no longer raise NATIVE_GEOGRAPHY_GAP" (real `LTA:MRT:CLEMENTI`, `URA:AREA:CLEMENTI`, `URA:REGION:WEST` → `destination_id`, no warning) | pass |
| End-to-end live (database mode, cache disabled, one run each) | hard Hospital ≤ 1,500 m + price ≤ 4,000 → success, pool 4,684 (identical to P3's pool size), hard-geo evaluation 122 ms (P3: 5.5–6.7 s through Neo4j relations), wall 2.37 s (P3 uncached median 7.7 s; not a controlled comparison: one run, different code paths); hard MRTStation ≤ 600 m + soft 金文泰 → success, pool 3,698; hard `URA:REGION:WEST` (0 m) → success, pool 1,834 | pass |
| Malicious listing/place text | not tested in part 1 (resolution and distances are code-only and never read listing text, but the model-facing tools that could carry such text are part 2) | not_done (part 2) |
| LLM-returned ID not from tools rejected | `bind_entity` / tool scoping are part 2 | not_done (part 2) |
| Terminal resolution without TS | the service resolves natively in fixture and database mode (tests above, live script), but the terminal resolution commands (`show-resolution`, entity ambiguity transcript) are part 2 / P6 | not_done (part 2) |

#### Test assertions changed on purpose (P4 part 1)

- `test_settings.py::_remap_evidence_ids` (baseline parity test): the expected R4 evidence ID is
  computed with `criterion_key(c, resolved)`, `resolved` taken from the response's own
  `resolution` (geo keys now carry the resolved entity). Every other field — scores,
  contributions, criterion results, evidence values, methods, unknown reasons — still matches
  the 4d36507 baseline exactly, for all four rounds (distance values are recomputed with the
  same scalar haversine).
- `test_terminal_repositories.py::test_neo4j_schema_mapping_and_missing_edges`: the mocked
  Place row gains `name`/`category` (category targets resolve through the catalog); the
  assertions "the second query is the NEAR_PLACE relations query with integer ids" became "no
  Listing/NEAR_PLACE query is issued; destination `OSM:node:1`; mapping_status
  `not_applicable`" (the 1 m tolerance path and graph edges are gone, spec section 6). The
  value/method assertion is unchanged.
- `test_pool_cache.py::test_destination_name_variants_never_share_a_pool` →
  `test_destination_name_variants_share_a_pool_only_when_they_resolve_alike`: the P3 transitional
  verbatim rule is replaced by the R4 resolved-entity rule. Kept intent: the second request's
  outcome equals an uncached run; now 'fixture school' / ' Fixture School ' share the pool
  (`reused`, same key) and an unresolved spelling ('Fixture Schools') gets a new key (`built`).
  The `Counting` helper forwards the new keyword arguments of `evidence()`.
- `tests/native-recommendation.test.ts`: the interim test "LTA:/URA: place targets keep the
  NATIVE_GEOGRAPHY_GAP warning…" is replaced (see table). In the round-trip test a missing
  newline had turned the `最多10套` assertion into part of a comment (pre-existing defect); it
  is on its own line again and passes.
- Regenerated `recommendation/examples/contracts/*.json`: field-path diff against the previous
  files shows only `pool.{pool_key,pool_version,data_version}`, `selection.cold_start.pool_key`
  (pool format `pool-v3` + new catalog version) and volatile IDs/durations/state refs.
  `scripts/validate-recommendation-examples.ts`: passed; `scripts/export-contracts.ts`: exit 0
  (no TS contract change).

#### Rules decided in P4 part 1 (not literal in the spec)

| Rule | Reason |
|---|---|
| ~~Explicit station request also on `station`, 站, 轻轨, 捷运 (TS: MRT/LRT/地铁站/地铁 only)~~ — **reverted in the P4 review round** (TS rule; pending user decision in OPEN_ITEMS) | Otherwise "Bukit Panjang Station" silently becomes the URA area; with it the MRT/LRT twins are reported ambiguous (task example). `normalizePlace` is unchanged (parity fixture) |
| Several exact area/region matches → ambiguous (TS falls through to stations) | Spec: several hits → ambiguous, never a first pick |
| Fixture places keep their `fixture-` IDs as an extra namespace of the fixture place catalog | Existing tests/examples use `fixture-school`; the real namespaces `OSM:`/`LTA:`/`URA:` apply in both modes; any other ID is `unsupported_namespace` (e.g. the old test target `absent`) |
| Reported point distances are recomputed with the scalar `ranking.metrics.haversine` after the numpy nearest selection | Identical values to the pre-P4 path (baseline parity) and one canonical formula |
| Area distance method label `ura_polygon.zero_inside_nearest_edge_local_equirectangular`; OSM point label `coordinate_haversine` kept; station labels `haversine.nearest_recorded_exit` (TS) / `haversine.exit_centroid` | Labels state the measurement; unchanged facts keep identical evidence |
| `Evidence.mapping_status` is `not_applicable` and `coverage_m` is null for every native fact | Facts no longer depend on graph mappings or edge radii |
| Exit provenance lives in the repository record (`nearest_exits`, trace tool snapshot), not in Evidence | The Evidence contract has no field for it (contract unchanged in part 1); the part 2 `nearest_mrt` tool returns it |
| `retrieve_listings` runs the entity-resolution stage implicitly (`Dispatcher.resolve_entities`) | Clean extension point for the part 2 `resolve_entities` tool without changing the current tool list |
| Resolution reasons are codes (`unsupported_namespace`, `id_not_found`, `no_exact_match`, `multiple_exact_matches (n)`, `id_name_conflict`, …); clarifications translate them | R12 names the code literally; users get readable text |

#### Generic gates after P4 part 1

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **299 passed** (278 before + 21 new in `test_geography.py`) |
| `python -m recommendation.schemas --check` | exit 0 (no contract change) |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 206 tests: **186 passed, 0 failed**, 20 skipped (+2: `tests/native-geography-parity.test.ts`) |
| `npx tsx scripts/export-contracts.ts`, `scripts/validate-recommendation-examples.ts` | exit 0; validation passed |
| `scripts/import-recommendation-upstream.py --check` | fails as expected (`Upstream port fingerprint changed`); P4 adds `repositories/geographic_repository.py`, `tests/test_terminal_repositories.py` and the `recommendation/README.md` paragraph to the files to register in P7 (INVENTORY section M) |
| live state `data/.runtime/recommendation/state.sqlite` | md5 `247b2503…` unchanged; services on 8010/8091/8092 untouched |

New failures introduced by P4 part 1: none, apart from the expected upstream fingerprint check.

### P4 part 2: tools, bind_entity, budgets, scripted/live controller

Scope (spec section 6 tool list, R12 bind invalidation, section 13 P4 items that need the
model-facing tools). New modules: `recommendation/tools/arguments.py` (typed per-tool argument
models, Contract base: extra=forbid, allow_inf_nan=False, every model carries `context_ref`),
`recommendation/tools/registry.py` (names, kinds, stage dependencies, descriptions, per-tool JSON
schemas), `recommendation/tools/geo_tools.py` (search_places, describe_fields, nearest_mrt,
distance_to, nearby_amenities, district_membership, bind_entity validation),
`recommendation/geography/search.py` (exact normalised search with the resolver's rules),
`recommendation/repositories/platform_transit.py` (platform NEAR_MRT text by ID, READ ONLY),
`recommendation/agent/prompt.py` (system prompt and repair text). Changed:
`tools/dispatcher.py` (pipeline `resolve_entities` -> `build_pool` -> `get_geographic_evidence`
-> `rank_candidates`; out-of-order -> `STALE_REFERENCE` tool error; a stage discards every later
stage; `validate_hard_constraints` folded into `rank_candidates`; bind applies the bound
Resolution and discards build_pool/geographic evidence/ranking; accepted bindings are
re-applied by `resolve_entities`, also after a data-change invalidation; budgets),
`agent/controller.py` (ScriptedController = the fixed pipeline, never search/bind, proposes
`dispatcher.selected`; LiveController sends `registry.model_tools()` and the new prompt; repair
message carries `recorded_selection` and the pipeline order), `models/tool_results.py`
(`selected_ids`, `completed_stages`, `data`), `geography/evidence.py` (public helpers
`entity_facts` / `nearest_stations` reusing the criterion-evidence distance code), settings
(`entity_resolution.max_search_attempts` and `controller.max_resolution_tool_calls` now in
effect: pending entries and YAML markers removed), README / native doc tool tables.

#### State before P4 part 2 (pre-existing failures)

Re-run on the tree as left by part 1, before any part 2 edit: pytest generic gate **299 passed**.
Other gates are taken from the part 1 record (`schemas --check` 0, typechecks 0, `npm test` 206 /
186 pass / 0 fail / 20 skip; upstream `--check` fails as expected). No pre-existing failure
besides the upstream fingerprint check.

#### P4 validation items covered by part 2

Python tests: `recommendation/tests/test_geo_tools.py` (36 tests; fixture mode, real static
LTA/URA catalog; live-controller tests use scripted fake models, never an LLM). Live smoke:
`PYTHONPATH=. .venv/bin/python evaluation/recommendation-exploration-2026-10-23/p4/live_tools.py`
-> `p4/live-tools.json` (database mode, PostgreSQL READ ONLY, Neo4j READ sessions,
ScriptedController only, temporary state file, pool cache disabled / no store for the direct
tool calls; live state md5 `247b2503ac952c17f8676443f5f8355a` before and after; services on
8010/8091/8092 untouched).

| Item (spec section 13, P4 / task list) | Proof | Outcome |
|---|---|---|
| Typed arguments (extra=forbid) and per-tool JSON schemas | `test_registry_tools_and_per_tool_schemas` (11 tools; every schema `additionalProperties: false` and requires `context_ref`; exact property/required sets for search_places, nearby_amenities, bind_entity, distance_to, describe_fields); `test_typed_arguments_reject_extra_and_invalid_values` (10 cases: extra key, empty query, unknown kind/category/mode, NaN/negative radius, duplicate listing IDs, missing entity_id, arguments on a pipeline tool -> `INVALID_ARGUMENT` naming the argument, before any tool and before the resolution budget); `test_context_ref_scopes_every_tool_to_the_current_run` | pass |
| Stage order errors (out-of-order -> STALE_REFERENCE) and invalidation | `test_stage_order_errors_and_invalidation`: build_pool / get_geographic_evidence / rank_candidates before their predecessor, listing query tools and bind before their stage -> STALE_REFERENCE; query tools leave the snapshot unchanged; re-running build_pool / resolve_entities discards the later stages. Live: `rank_after_bind_stale` -> STALE_REFERENCE | pass |
| ScriptedController fixed order, no bind | `test_scripted_controller_runs_the_fixed_pipeline_and_never_searches_or_binds` (trace tool order == PIPELINE, 4 tool calls). Live: all 4 end-to-end scenarios `tool_order_is_pipeline: true` | pass |
| search_places (exact, filters, no substring, absent place not invented) | `test_search_places_exact_filters_limit_and_no_substring`: 'Clementi' -> area + MRT station (2), `kind=ura_area` -> 1, 金文泰地铁站 -> `LTA:MRT:CLEMENTI`, 'Clement' -> 0, limit/truncation, category filter, limit > max_candidates rejected, non-geo criterion_id rejected, 'NUS ISS' -> 0 candidates. Live: 'Cold Storage' total 38 | pass |
| bind_entity: single (kind-filtered) candidate only; changes pool_key; invalidates pool/evidence/rank (R12) | *(P4 review round: superseded, see below)* `test_bind_accepts_only_the_single_candidate_and_invalidates_the_pool`: 2-candidate search -> `BIND_REJECTED` ("never the first of several"), nothing invalidated; `kind=ura_area` single -> accepted, stages back to `[resolve_entities]`, rank -> STALE_REFERENCE, rebuilt pool has a different pool_key (size 0 -> 11), second bind rejected, binding survives resolve_entities and `invalidate_pool` + resolve_entities. `test_bind_entity_for_an_ambiguous_criterion_end_to_end_with_a_scripted_live_model`: ambiguous 'Bukit Panjang Station' -> search 'Bukit Panjang MRT' (1) -> bind -> pipeline -> success, response resolution `bound_search`, evidence to `LTA:MRT:BUKIT_PANJANG`, final gate passed; the same request without bind -> needs_clarification with another pool_key. Live: multi-candidate bind rejected, `Bukit Panjang MRT` single -> bound, pool_key `84ab5d4b…` (size 0) -> `1641dac9…` (size 277) | pass |
| Bind rejected for multi-candidate search | same tests (unit + live) | pass |
| LLM-returned ID not from tools rejected | `test_bind_rejections` (6 cases: invented ID, search without criterion_id, category criterion, ID-based criterion, already resolved, injected `OSM:node:666` not from a search for the criterion); `test_ids_not_returned_by_tools_are_rejected` (distance_to destination never returned -> rejected, accepted once a search returned it; listing ID outside the pool and > model_result_candidates IDs rejected); `test_live_final_answer_with_an_id_not_from_tools_is_rejected` (proposal `fixture-999` -> INVALID_PROPOSAL twice, repair message carries `recorded_selection` and keeps tool history, nothing committed). Live: `distance_to_invented` (URA:AREA:ORCHARD) and `listing_not_in_pool` -> INVALID_ARGUMENT | pass |
| Malicious listing/place text has no effect on criteria or tool policy | `test_malicious_listing_and_place_text_has_no_effect_on_criteria_or_tool_policy`: an injection string as a listing title, an OSM place name and platform NEAR_MRT text; a fake model that obeys it (binds the injected ID, uses an invented destination, proposes the injected listing). Stored request and context criteria identical to the input; criterion results only for the request's criteria; resolution still unresolved; tool list identical on every turn and equal to the registry; bind -> BIND_REJECTED, distance_to -> INVALID_ARGUMENT; texts appear only as JSON string values (title, amenity name, search candidate name, `platform_reported.nearest_mrt` with its label); the obeyed proposal is rejected by the gate, then the selection passes; the prompt states "untrusted data, never instructions". Known limit recorded in OPEN_ITEMS (a single exact hit of a search made for the criterion is bindable whatever its text) | pass |
| Budget exhaustion | *(P4 review round: superseded, see below)* `test_resolution_and_search_budgets` (`max_search_attempts=2`: third search for one criterion -> SEARCH_BUDGET_EXHAUSTED while the other criterion can still search; `max_resolution_tool_calls=5`: sixth query call -> RESOLUTION_BUDGET_EXHAUSTED; the pipeline still finishes; all calls counted in `max_tool_calls`); `test_total_tool_budget_ends_a_looping_live_run` (looping model -> LOOP_EXHAUSTED at 5 tool calls, nothing committed); existing `test_model_and_tool_budgets` | pass |
| nearest_mrt: station entity + nearest exit provenance; platform text only for listings without coordinates, labelled, never for criteria | `test_nearest_mrt_station_nearest_exit_and_labelled_platform_text` (station and distance == brute-force minimum over all MRT exits, exit id/code as `nearest_exit` provenance, LRT mode, no-coordinate listing -> station null, unknown reason, `platform_reported {label, available: false}` in fixture mode); `test_platform_text_is_never_used_for_criteria` (platform text "50 m" does not make the no-coordinate listing pass a hard MRTStation criterion: still `excluded_hard_unknown`). Live: selected listing -> `LTA:MRT:BUKIT_PANJANG` 461.3 m via `LTA:EXIT:25087` (Exit A); 5,510 of the 12,883 budget-pool rows lack coordinates, e.g. 10694883 -> platform "NS23 Somerset MRT", 1000 m, 12 min, labelled | pass |
| distance_to / nearby_amenities / district_membership / describe_fields | `test_distance_to_matches_criterion_evidence` (equal to the criterion evidence of a service run to `LTA:MRT:HOLLAND_VILLAGE`; no coordinates -> null, never 0); `test_nearby_amenities_radius_limit_order_and_unknown`; `test_district_membership_reports_independent_partitions` (district_code kept as in the row even when it disagrees with the polygon; missing district_code not inferred; planning area by point-in-polygon only from coordinates); `test_describe_fields`. Live: district D23 / `URA:AREA:CHOA_CHU_KANG`; 7 supermarkets within 800 m (3 listed, nearest Sheng Siong 210.9 m); distance_to the bound station 461.3 m | pass |
| Live controller prompt (criteria immutable, untrusted data, hypotheses, tool IDs only, no first pick, limited retries with configured budgets, code selection, final JSON) and repair path | `test_live_prompt_states_the_policy_with_configured_budgets` (phrases and budgets from an override config; the model receives exactly `model_tools()` and the prompt; data-change repair text names resolve_entities -> build_pool); existing `test_invalid_final_ids_are_repaired_with_existing_tool_history`, `test_live_dispatch_recovery_content_blocks_and_trace` | pass |
| Terminal resolution without TS | `test_terminal_resolves_natively_without_typescript`: existing terminal commands (`requirements @file`, `recommend`), fixture + scripted, `subprocess.run` patched to fail: 金文泰 -> `URA:AREA:CLEMENTI`, 'Clementi MRT' -> `LTA:MRT:CLEMENTI`, the four pipeline tools printed. Full terminal debug commands (`show-resolution`, transcript) are P6 | pass (existing commands); P6 commands not_done (out of scope) |
| Nearest MRT uses station + nearest exit (as a tool; part 1 had the distance semantics only) | see nearest_mrt row | pass |
| Live smoke of the deterministic resolver and tools (read-only, scripted) | `p4/live_tools.py`: 'Clementi MRT' ≤ 1,500 m -> success, pool 210; 'NUS ISS' -> needs_clarification (unresolved); 'Bukit Panjang Station' -> needs_clarification (ambiguous, MRT/LRT candidates); soft 金文泰 -> success (`URA:AREA:CLEMENTI`), pool 12,883. Tool timings on the 277-row bound pool: query tools 6–7 ms, build_pool ~1.0 s (full retrieve, cache disabled) | pass |

#### Test assertions changed on purpose (P4 part 2)

- `test_policy_controller.py::RecoveringModel`: the four tool names of the second turn are the new
  pipeline names (`resolve_entities`, `build_pool`, `get_geographic_evidence`, `rank_candidates`);
  tool-call count (5) and all other assertions unchanged.
- `test_policy_controller.py::test_model_preview_limit_preserves_full_pool`: "the first tool step"
  became "the build_pool step" (the first step is now resolve_entities, which has no pool yet);
  the truncation assertions are unchanged.
- `test_recommendation.py::test_full_flow_and_restart` and `::test_reference_resolution_and_gate_replay`:
  `trace["steps"][0]` (was retrieve_listings) became the build_pool step; same rows/snapshot_ref
  assertions.
- `test_fit_controller_regression.py::RankedModel` (outside the manifest): calls the pipeline tools
  of the tool list instead of every listed tool (the list now also has query/bind tools with other
  required arguments); tool-call counts (4) unchanged.
- `test_pool_cache.py`: one docstring wording (no assertion change).
- Contract examples: regenerated into the tree and compared ignoring volatile IDs/durations: only
  `previous_state_id` differs (volatile), so the committed files were restored unchanged.
  `scripts/validate-recommendation-examples.ts`: passed (it rewrites the timestamp in
  `evaluation/reports/recommendation-handoff-document.json`).

#### Rules decided in P4 part 2 (not literal in the spec)

See OPEN_ITEMS.md "Decisions and known effects of P4 part 2": optional `criterion_id` on
search_places (per-criterion budget; bind requires it), structural bind rule, validation folded
into rank_candidates, budget semantics (query/bind calls only; exhaustion is a tool error),
listing tools need the pool, argument ceilings as code constants (50 km radius, 20 amenities,
200 characters), platform text only for listings without coordinates (unavailable in fixture
mode), `resolve_entities` re-applies accepted bindings while the entity still exists.

#### Generic gates after P4 part 2

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **335 passed** (299 before + 36 new in `test_geo_tools.py`) |
| `python -m recommendation.schemas --check` | exit 0 (no contract change) |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 206 tests: **186 passed, 0 failed**, 20 skipped (unchanged) |
| `npx tsx scripts/validate-recommendation-examples.ts` | passed |
| `scripts/import-recommendation-upstream.py --check` | fails as expected (`Upstream port fingerprint changed: recommendation/agent/controller.py`); part 2 manifest edits listed in INVENTORY section M for P7 |
| live state `data/.runtime/recommendation/state.sqlite` | md5 `247b2503ac952c17f8676443f5f8355a` before and after the live smoke; services untouched |

New failures introduced by P4 part 2: none, apart from the expected upstream fingerprint check.

### P4 review round (fixes)

State before the fixes: pytest generic gate **335 passed** (re-run), other gates as recorded for
part 2. Every finding was reproduced first (reviewer probes in the scratchpad `review-p4/`:
bind 'Orchard' for 'NUS ISS' accepted; two stage-rejected `nearest_mrt` calls exhausted
`max_resolution_tool_calls=2`; pool_version `dcbe2ac0…` (name) vs `e553ac37…` (ID) for one
pool_key; `get_geographic_evidence` tool message 6,270 bytes with 11 `nearest_exits` per soft
station criterion on the 12-row fixture pool).

| Finding | Outcome | Change and proof |
|---|---|---|
| (major) Invalid ID -> name search / ID-name conflict unreachable from a request; row reported `pass` | fixed (plan Step 5 implemented end to end; contract change pending user confirmation in OPEN_ITEMS) | Part 1 row re-labelled "partial at part 1 (resolver API only)". `Criterion` accepts a category alone, or destination_id and/or destination_name (generated schemas and zod mirror unchanged: they never encoded "exactly one"); TS adapter sends `place_id` + `name`. Resolver checks ID/name consistency against ALL exact matches (`Resolution.matches`), not the truncated candidates. `test_id_with_name_end_to_end_invalid_id_fallback_and_conflict` (service, contract 2.0 requests): `URA:AREA:ATLANTIS` + 'Holland Village MRT' -> success, `id_then_name`, `id_not_found; unique_exact_station_match`; `GOOGLE:place:1` + 武吉知马 -> `unsupported_namespace; unique_exact_area_match`; agreeing ID + name -> `id+name`, same pool reused; `URA:AREA:BUKIT_TIMAH` + 'Bedok' -> needs_clarification, `id_name_conflict`, clarification names both; category + name rejected. TS: `nativeRequest` maps `{place_id:'OSM:way:54519165', name:'NUS'}` to both fields (assertion changed, below). Live read-only resolver probe: `OSM:way:54519165` + 'NUS' -> `id+name` resolved; `URA:AREA:ATLANTIS` + 'Clementi MRT' -> `LTA:MRT:CLEMENTI`; `URA:AREA:CLEMENTI` + 'Bedok' and NUS ID + 'Cold Storage' (38 matches) -> `id_name_conflict` |
| (minor) Explicit-station rule extended beyond TS | fixed (reverted to TS; extension listed as a pending user decision) | `normalize.explicit_station` / `requested_mode` are the TS regexes verbatim (no NFKC). New shared fixture `tests/fixtures/place-mention-rules.json` (78 cases, generated from TS) run by `tests/native-geography-parity.test.ts` (TS helpers `isExplicitStation`, `stationMode` exported and used in place of the inline regexes, behaviour unchanged) and `test_mention_rules_match_shared_ts_fixture`: 78/78 equal. 'Bukit Panjang Station' / 'Clementi Station' / 武吉班让站 -> URA area (TS), `… MRT` / `… LRT` -> the station twin. Live: same |
| (minor) OSM place without coordinates by ID returned `resolved` | fixed | `resolver.usable`: unresolved `destination_without_coordinates (<id>)` by ID, name or both (`test_destination_without_coordinates_is_unresolved`); `bound_resolution` gives the same, `bind_entity` rejects such an entity. Service: hard `destination_id` to a coordinate-less place -> needs_clarification with a clarification (`test_service_destination_without_coordinates_needs_clarification`; was no_match). Live: 0 of 3,716 active places lack coordinates (latent) |
| (minor) Missing-coordinate unknown reason still mentions NEAR_PLACE | fixed | `MISSING_COORDINATES` = "listing has no valid coordinates (unknown; never 0 m)" (= the tool text). The baseline parity test needed no change (the old text occurs only in non-returned evidence and `unverified_candidates` of the 4d36507 baseline, which the test does not compare); one assertion in `test_pool_cache.py` changed (below). Examples regenerated (no example carried the text) |
| (minor) Stage-rejected query calls consumed resolution budget | fixed | Dispatcher: stage check first; resolution budget charged only after the tool ran (not for INVALID_ARGUMENT incl. the tool's own checks, STALE_REFERENCE, UNSUPPORTED_OPERATION, budget errors; BIND_REJECTED is charged). `test_stage_order_errors_and_invalidation` asserts 0 after stage rejections; `test_rejected_query_calls_use_no_resolution_budget` (three stale + six tool-level INVALID_ARGUMENT calls free; then search + rejected bind = 2; next call exhausted) |
| (minor) Generic MRT name ('MRT') unresolved `empty_name` | fixed | Port of TS `isGenericMrt` (same shared fixture): name-only generic mention -> category `MRTStation`, method `name->category`; with a verified ID -> `verified_id; generic MRT name` (no conflict); invalid ID + generic name -> category via `id_then_name` (`test_generic_mrt_name_is_the_mrt_station_category`). Live: 'MRT' -> nearest of 147 LTA MRT stations |
| (major) bind_entity could substitute another place for an absent/ambiguous target | fixed | `GeoTools.bind_problem`: criterion ambiguous by name (unresolved never binds; `id_name_conflict` left to the user), search for that criterion whose query normalises to the criterion's own name (after the NUS/NTU map), single filtered candidate, member of ALL the name's exact matches, valid coordinates. `search_places.bindable` and the resolution view's `bindable` use the same rule; prompt rule 4 and the registry description updated. `test_bind_never_substitutes_another_place_for_the_criterion_name`: 'NUS ISS' + search 'NUS' (1 hit, `OSM:way:54519165`) -> not bindable, BIND_REJECTED; ambiguous 'Holland Hub' + 'Orchard' (`kind=ura_area`, 1 hit) -> rejected; ID/name conflict + 'Bedok' -> rejected; coordinate-less single candidate -> rejected; 'holland hub' + `category=School` -> accepted. The accepted-bind tests now use the ambiguous 'Holland Hub' (Mall/School twins) instead of the unresolved 'Clementi Town' -> 'Clementi' substitution and 'Bukit Panjang Station' (no longer ambiguous under the TS rule) |
| (major) get_geographic_evidence sent every pool listing's nearest exit to the model | fixed | `data.records` are per-criterion summaries (criterion_id, resolution status/entity/kind/method/reason, method, listings, known, unknown); `nearest_exits` stays in the trace snapshot only (`test_geographic_evidence_result_is_compact_for_the_model`: no `nearest_exits` in the ToolResult JSON, 11 in the snapshot). Trace-side slimming remains P6 (OPEN_ITEMS interim item) |
| (minor) Duplicate of the budget finding (charged before the tool's own validation) | fixed | same change and tests as the stage-order budget finding; the reviewer's probe now lets the later `search_places` run |
| (minor) pool_version depended on the resolution method/reason | fixed | Pool content keeps `{status, entity_id, entity_kind}` per hard-geo key; `POOL_FORMAT_VERSION` -> `pool-v4`. `test_pool_version_does_not_depend_on_how_the_entity_was_resolved`: 'Clementi MRT', `LTA:MRT:CLEMENTI`, 金文泰地铁站 and ID + name, each built uncached in its own store -> one pool_key and one pool_version (methods name / id / name / id+name) |

#### Test assertions changed on purpose (P4 review round)

- `test_geography.py::test_multiple_candidates_are_ambiguous_never_first_pick`: the ambiguity
  example is the duplicate OSM name 'Cold Storage' (both candidates, `max_candidates=1` keeps
  one, `matches` keeps both); 'Bukit Panjang Station' / 'Clementi Station' now assert the URA
  area (TS rule), `… MRT` / `… LRT` the station.
- `test_geography.py::test_no_match_and_absent_place_are_unresolved_not_invented`: the
  `empty_name` example is 'Station' ('MRT' is now the MRTStation category, new test).
- `test_geography.py::test_service_unresolved_hard_target…` and
  `::test_geography_and_resolution_settings_are_in_effect`: the ambiguous service request is the
  ID/name conflict `URA:AREA:CLEMENTI` + 'Bedok' (still needs_clarification with candidates;
  `max_candidates=1` still bounds them).
- `test_geo_tools.py::test_bind_accepts_only_the_single_candidate_and_invalidates_the_pool` and
  `::test_bind_entity_for_an_ambiguous_criterion_end_to_end_with_a_scripted_live_model`: bind an
  ambiguous 'Holland Hub' via a category-filtered search for the same name (same invalidation,
  pool_key, final-gate and no-bind assertions).
- `test_geo_tools.py::test_resolution_and_search_budgets`: `resolution_calls` is 3 (the
  SEARCH_BUDGET_EXHAUSTED rejection is free), one more search reaches the limit of 5, total
  calls 11 (was 4 / 10).
- `test_geo_tools.py::test_live_prompt_states_the_policy_with_configured_budgets`: phrase
  "Never choose the first" -> "never choose the first", plus the new own-name phrases.
- `test_pool_cache.py::test_outputs_belong_to_the_pool_version_and_partition_invariant`: the
  unknown-reason key text.
- `tests/native-recommendation.test.ts` (round-trip test): `destination_name` is 'NUS' (was
  undefined) for a target with both place_id and name.
- Regenerated `recommendation/examples/contracts/*.json`: field-path diff shows only
  `pool.{pool_key,pool_version}`, `selection.cold_start.pool_key` (pool-v4) and volatile
  IDs/durations/state refs; `scripts/validate-recommendation-examples.ts` passed.

#### Generic gates after the P4 review round

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **344 passed** (335 + 9 new: 5 in `test_geography.py`, 3 in `test_geo_tools.py`, 1 in `test_pool_cache.py`) |
| `python -m recommendation.schemas --check` | exit 0 (generated schemas unchanged) |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 207 tests: **187 passed, 0 failed**, 20 skipped (+1: shared mention-rules fixture) |
| `npx tsx scripts/validate-recommendation-examples.ts` | passed |
| `scripts/import-recommendation-upstream.py --check` | fails as expected (`Upstream port fingerprint changed`); `recommendation/contracts/requirements.py` added to INVENTORY section M |
| live state `data/.runtime/recommendation/state.sqlite` | md5 `247b2503ac952c17f8676443f5f8355a` before and after (live probe read-only); services on 8010/8091/8092 untouched |

New failures introduced by the P4 review round: none, apart from the expected upstream fingerprint check.

## P5

### P5 part 1: full-pool fused scoring (fused-v2)

Scope (spec section 7, R1d, R10, R11; section 13 P5 items about scoring): new module
`recommendation/ranking/scoring_v2.py` (numpy component arrays: pool x soft utilities with a
known mask; features x pool x references similarities with a compared mask and a self-pair
mask; `PoolScores.combine(weights)` derives soft / appointment / browsing / behaviour /
combined scores and coverages from the components, so stability trials can reweight without
rescoring; order O = sort(-combined, listing_id) by `np.lexsort`; ScoreBreakdown/Contribution
built only on demand by `breakdown()`; `summary()` for the trace). `recommendation/ranking/ranker.py`
rewritten as the scalar reference (`soft_utility`, `feature_similarity`, `pair_similarity`),
used by the scorer for soft utilities and by the tests as an independent oracle.
`tools/dispatcher.py` rank_candidates scores the whole eligible pool with `score_pool`, keeps
the components on `dispatcher.scored`, builds breakdowns only for the selection + the first
`trace.preview_rows` of O, and adds a `scoring` record (effective weights, active / suppressed /
dropped features, fitted spreads, scales, reference accounting, duration) to the tool data.
`context/manager.py` drops the weighted-v1 path (profile records the v2 browsing weights);
`service.py` takes `scoring_version` (`fused-v2:<selection_hash[:12]>`) and the policy from
scoring_v2; `assembly.py` drops the transitional appointment warning. `ranking/legacy.py`
(L1-L6, plus the P2 "L7" mapping) is deleted; the `ranking.*` YAML markers and their
`settings/pending.py` entries are removed (cold_start / selection / trace keys stay marked).
Selection is still the prefix of O (P5 part 2). The scoring_version keeps the
`:<selection_hash[:12]>` suffix of P1 so that a settings change still changes the identifier
(`test_config_change_never_alters_replay_of_old_request`).

#### State before P5 (pre-existing failures)

pytest generic gate re-run on the pre-P5 tree at the start of this phase: **344 passed**. The
other gates are taken from the P4 review-round record above (not re-run before editing):
`schemas --check` exit 0; both typechecks exit 0; `npm test` 207 tests, 187 passed, 0 failed,
20 skipped; `scripts/import-recommendation-upstream.py --check` fails as expected
(`Upstream port fingerprint changed`, registration is P7). No other pre-existing failure.

#### P5 validation items covered by part 1

Tests are in `recommendation/tests/test_scoring_v2.py` (19 tests) unless noted.

| Item (spec section 13, P5) | Proof | Outcome |
|---|---|---|
| Whole pool scored | `test_whole_pool_is_scored_and_ordered_by_combined_then_id`: service round with browsing + appointment feedback → rank_candidates `scoring.pool_scored` == `response.pool.size` == len(snapshot ranking) == 9; snapshot `scores` hold only the preview/selection rows (R10); O == sorted by (-combined, id) and score_rank == position; all combined finite. Synthetic timing test: `len(ranked) == 21431`. Live (below): 21,431 rows scored, all finite | pass |
| Earlier-round browsing has no effect | `test_earlier_round_browsing_has_no_effect`: two conversations, identical round 1 and identical round-3 snapshot (refers back to round 1), different round-2 browsing (1/5 s vs 1/900 s): round-2 scores differ, round-3 items and full ScoreBreakdowns are identical. P2 `test_three_rounds_no_loss_no_accumulation_appointments_only_via_input` still passes (round 3 scores == round 1) | pass |
| > 120 s still increases weight | `test_browsing_beyond_120_seconds_still_increases_weight`: `browsing_weight` strictly increasing over 1, 60, 120, 121, 600, 3600, 86400 s (cap null); w(90) == log1p(90/time_scale_seconds); with two references the candidate's browsing score rises 60 < 120 < 600 < 3600 s; a configured `duration_cap_seconds: 120` makes 600 s == 120 s (cap only when set). `test_recommendation.py::test_metrics`: w(999) > w(120) | pass |
| Appointment status upgrade adds nothing | `test_appointment_status_upgrade_adds_nothing`: scorer and service (four conversations) with one entry for the same listing and statuses [MANUAL_SENT], [CONFIRMED, MANUAL_SENT], [MANUAL_REPLIED, MANUAL_SENT], all three → identical ScoreBreakdowns; appointment branch present. P2 fixture `appointment-dedup-one-entry-per-listing` passes | pass |
| Missing handling | `test_missing_attributes_are_not_compared_and_weights_renormalise`: fixture-10 (no bedrooms) → pair coverage 1 − w_bedrooms/active, bedrooms contribution unknown / weight 0 / method `missing`, appointment contribution weights sum to 1; fixture-12 (no coordinates) → `district_fallback` (both D21 → 1.0); with fallback off and `min_comparison_coverage` 0.9 the pair is unavailable (behaviour None, coverage None, contributions `pair_unavailable`, weight 0) while other listings keep a score; missing and 0-second browsing records are not references and are counted (`missing`, `zero_seconds`). `test_formula_missing_branches_contribute_zero_without_redistribution`: combined == soft_weight·soft + behavior_weight·(appointment_weight·(appointment or 0)); no soft criteria → soft None and coverage 0 | pass |
| Zero variance | `test_zero_variance_feature_is_dropped_without_division_by_zero`: constant bedrooms → `dropped_features {bedrooms: zero_variance}`, no bedrooms contribution, all scores finite; mostly-equal values: IQR 0 (dropped) but std > 0 (kept); no known value (NaN spread) → dropped. `test_fit_spread_pins_ddof_and_linear_quantiles` pins std ddof=0 and numpy 'linear' percentiles (R11) | pass |
| Unit change invariance | `test_unit_change_invariance[std|iqr|p05_p95]`: price and floor_area_sqft × 2 → spread × 2 and the similarity tensor and combined scores bit-identical; × 10.7639 and ÷ 10.7639 (sqm↔sqft) → equal within 1e-12 | pass |
| Single-reference normalisation documented + tested | Documented in the `scoring_v2.py` module docstring, in `context.scoring_policy.browsing.single_reference` of every trace, and OPEN_ITEMS. `test_single_reference_normalisation_cancels_absolute_duration`: one reference at 5 s vs 5,000 s → browsing branch arrays exactly equal and equal to that pair's scalar similarity; with two references the relative duration changes the score | pass |
| Weights / scales visible in trace | `test_weights_and_scales_are_visible_in_the_trace`: `context.scoring_policy` = configured weights, feature weights, scaling, quantile method, ddof, geography scale, fallback, coverage gate, browsing transform/scale/cap; rank_candidates `scoring` = effective weights (soft criteria, fusion, branches, active feature weights), fitted spreads, geographic scale, time scale, cap (null), per-reference browsing weights; Contribution.scale equals the fitted spread; `trace.configuration` = the effective settings | pass |
| Self pairs excluded (R1d) | `test_self_pairs_are_excluded`: a listing that is its own only reference → behaviour None (all others scored); with two references only the other one counts (score == scalar pair similarity, contributions never reference itself); `self_pairs_excluded` counted. `test_metrics`: `pair_similarity(a, a)` → `(None, None, [])` | pass |
| Out-of-pool references ignored and counted (decision 3, reference_scope in_pool) | `test_out_of_pool_references_are_ignored_and_counted`: browsing and appointment references outside the pool leave the combined array bit-identical to a run without them and appear in `references.*.out_of_pool` / `out_of_pool_count`; service: an appointment for fixture-11 (fails the hard budget) → no behaviour score, trace `out_of_pool: [fixture-11]`, `used_count 0` | pass |
| Suppression only by hard criteria (decision 1) | `test_suppression_follows_hard_criteria_only`: soft price keeps the price feature, hard price suppresses it, `all_criteria` (configured alternative) suppresses for a soft one | pass |
| numpy == scalar reference | `test_numpy_arrays_match_the_scalar_reference`: every (candidate, reference) pair of the 12-row fixture pool (missing bedrooms / coordinates included) under three configurations (default iqr + fallback; std, no fallback, coverage 0.9; p05_p95, suppression none): availability equal, similarity and coverage within 1e-12; appointment and browsing branch scores equal the scalar weighted means; contributions sum to the branch scores | pass |
| Components kept for reweighting (extension point for P5 part 2 stability) | `test_breakdowns_are_built_only_on_demand`: `combine(reweighted())` reproduces the base combined array exactly; changed layer weights change it | pass |
| Timing test (R10, scoring part) | `test_timing_synthetic_21431_listing_pool_with_5_references` (synthetic 21,431 listings, ~5 % missing per attribute, 3 browsing + 2 appointment references, 2 soft + 1 hard criteria): score_pool + 10 breakdowns **0.054–0.076 s** over the runs in this phase (assert < 3 s); evaluate() over the pool 0.107 s. Live, read-only (scratchpad `p5/live_scoring.py`: database `retrieve` 0.61 s, no service, no state write): 21,431 real RENT rows, 5 references, 2 soft criteria → scoring median **0.077 s** over 5 runs (0.054–0.092 s), evaluate 0.10 s, spreads (iqr) price 4,300 / bedrooms 3 / floor_area_sqft 958, nothing dropped, behaviour available for all rows, all finite. Diagnostics and selection timing are part 2 | pass (scoring); diagnostics + selection: not_done (part 2) |
| No soft requirements & all tied → exploration; unresolved data not under-specification; C/B/S threshold boundaries; scoring before sampling; quota totals; after exit no price exploration but near-tie sampling; diagnostics complete; plan scenarios; no duplicates / out-of-pool; replay identical; new request can resample; config change needs new request; ≥200-seed statistical check; not permanently the same IDs | cold start, selection, gates/replay are P5 part 2 | not_done (part 2) |

#### Test assertions changed on purpose (P5 part 1)

- `test_settings.py`: `test_weighted_v1_score_parity_all_pairs_and_suppression_sets` and
  `test_weighted_v1_scores_identical_to_baseline_fixtures` (with their helpers `_same`,
  `legacy_dump`, `_LegacyView`, `v2_request`, `_remap_evidence_ids`) removed: fused-v2 changes
  every score by design. `baseline/score-parity.json`, `baseline/capture_scores.py` and the
  baseline responses stay as historical evidence (not asserted; the capture script still runs
  against a 4d36507 archive via `PARITY_ROOT`).
- `test_settings.py::test_unreachable_parameters_may_be_null`: `legacy.similarity_policy(config)
  is None` → `not scoring_v2.behaviour_enabled(config)`; the "no implicit signal" check uses the
  fused-v2 scorer (behaviour None with behavior_weight 0, present with the committed YAML).
- `test_settings.py::test_no_duplicate_hard_coded_parameters_outside_settings`: the
  `ranking/legacy.py` exemption is gone; asserts the module no longer exists.
- `test_settings.py::test_not_in_effect_markers_match_pending_table`: the keys that legacy
  constants overrode (soft_weight, browsing cap / time scale, suppression, numeric_scaling,
  feature_weights.price, appointment_weight) must now NOT be covered by a pending entry, and no
  `ranking*` entry remains (was: each had to be covered).
- `test_settings.py::test_trace_records_settings_not_in_effect`: `policy["suppression"] ==
  "all_criteria"` → `policy["similarity"]["suppression"] == "hard_criteria"` (configured value in
  effect). `legacy.scoring_identifier` → `scoring_v2.scoring_identifier` in
  `test_app_loads_yaml_by_default_and_env_alternate` and
  `test_config_change_never_alters_replay_of_old_request` (assertions unchanged).
- `test_recommendation.py::test_full_flow_and_restart`: `combined_score == soft_score` →
  `== soft_weight × soft_score` (missing behaviour branch contributes 0, no redistribution);
  `behavior_weight == 0.2` → `== ranking.behavior_weight` (0.40).
- `test_recommendation.py::test_metrics`: `pairwise`/`score` → `pair_similarity` / `pool_scores`.
  Self similarity 1 → self pair excluded (R1d); symmetry kept; missing-coordinate coverage 0.7
  (weighted-v1 geography weight) → 1 with district fallback, 1 − w_geo/active without it;
  invalid-scale ValueError kept (zero spread; negative weights are rejected by the loader,
  `test_invalid_policy`); "no signal → combined 0, stable_listing_id" and "0 s → no behaviour"
  kept; the 120 s cap equality → w(999 s) > w(120 s) with the cap null.
- `test_policy_controller.py`: `scoring_identifier(default) == "weighted-v1:…"` → `"fused-v2:…"`;
  the bedroom-suppression check and `test_configured_scales_change_similarity_without_pool_normalization`
  call the fused-v2 scorer (same intent: a HARD bedroom criterion suppresses the only enabled
  feature; a larger configured geography scale raises behaviour similarity and appears as the
  contribution scale). Parametrize comment updated (numeric spreads are fitted on the pool).
- `test_contract_v2_fixtures.py::test_contract_cases_at_service_level` (cases `zero-seconds`,
  `all-missing`): "behaviour None when no positive browsing" → browsing None for every item and
  behaviour None exactly when no OTHER in-pool appointment exists (the cases carry an
  appointment for fixture-07, which now contributes; its own item has none, R1d).
- `tests/fixtures/recommendation-v2/flow.json`: round-2 `expect.shown_listing_ids` is the
  fused-v2 order (05, 09, 03, 01, 07, 06, 08, 04, 02; was 05, 07, 03, 09, 01, 06, 04, 08, 02) and
  round 3's previous-round snapshot (sidecar + native_body, shown ids and browsing records)
  follows that display order; the set, statuses and durations are unchanged (TS flow test passes).
- `tests/fixtures/recommendation-v2/ten-results.json`: regenerated with the scratchpad generator
  `gen_ten_results.py` (soft-only request: combined = 0.6 × soft, same 10 IDs; the fixture
  asserts a fresh run reproduces it).
- `tests/native-geography-live.test.ts:25` (env-gated, not run here): oracle `combined ==
  1/(1+d/1000)` → `soft_weight × 1/(1+d/1000)` (a block comment: a first edit with an inline `//`
  comment broke the one-line loop, was caught by `npm test` as a TransformError and fixed).
- Regenerated `recommendation/examples/contracts/*.json`: field-path diff against the pre-P5
  files shows only `scores.{combined_score, soft_weight, behavior_weight, appointment_weight,
  browsing_weight, ordering, suppressed_features, contributions.method}`,
  `execution.scoring_version` and volatile IDs/durations/state refs;
  `npx tsx scripts/validate-recommendation-examples.ts` passed.

#### Generic gates after P5 part 1

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **361 passed** (344 − 2 removed weighted-v1 parity tests + 19 new in `test_scoring_v2.py`) |
| `python -m recommendation.schemas --check` | exit 0 (generated schemas unchanged) |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 207 tests: **187 passed, 0 failed**, 20 skipped |
| `npx tsx scripts/validate-recommendation-examples.ts` | passed |
| `scripts/import-recommendation-upstream.py --check` | fails as expected (`Upstream port fingerprint changed`); P5 manifest files listed in INVENTORY section M |
| live state `data/.runtime/recommendation/state.sqlite` | md5 `247b2503ac952c17f8676443f5f8355a` (same as after P4); services on 8010/8091/8092 untouched |

New failures introduced by P5 part 1: none, apart from the expected upstream fingerprint check.

### P5 part 2: cold start + select-v1 selection, gates and replay

Scope (spec section 8, R3, R6, R9, R11; section 13 P5 items about cold start, selection and
replay): new modules `recommendation/ranking/selection.py` (tie group, cold-start diagnostics
C/B/S, `cold_start_active`, R3 seeds and streams, `select_v1` shared by the run and by replay,
`select()` for the rank stage building `SelectionSummary`, per-item reasons/notes, the R6
`selection_input` and a trace record) and `recommendation/ranking/exploration.py` (full-pool
price quantile edges with duplicates merged, [e_i, e_{i+1}) bins with the last closed and a
null-price bin, largest-remainder allocation with availability caps/redistribution and RNG
remainder ties, uniform within-bin sampling). `tools/dispatcher.py` rank_candidates runs
`selection.select` after full-pool scoring and returns the selection (`selected_ids`,
`data.selection`); ScriptedController and LiveController return it unchanged.
`validation/selection_replay.py` registers `select-v1` (ids, mode and reasons recomputed from the
snapshot only) and keeps `ranked-prefix-v1`; `validation/gate.py` final_gate also requires pool
membership and that the recorded selection replays from its own input, replay_gate also checks
mode, seed and per-item reasons for select-v1. `assembly.py` takes the summary, reasons and
notes from the selection; `service.py` records `context.selection_policy`. The `cold_start` /
`selection` YAML markers and `settings/pending.py` entries are removed (only `trace.mode` and
`trace.debug_max_rows` stay pending, P6). `execution.selection_algorithm` = `select-v1`.

#### State before P5 part 2 (pre-existing failures)

pytest generic gate re-run at the start of part 2: **361 passed** (same as the end of part 1).
Other gates as recorded at the end of part 1 (schemas --check 0, both typechecks 0, `npm test`
207 / 187 pass / 0 fail / 20 skipped). `scripts/import-recommendation-upstream.py --check`
fails as expected (`Upstream port fingerprint changed`, P7). No other pre-existing failure.

#### P5 validation items covered by part 2

Tests are in `recommendation/tests/test_selection_v1.py` (35 tests in part 2; 38 after the P5 review round) unless noted.

| Item (spec section 13, P5) | Proof | Outcome |
|---|---|---|
| No soft requirements & all tied → exploration | `test_no_soft_requirements_and_all_tied_enters_price_exploration`: 40 identical listings, no criteria → C 0 (reason starts `no_soft_requirements`), B 40, S < 0.9, active, mode `cold_start_exploration`, R 40, stage A 30, quotas sum 30, sampled 10. Live: hard-geography pool without soft criteria → same mode, C 0, B 4,684, S 0.005 (below) | pass |
| Unresolved data not treated as under-specification | `test_unresolved_data_is_not_treated_as_under_specification` (service, fixture): soft district + soft distance to an unresolvable name → status success, no clarification, resolution `unresolved`, C 0.5 with `c_below_min` and never `no_soft_requirements`, item coverage 0.5; with a hard bedrooms criterion the hard-unknown listing (fixture-10) is excluded and C is unchanged | pass |
| Threshold boundaries (C, B, S exactly at thresholds) | `test_threshold_boundaries_c_b_s_exactly_at_thresholds`: `exit_conditions(0.8, 5, 0.9)` true; `nextafter` below C, B 6, `nextafter` below S, and S None each false; computed C = mean(1, 1, 1, .5, .5) = 0.8 and B = 5 exactly → neither `c_below_min` nor `b_above_max`, exit == (S ≥ 0.9); B 6 → `b_above_max`; C 0.7 → `c_below_min`. `test_plan_scenario_fully_distinct...`: C 1, B 1, S 1.0 → exit met | pass |
| S computed with every weight layer perturbed, components reweighted, random tie-break, reproducible | `test_stability_is_computed_reweights_components_and_is_reproducible`: identical stability seed and S for two request IDs / conversations (R3: no request_id), different feedback → different stability seed; `top_k_stability` runs with `PoolScores.__init__` patched to fail (no rescoring); `perturbed_weights` keeps layers for u = 0 and renormalises soft criteria, soft/behaviour, appointment/browsing, features. `test_stability_random_tie_break_never_fakes_stability`: all tied → S < 0.5 (≈ K/\|pool\|) instead of 1 | pass |
| Scoring before sampling | `test_scoring_happens_before_sampling`: the whole pool (40) is scored and ordered; `selection_input.head` equals O's prefix with the scored combined values; leaders = O[:3]; drawn ⊆ O[3:33] (R); every score_rank ≤ 33. Service: rank step `scoring.pool_scored` == pool size (40) before `data.selection` (`test_service_round_trip...`) | pass |
| Quota totals correct | `test_quota_allocation_largest_remainder_with_caps_and_rng_ties` ([5,3,2] of 7 → [4,2,1]; [1,10] of 5 → [0,5]; exact remainder ties resolved differently across seeds; 500 random cases: sum == size, 0 ≤ q ≤ count, \|q − exact\| < 1). `test_exploration_quota_totals_and_stage_sizes` (5 pool shapes × 25 seeds): candidate_count = max(\|G\|, min(30, \|pool\| − \|L\|)), sample_size = min(30, \|R\|), Σcount = \|R\|, Σquota = \|A\|, Σsampled = m, sampled ≤ quota ≤ count, proportional within 1 | pass |
| Price bins (R11) | `test_price_bins_full_pool_quantiles_duplicates_merged_last_bin_closed` (edges = sorted distinct numpy 'linear' quantiles, [e_i, e_{i+1}), last closed, null → own bin, single price → [e0, e0], no prices → only the unknown bin, out-of-range rejected); `test_price_bins_come_from_the_full_pool_not_the_candidates` (edges span the expensive tail outside R; unknown-price candidate bin listed) | pass |
| After exit no price exploration, near-tie sampling kept | `test_after_exit_no_price_exploration_but_near_tie_sampling_kept`: exited at this pool_key → evaluated, not active, `exited_by_orchestrator`, mode `near_tie_sampling`, no exploration, `bin_edges` null; exited at another pool_key + reenter → active, `reentered_pool_rebuilt`, exploration; reenter false → stays exited; `cold_start.enabled: false` → not evaluated, no exit claim, near-tie sampling. Service: `test_service_round_trip...` round 2 (simulated orchestrator exit injected at round 1's pool_key; round 1 did not meet the exit conditions, so an R7 orchestrator would not record it) → `near_tie_sampling`, reasons leader ×3 + tie_group_sample ×7. `test_data_version_only_rebuild_does_not_reenter_cold_start`: a data change rebuilds the pool (`built`, new data_version, same pool_key) and the exit still holds (R9) | pass |
| Diagnostics output complete | `test_service_round_trip_cold_start_then_exit_with_complete_diagnostics`: `selection` {mode, algorithm_version select-v1 (= execution.selection_algorithm), 64-hex seed, result_limit, leaders 3, tie_group size 27, exploration R 30 with 5 bins, cold_start {evaluated, active, reason, C, B, S all non-null, thresholds {0.8, 5, 0.9}, exit_conditions_met, pool_key = pool.pool_key}}; trace rank step `data.selection` (mode, stability seed, trials 20, durations), `context.selection_policy`, final_gate step `selection`; snapshot seeds.base == response seed | pass |
| Plan scenarios (exact ties) | `test_plan_scenario_leaders_kept_and_seven_drawn_from_the_tie_group[3-30-40 / 3-20-40]`: after exit → leaders L001–L003 + 7 `tie_group_sample` from G (27 / 17), tie group {size, 0.3, 0.3, span 0, ε 0.02}; in cold start → leaders kept + 7 `price_exploration_sample` from R (30). `test_plan_scenario_all_tied_draws_ten_from_the_whole_pool` (10 of 40, head = whole pool). `test_plan_scenario_fully_distinct_selects_by_score_even_with_cold_start_enabled` (mode ranked = O[:10]). `test_plan_scenario_small_pool_returns_everything_in_score_order` (7 ≤ 10 → all, `small_pool`; empty pool → `empty`) | pass |
| No duplicates / no out-of-pool | `test_no_duplicates_and_nothing_outside_the_pool[True/False]`: 2 pools × 150 seeds × (exited / cold start): 10 unique IDs ⊆ pool. final_gate rejects IDs outside the pool | pass |
| Same request replay identical | `test_same_request_replays_identically_and_tampering_is_rejected`: the second identical request returns the equal stored response (`immutable_replay`); `replay_select(snapshot.selection_input)` reproduces the IDs; replay_gate passes; tampered response mode / seed / a selection_reason and tampered snapshot `cold_start_active` / stageB seed → INVALID_SNAPSHOT. `test_replay_recomputes_select_v1_from_the_stored_input_only`: five modes round-trip through JSON with identical ids/mode/reasons; the stored keys are exactly the R6 set; wrong K, reversed or truncated head, unknown version → INVALID_SNAPSHOT | pass |
| ranked-prefix-v1 snapshots still replay | `test_ranked_prefix_v1_snapshots_still_replay` (a prefix `selection_input` built with `prefix_selection_input` passes replay_gate; mode check skipped for that version) | pass |
| New request can resample | `test_new_request_can_resample_with_the_same_inputs`: 6 chained requests, same requirements and pool → more than one distinct selection, identical S, leaders always L001–L003 | pass |
| Config change requires a new request | `test_config_change_requires_a_new_request`: the old request under a changed ε returns its stored response; a new request records the new selection_hash and its seed equals `derive_seeds(changed, …)` and differs from the seed under the old settings. `test_seeds_follow_r3`: base/tie seeds are the R3 sha256 chain; request_id and selection_hash change the base; a non-selection key (`trace.preview_rows`) does not; the override replaces the base | pass |
| ≥ 200-seed statistical check, every tie-group member selected | `test_every_tie_group_member_is_selected_across_seeds_and_ids_are_not_fixed`: 400 request IDs, G = 27, m = 7: every member drawn, each count within 5 sd of 400·7/27 ≈ 103.7; in cold start every G member is drawn too (R members in a bin with quota 0 are not, see OPEN_ITEMS) | pass |
| Not permanently selecting the same IDs | same test: the 7 lowest IDs of G (what an ID tie-break would return) come out together at most once, and > 95 % of the 400 draws are distinct sets | pass |
| final_gate requires proposal == selection | `test_final_gate_rejects_the_score_prefix_when_it_is_not_the_selection`: a controller proposing the score prefix → INVALID_PROPOSAL after the repairs. `test_live_controller_returns_the_recorded_selection` (fake LiveController model returning `selected_ids`) → success, IDs == rank step `selected_ids`, mode cold_start_exploration | pass |
| Timing test (R10: scoring + diagnostics + selection) | `test_timing_full_pipeline_on_synthetic_21431_listing_pool` (synthetic 21,431 listings, 5 references, assert < 3 s): soft+behaviour case **0.177–0.202 s** total (scoring 0.052–0.055 s, selection 0.12–0.14 s of which diagnostics 0.11–0.13 s, 20 trials), mode cold_start_exploration; no-soft all-tied case **0.076–0.113 s** (head = 21,431, replay 0.024–0.061 s), over the 5 runs in this phase. P5 review round: the per-listing criterion evaluation (`validation/rules.evaluate` over the pool, which rank_candidates also runs) is now timed separately and the end-to-end figure including it is asserted < 3 s as well: soft+behaviour 0.171–0.282 s + evaluation 0.111–0.135 s = **0.28–0.42 s**; no-soft case (0 references, printed correctly now) 0.092–0.111 s + 0.007–0.012 s = **0.10–0.12 s** (4 runs) | pass |
| End-to-end service tests (fixture/scripted) | `test_service_round_trip...`, `test_data_version_only_rebuild...`, `test_same_request_replays...`, `test_new_request_can_resample...`, `test_config_change_requires_a_new_request`, `test_final_gate_rejects...`, `test_live_controller_returns...` (synthetic 40-listing fixture file through `FixtureListingRepository`, real service, StateStore, pool cache) | pass |

#### Live read-only smoke (database mode, ScriptedController, temporary state, no LLM)

Script `evaluation/recommendation-exploration-2026-10-23/p5/live_selection.py`, output
`p5/live-selection.json`. Live state `data/.runtime/recommendation/state.sqlite` md5 before and
after: `247b2503ac952c17f8676443f5f8355a` (unchanged).

| Scenario | Pool | Mode | Leaders | Tie group | C / B / S, reason | Exploration | Timing (ms): service / rank step / scoring / selection (diagnostics) |
|---|---|---|---|---|---|---|---|
| attributes-soft-district round 1 (hard budget ≤ 3000 + bedrooms ≥ 2, soft D05) | 306 (built) | cold_start_exploration | 3 | 303 | 1.0 / 303 / 0.315, `b_above_max; s_below_min` | R 303, A 30 in 3 bins (5 quantile bins, duplicate edges merged: 450–2800 / 2800–2900 / 2900–3000; quotas 6/4/20, sampled 1/1/5) | 1273 / 7.8 / 0.8 / 2.5 (2.0) |
| same, round 2-simulated-exit: round-1 browsing + a **simulated** orchestrator exit (`cold_start.exited` injected at the round-1 pool_key although round 1 reported `exit_conditions_met` false; R7 would not record this exit, the round only exercises near-tie sampling on live data) | 306 (reused) | near_tie_sampling | 3 | 111 (span 0.016) | 1.0 / 111 / 0.97, `b_above_max; exited_by_orchestrator` | none | 344 / 26.3 / 2.8 / 5.7 (5.5) |
| hard-geography-no-soft (hard budget ≤ 4000 + within 1500 m of a Hospital) | 4,684 (built) | cold_start_exploration | 0 | 4,684 | 0.0 / 4,684 / 0.005, `no_soft_requirements; b_above_max; s_below_min` | R 4,684, A 30 (6/4/8/6/6), sampled 2/1/3/2/2 | 1778 / 128.5 / 2.0 / 10.3 (6.3) |

(Re-run in the P5 review round after relabelling the simulated exit; the re-run overwrote
`p5/live-selection.json`. Mode, leaders, tie groups, C/B/S, reasons and the hard-geography quotas
match the first run's table. The first table listed "5 bins" for round 1. The re-run shows 3
merged bins, which is what the R11 duplicate-edge merge gives for this price distribution:
198 of 303 candidates sit in 2900–3000. The first run's file is gone, so "5" cannot be
re-checked. It was most likely the configured `quantile_bins`. Timings above are from the
re-run.)

#### Test assertions changed on purpose (P5 part 2)

- `test_pool_cache.py::test_compact_snapshot_and_replay_gate`: `algorithm_version ==
  "ranked-prefix-v1"` → `"select-v1"` (the rest unchanged; the pool is not larger than
  result_limit, so the head is still the returned IDs).
- `test_pool_cache.py::test_destination_name_variants_share_a_pool_only_when_they_resolve_alike`:
  the uncached comparison run uses the same conversation and request IDs as the cached run
  (was `"u"`/`"u"`), because the select-v1 seed depends on them (R3); the comparison of status,
  pool size and IDs is unchanged.
- `test_geo_tools.py::test_malicious_listing_and_place_text_has_no_effect_on_criteria_or_tool_policy`:
  `"fixture-12" not in recommendations` → recommendations == the latest rank_candidates
  `selected_ids` and != `["fixture-12"]` (pool 11 > 10 is now in cold start, so exploration may
  draw fixture-12 on its own; the obeyed injected answer is still rejected by the gate,
  `[False, True]`).
- `tests/fixtures/recommendation-v2/ten-results.json`: regenerated with the scratchpad generator
  `gen_ten_results.py` (same 10 IDs and scores; selection now `cold_start_exploration` with 6
  leaders + 4 `price_exploration_sample`; a fresh run reproduces it).
- `tests/fixtures/recommendation-v2/response-cases.json`: case
  `cold-start-active-without-evaluation` merges `evaluated: false` explicitly (the regenerated base
  is evaluated); two cases added (`cold-start-exit-claim-without-stability` invalid,
  `cold-start-exit-with-evaluated-diagnostics` valid), run by the Python test and the TS mirror.
- Regenerated `recommendation/examples/contracts/*.json`: field-path diff against the files
  before part 2 shows only `execution.selection_algorithm`, `selection.{algorithm_version, seed,
  cold_start.reason}` and volatile IDs/durations/state refs;
  `npx tsx scripts/validate-recommendation-examples.ts` passed.
- `settings/pending.py` / YAML: `cold_start` and `selection` entries and markers removed
  (`test_not_in_effect_markers_match_pending_table` unchanged and passing).

#### Generic gates after P5 part 2

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **398 passed** (361 + 35 in `test_selection_v1.py` + 2 new response cases) |
| `python -m recommendation.schemas --check` | exit 0 (generated schemas unchanged) |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 207 tests: **187 passed, 0 failed**, 20 skipped |
| `npx tsx scripts/validate-recommendation-examples.ts` | passed |
| `scripts/import-recommendation-upstream.py --check` | fails as expected (`Upstream port fingerprint changed`); part-2 manifest files listed in INVENTORY section M |
| live state `data/.runtime/recommendation/state.sqlite` | md5 `247b2503ac952c17f8676443f5f8355a` (unchanged); services on 8010/8091/8092 untouched |

New failures introduced by P5 part 2: none, apart from the expected upstream fingerprint check.

### P5 review round (fixes)

| Finding | Severity | Outcome | Change |
|---|---|---|---|
| `terminal/acceptance.py` duplicate_feedback required identical IDs/order for a re-sent snapshot under a new request_id | major | fixed | The step now compares only request-independent outputs (`resent_feedback_differences`): status, pool_version, selection mode / result_limit / leaders / tie group / cold-start diagnostics (S uses the request-independent stability seed), exploration candidates and bin counts, leader IDs, the full ID order for seedless modes (ranked / small_pool / empty), scores + score_rank + criterion results of every listing present in both, and the rank_candidates scoring summary (references, weights, spreads; timing excluded) from both traces. A changed pool_version gives `blocked` (source change, check inconclusive); any other difference gives `failed`. Modes, seeds and differences are recorded in the step. New tests: `test_acceptance_duplicate_feedback_tolerates_resampling_on_a_pool_larger_than_the_limit` (40-listing synthetic pool, fixture/scripted acceptance: cold_start_exploration with different seeds and different IDs in 3/3 runs, step passed) and `test_resent_feedback_comparison_ignores_resampled_items_but_not_score_changes` (fixed request IDs: resampled IDs → no difference; a tampered score → `scores of <id>`; a changed scoring summary → reported). Database-mode re-run of the reviewer's scenario (hard price ≤ 3000 + bedrooms ≥ 2, soft D05; scripted controller; output in the scratchpad): **all 8 steps passed**, duplicate_feedback with pool 306, two cold_start_exploration responses with different seeds, the same 3 leaders and 7 different samples, differences `[]`; live state md5 unchanged |
| `execution.strategy` still "complete-pool weighted ranking" | minor | fixed | `service.py`: `"full-pool fused-v2 scoring + select-v1 selection"`. Examples regenerated (`python -m recommendation.examples.generate_contract_examples`): diff against the previous files = only `execution.strategy` and volatile IDs/durations/state refs; `tests/fixtures/recommendation-v2/ten-results.json` regenerated with the scratchpad `gen_ten_results.py` (same IDs/scores/reasons; only strategy and volatile fields changed); `validate-recommendation-examples.ts` passed |
| Selection tests pinned to the committed YAML's provisional values | minor | fixed | `test_selection_v1.py` builds every config from an explicit fixture (`FIXTURE`: `selection.tie_max_score_span` 0.02, `cold_start.stability.trials` 20, `weight_perturbation_fraction` 0.2, `price_sampling.target_sample_size` 30, `quantile_bins` 5), and asserts against the `EPSILON/TRIALS/FRACTION/TARGET/BINS` constants instead of literals (service tests pass the fixture config too). The fully-distinct scenario now asserts its premise: the score gaps around the cutoff (0.03) are > `EPSILON / 2`. Confirmed values (thresholds 0.8/5/0.9, weights 0.6/0.4) are still read from the YAML. Expectations themselves unchanged |
| Ranking-prefix live oracles and INVENTORY status inconsistent | minor | fixed (one oracle rewritten, two rescheduled explicitly) | `tests/native-geography-live.test.ts:24` rewritten as a select-v1 oracle (leaders == O prefix; every score_rank == position in the independently computed O; drawn items directly after the leaders within \|G\| / \|R\| with matching selection reasons; exact prefix only for ranked / small_pool); type-checked with a scratch tsconfig, env-gated and not run (needs the 8089 data API and a native service running this code). `native-recommendation-live.test.ts` (whole test predates 2.0) and `advanced-live.test.ts` (app path, baseline-style tie-break) rescheduled to P7 with the same status in INVENTORY sections A and P and in OPEN_ITEMS. `controller.py:106` row → resolved-P4 (prompt in `agent/prompt.py`, rule 7 returns `selected_ids`). The P7 `legacy` grep criterion amended: weighted-v1 module/constants/scoring-id references must be absent (empty today); the contract-1.0 `legacy_*` readers are kept on purpose |
| Tie-group boundary compared on raw floats | minor | fixed (documented pin + test) | No tolerance added: replay repeats the identical comparison on the stored head, and a tolerance would change membership for existing select-v1 snapshots. `selection.tie_group` docstring pins the IEEE-double evaluation (R11-style); `test_tie_group_boundary_is_evaluated_on_ieee_doubles_without_tolerance` covers decimal scores at the boundary (0.31/0.29 vs 0.30 at ε 0.02 both excluded; 0.32 excluded and 0.28 included at ε 0.04) and binary-exact boundary members (included); OPEN_ITEMS entry added |
| Live smoke round 2 presented as a realistic exit | minor | fixed | `p5/live_selection.py` docstring and output label it a simulated orchestrator exit (`round-2-simulated-exit`, `scenario_note`); smoke re-run (read-only, temporary state, live md5 unchanged), table above relabelled. The same label was added to the service test comment and the "after exit" row |
| Timing test excluded `evaluate()` and printed "5 refs" for the 0-reference case | minor | fixed | Criterion evaluation timed separately, end-to-end incl. evaluation asserted < 3 s too, reference count printed from the request (5 / 0); figures in the timing row above |

Test assertions changed on purpose (review round): none weakened. `test_selection_v1.py`
literals became fixture constants with the same values. One premise assertion was added (fully
distinct). The service-test comment was relabelled. Three tests were added.

#### Generic gates after the P5 review round

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **401 passed** (398 + 3 new) |
| `python -m recommendation.schemas --check` | exit 0 |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 207 tests: **187 passed, 0 failed**, 20 skipped |
| `npx tsx scripts/validate-recommendation-examples.ts` | passed |
| `scripts/import-recommendation-upstream.py --check` | fails as expected (`Upstream port fingerprint changed`, P7) |
| live state `data/.runtime/recommendation/state.sqlite` | md5 `247b2503ac952c17f8676443f5f8355a` (unchanged); services on 8010/8091/8092 untouched |

## P6

### P6 part 1: slim trace (stages, compact tool/model steps, debug mode, references)

Scope (spec section 9, R13, R14 trace part; section 13 P6 items about the trace): new modules
`recommendation/tracing/slim.py` (layout `slim-v1`: stage record helper, compact tool step /
compact ToolResult, model steps with only the added messages, transcript reconstruction,
debug rows, error location, `finalize` = drop controller scratch state + `redact` + `scrub`,
`resolve_step`) and `recommendation/tracing/stages.py` (stage records of the pipeline tools).
`service.py` records input_validation, final_gate (also for the immutable replay) and
response_assembly stages, writes `trace.error = {stage, code, message, retryable}` on failure and
persists `slim.finalize(trace)`. `tools/dispatcher.py` stores arguments + a compact result per
tool step (no `snapshot`), writes the stage records (rank_candidates: scoring,
cold_start_diagnostics, price_sampling, final_selection with per-sub-stage durations from
`selection.select(..., marks=)`), and the `debug` block when `trace.mode` is `debug`.
`agent/controller.py` model steps store `messages_added` / `message_offset` / `message_count`
(tool schemas once per trace). `state/store.py` `resolve_reference` delegates to
`slim.resolve_step` and adds `legacy_note` to legacy v1 content. `trace.mode` and
`trace.debug_max_rows` are in effect: their `settings/pending.py` entries and YAML markers are
removed (the table is now empty). Terminal commands (show-* / set-config / step / export /
show-trace --debug and the transcript) are part 2 and not started.

#### State before P6 part 1 (pre-existing failures)

`pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q`
401 passed; `schemas --check` exit 0; both typechecks exit 0; `npm test` 207 tests, 187 passed,
0 failed, 20 skipped; `scripts/import-recommendation-upstream.py --check` fails as expected
(`Upstream port fingerprint changed`, registration is P7). No other pre-existing failure.

#### P6 part 1 validation items

| Item (spec section 13 P6 / task) | Proof | Outcome |
|---|---|---|
| Stage records input_validation, entity_resolution, pool_build_or_load, geographic_evidence, scoring, cold_start_diagnostics, price_sampling, final_selection (+ final_gate) with status, duration_ms, input/output counts, key params, versions (config_hash, selection_hash, pool_version), preview ≤ preview_rows, unknown summaries | `test_trace_v2.py::test_stage_records_cover_every_stage_with_counts_params_versions_and_bounded_previews` (40-listing cold-start run: exact stage order incl. final_gate and response_assembly; every record ok with duration ≥ 0, config/selection hash = response.execution; pool_version on every stage after entity resolution; previews ≤ 10; pool / scoring / cold-start / sampling / selection / validation outputs equal the response); `test_price_sampling_is_skipped_outside_cold_start_and_not_evaluated_diagnostics_say_why` (skipped status + reason) | pass |
| Default trace does not serialize the pool into tool steps | `test_default_trace_does_not_serialize_the_pool_into_tool_steps` (4,000-listing pool: tool steps have exactly {kind, tool, snapshot_ref, stages, arguments, result, duration_ms}, ID lists and previews ≤ preview_rows, no `rows` / `validation` / `nearest_exits` in the stored text, at most 70 distinct listing IDs named in the whole trace); `test_geo_tools.py::test_geographic_evidence_result_is_compact_for_the_model` (pool-wide nearest exits stay in run memory; P6 review: the returned listings' exits are recorded by the final gate, `test_final_gate_records_the_nearest_exit_of_the_returned_listings_only`) | pass |
| Tool steps: arguments + compact result; query tool data cut to preview_rows with original lengths | `test_trace_size_is_bounded_under_repeated_tool_calls_and_capped_by_model_result_candidates` (was `..._does_not_scale_with_candidates`; nearest_mrt with 100 IDs: stored arguments 10 IDs + `_truncated {listing_ids: 100}`, data 10 results + `data_truncated.results == 100`) | pass |
| Model steps store only the messages added since the previous model step; resolve_reference reconstructs | `test_model_steps_store_added_messages_and_references_reconstruct_what_the_model_received` (every `context_ref` resolves to exactly the message list the fake model received; `<trace>/messages` = transcript); repeated-calls test: Σ len(messages_added) == last message_count, offsets contiguous, no `messages` / `tool_schemas` per step | pass |
| Trace size independent of candidates × tool calls (measured) | `test_trace_size_is_independent_of_pool_size` (pools 40 / 400 / 4,000, scripted: sizes within 1%); `test_trace_size_is_bounded_under_repeated_tool_calls_and_capped_by_model_result_candidates` (pools 400 / 4,000 × 2 / 5 query calls: totals within 1%, growth per call within 1%; P6 review: pool 40 added, its growth per call is smaller). Measurement table below | pass for scripted runs and for live runs whose pool exceeds `controller.model_result_candidates`; **bounded, not independent** for live runs below the cap (P6 review): each live query call stores the model-facing message, so growth per call follows min(pool, model_result_candidates) and a live trace is bounded by about `max_tool_calls` × the capped per-call size (20 × ~57 KB ≈ 1.1 MB) |
| Errors locate a stage | `test_errors_locate_the_pipeline_stage` (injected failures in resolve_criteria / retrieve_versioned / geographic evidence / score_pool → `trace.error.stage` = entity_resolution / pool_build_or_load / geographic_evidence / scoring, one error record with code and `call`); `test_errors_locate_the_rank_sub_stage` (evaluate_cold_start / stratified_sample / `_notes` → cold_start_diagnostics / price_sampling / final_selection; earlier sub-stages of that call recorded ok, no later ones); `test_errors_locate_input_validation_final_gate_and_model` (STALE_REFERENCE → input_validation; wrong proposal → final_gate, one error record per attempt; MODEL_UNAVAILABLE → `model`) | pass |
| trace.mode summary / debug (config and RECOMMENDATION_TRACE_MODE); debug adds ≤ debug_max_rows rows (top scores with contributions, tie group, exploration bins) | `test_debug_mode_adds_bounded_rows_with_contributions_tie_group_and_bins` (27 tie members at the tie score, 30 exploration candidates with bin / stage A / drawn flags matching the response, 40 top rows with contributions; 97 ≤ 200; summary mode has no `debug`); `test_debug_rows_never_exceed_debug_max_rows_and_keep_every_kind_of_row` (P6 review, shared budget: debug_max_rows 20 → 7 drawn exploration candidates + 10 top-score rows + 2 tie members + 1 more candidate, score order kept; tie group 297 > 200 → all three kinds present, all 7 drawn listings included; near-tie mode with 12 → the 7 drawn tie members + 5 top rows); `test_trace_mode_env_toggles_debug_and_is_in_effect`; `test_settings.py::test_not_in_effect_markers_match_pending_table` (trace keys no longer pending) | pass |
| `<trace>/pool` via pool_cache; evicted → STALE_REFERENCE | `test_pool_reference_resolves_from_pool_cache_and_reports_eviction` (retention 1: the first pool resolves, then "Pool evicted"); existing `test_pool_cache.py::test_retention_index_bound_and_evicted_pool_reference` and the not-persisted case still pass | pass |
| snapshot_ref works for v2 runs (compact step record); legacy v1 traces readable with an explicit legacy note | `test_model_steps_store_added_messages_and_references_reconstruct_what_the_model_received` (every snapshot_ref → its step record; unknown ref / other conversation → STALE_REFERENCE); `test_legacy_v1_trace_is_readable_with_an_explicit_note` (baseline `initial.trace.json` from 4d36507 inserted as a legacy row: trace and step snapshot returned with `legacy_note`, terminal `show-trace` prints "Note: legacy v1 trace"; an older P2–P5 v2 trace still returns its stored snapshot / messages); `test_recommendation.py::test_reference_resolution_and_gate_replay` | pass |
| No credentials (redact still applied) | `test_trace_never_stores_credentials` (sentinel secret in env, in model-sent tool arguments `api_key` / `password` / `accessToken` / `databaseUrl` / a `user:pass@host:port` string and in model content as a postgres URL: absent from the stored text, `[REDACTED]` present; `usage.total_tokens` preserved) | pass |
| Evidence refs complete | `test_evidence_refs_are_complete_and_only_referenced_evidence_is_returned` (hard + soft geographic criteria: every item ref resolves to evidence of the same listing and criterion, the response evidence equals the referenced set, final-gate check refs ⊆ response evidence) | pass |
| `/app/recommend` counts / proofs unaffected | `test_app_recommend_counts_and_proofs_are_unaffected_by_the_slim_trace` (TestClient, summary and debug mode: listings = returned rows, counts = {assessed: source_count, eligible: size, unverified: excluded_hard_unknown, excluded: excluded_hard_fail}, assessed = sum; stored trace is slim-v1; presentation identical across trace modes); existing `tests/test_native_transport.py` | pass |
| Terminal transcript (first recommendation, feedback re-rank, config comparison with fixed seed, cache hit/miss, entity ambiguity, inspection, replay) | part 2 of P6 (see "P6 part 2" below) | not_done in part 1; pass in part 2 |

#### Measurement (trace size vs pool size and tool calls)

Scratch script `p6/measure.py` (scratchpad), fixture data, plan_rows(3, 30, n) pools, scripted
controller (0 query calls) or the `RepeatedQueries` fake model (nearest_mrt over the 100
model-visible IDs per call). "P5 snapshot bytes" = the dispatcher snapshots the P5 trace stored
in its tool steps (model-message duplication of P5 not included).

| pool | query calls | status | tool steps | slim trace bytes | P5 snapshot bytes (tool steps only) | distinct listing IDs in trace |
|---|---|---|---|---|---|---|
| 40 | 0 | success | 4 | 63,550 | 65,071 | 16 |
| 40 | 2 | success | 6 | 173,104 | 131,817 | 40 |
| 40 | 5 | success | 9 | 279,277 | 242,931 | 40 |
| 400 | 0 | success | 4 | 63,770 | 525,838 | 16 |
| 400 | 2 | success | 6 | 218,209 | 1,013,600 | 100 |
| 400 | 5 | success | 9 | 388,380 | 1,749,641 | 100 |
| 4000 | 0 | success | 4 | 63,826 | 5,194,064 | 15 |
| 4000 | 2 | success | 6 | 218,241 | 9,891,004 | 100 |
| 4000 | 5 | success | 9 | 388,443 | 16,956,205 | 100 |

Reading: the slim trace does not change with the pool size once the pool exceeds the
model-visible cap. Each live query call adds ~56 KB, almost all of it the model-facing tool
message (100 IDs + 100 nearest-MRT results, bounded by `controller.model_result_candidates`).
That message is stored once, so `resolve_reference` can rebuild exactly what the model saw. The
tool step itself stores 10 rows. Known effect (OPEN_ITEMS candidate): in live mode the
transcript grows with `model_result_candidates` × query calls (≤ `max_resolution_tool_calls`),
never with the pool size.

#### Test assertions changed on purpose (one at a time, intent kept)

- `test_recommendation.py::test_full_flow_and_restart`: build step `snapshot.rows` → the build step's
  pool_version equals `trace.pool_ref` and `<trace>/pool` has rows.
- `test_recommendation.py::test_reference_resolution_and_gate_replay`: `resolve_reference(snapshot_ref)["rows"]`
  → the resolved step record's pool_version equals the response pool.
- `test_policy_controller.py::test_live_dispatch_recovery_content_blocks_and_trace`: `messages[-1]` → `messages_added[-1]`.
- `test_policy_controller.py::test_model_preview_limit_preserves_full_pool`: `snapshot.pool_ids` count → `<trace>/pool`
  rows; `snapshot.scores[..].contributions` → the response item's contributions (model preview still has none).
- `test_scoring_v2.py::test_whole_pool_is_scored_and_ordered_by_combined_then_id`: `snapshot.ranking/scores`
  → scoring stage `outputs.scored` / `preview_total` / `breakdowns_built` (= selection ∪ preview rows).
- `test_geo_tools.py::test_geographic_evidence_result_is_compact_for_the_model`: provenance "kept in the trace
  snapshot" → kept in run memory and absent from the trace (intended rule change).
- `test_geo_tools.py` (two live tests): `trace.controller_messages` → `resolve_reference(<trace>/messages)`.
- `test_settings.py::test_not_in_effect_markers_match_pending_table`: added the three trace keys as not pending.

#### Generic gates after P6 part 1

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **423 passed** (401 + 22 in `test_trace_v2.py`); also 423 passed with `RECOMMENDATION_TRACE_MODE=debug` exported |
| `python -m recommendation.schemas --check` | exit 0 (contracts unchanged) |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 207 tests: **187 passed, 0 failed**, 20 skipped |
| `python -m recommendation.examples.check_export` (trace consumer) | runs; `gate: true` (`steps[-1]` is still the gate) |
| `scripts/import-recommendation-upstream.py --check` | fails as expected (`Upstream port fingerprint changed`, P7); P6 manifest files listed in INVENTORY section M |
| live state `data/.runtime/recommendation/state.sqlite` | md5 `247b2503ac952c17f8676443f5f8355a` (unchanged); services on 8010/8091/8092 untouched |

New failures introduced by P6 part 1: none, apart from the expected upstream fingerprint check.

### P6 part 2: terminal debugging commands (spec section 11, R14)

Scope: the new module `recommendation/terminal/debug.py` (outside the manifest). It has three parts:

- `Events` / `ObservedController`: structured progress events, application of the seed override,
  and the dispatcher retained from the latest run.
- `StepSession`: scripted step mode. Each stage runs inside a rolled-back state transaction, and an
  input change invalidates only the stages that depend on it.
- The command handlers.

It is wired into `recommendation/terminal/__main__.py` (manifest), which gained:

- staged `cold_start` / `result_limit`, `ensure_service`, `apply_config`, `input_changed` and
  `step_session`;
- a response summary with pool cache HIT/MISS;
- `--script` and `--brief`, both read with getattr defaults;
- `RecommendationError` printed as `CODE: message`;
- the extended help.

Every existing command is kept; `show-trace` moved into `debug.py` and keeps the legacy note. The
service, dispatcher, contracts and settings are unchanged.
`evaluation/recommendation-exploration-2026-10-23/TERMINAL.md` is the usage guide, linked from
`recommendation/README.md`.

#### State before P6 part 2 (pre-existing failures)

The P6 part 1 gates: 423 passed; `schemas --check` exit 0; both typechecks exit 0; `npm test`
187 passed / 0 failed / 20 skipped. `scripts/import-recommendation-upstream.py --check` fails as
expected (`Upstream port fingerprint changed`, P7). There are no other pre-existing failures.

#### P6 part 2 validation items

| Item (spec section 13 P6 / section 11 / R14) | Proof | Outcome |
|---|---|---|
| Terminal transcript: first recommendation | `recommendation/tests/test_terminal_debug.py::test_terminal_transcript_covers_the_p6_flows` step 1. Fixture data, scripted controller, temporary state; `set-config recommendation.default_result_limit 3` + `set-seed fixed-seed`. The run returns success with 3 items and `cache=built (MISS)`. A start and an end-ok event are printed for every pipeline tool, plus `gate: final_gate ok (selected=3, rows_read=3)`. The trace records `seed_override.value`, and the selection seed equals the override-derived base. | pass |
| Terminal transcript: feedback re-rank | Same test, step 3. `feedback-template` gives one browsing record per shown ID (null placeholders, in display order). It is filled with 150 s for rank 1 and 0 for the others, then staged with `feedback @file`. The next `recommend` gets a new request_id and `cache=reused (HIT)` (same pool_version). The browsing reference is exactly the rank-1 listing; every other pool listing has a browsing score; the full-pool order changed. | pass |
| Terminal transcript: config comparison with a fixed seed | Same test, step 4. With the same settings and seed, a new request_id gives the same IDs and seed as the first run (same config_hash). After `set-config cold_start.enabled false`, config_hash differs, the seed is the same, and the mode changes from cold_start_exploration to near_tie_sampling: all IDs come from the 5-member D05 tie group, while exploration had drawn outside it. P6 review: weight comparison under the same seed and the same staged browsing feedback (`set-config ranking` 0.6/0.4 → 0.0/1.0): selection_hash changes, the seed and pool_version do not, soft/behaviour component scores are identical per listing, every combined score equals the weighted sum, the full-pool order and the drawn IDs change (`[fixture-05, fixture-01, fixture-07]` → `[fixture-05, fixture-01, fixture-02]`). After `set-seed off` the seed differs and the trace has no `seed_override`. | pass |
| Terminal transcript: cache hit/miss | Same test, steps 3–5. Feedback re-rank, a same-input request and a settings-only change → `reused (HIT)`. A soft change (district D05 → D21) → HIT. A hard change (budget 3000 → 2600) → `built (MISS)` with a new pool_key. The ambiguity request → MISS. | pass |
| Terminal transcript: entity ambiguity | Same test, step 6. `destination_id URA:AREA:CLEMENTI` + `destination_name Bedok` → `needs_clarification` with no items; the summary prints `resolution: geo ambiguous (id_name_conflict)`. `show-resolution` prints `geo: AMBIGUOUS ... needs clarification (2 candidate(s))` with candidates `[URA:AREA:BEDOK, URA:AREA:CLEMENTI]`. | pass |
| Terminal transcript: single-listing inspection | Same test, step 2. `inspect-listing <rank 1>` is labelled "data retained in memory from this run", with `recomputed: false`, the selected rank, a score_rank and score breakdown equal to the response item, and the selection reason. `inspect-listing fixture-11` (outside the pool) is labelled recomputed: `recomputed: true`, `hard_failures == ["budget"]`. `show-pool` rows are in full-pool score order, `show-scores --top 4` gives the first 4 of that order, and `show-cold-start` / `show-selection` equal the response. After a restart: `test_inspection_after_restart_is_labelled_recomputed` ("RECOMPUTED from the current data"; same score_rank; show-pool rows come from pool_cache; show-scores says the retained scores are unavailable). | pass |
| Terminal transcript: original-request replay | Same test, step 7. `replay <first request id>` prints `REPLAY of <id> (stored response)` and "unchanged: True" while the session settings differ. The `runs_v2` count is unchanged and the terminal's current response is unchanged. `test_show_trace_debug_flag_and_help`: an unknown ID → "No committed run". | pass |
| show-config / set-config (session override, validated, never writes the file, new requests only) / reset-config / export-config | `test_set_config_is_validated_session_only_and_applies_to_new_requests`. Rejected: a layer sum ≠ 1, a negative ε, an unknown section, an unknown key, a secret-like key and a non-JSON value; settings stay unchanged after each rejection. A section merge sets the weights; `show-config [key]`; exported `.yaml` / `.json` files reload (`--config` loader) to the same config_hash, and `.txt` is rejected. The pending request is dropped, the next request has a new ID and the new config_hash, and the stored config of the earlier run is unchanged. The settings YAML sha256 is unchanged (also in the transcript test). | pass |
| recommend: always a new request_id except retry of a failed pending one; set-config / set-seed / feedback / load-request never reuse an ID | `test_recommend_mints_new_ids_except_retrying_a_failed_pending_request`: a simulated MODEL_UNAVAILABLE failure, then the retry keeps the same ID and succeeds. Each of recommend, set-seed, set-config and load-request (with a file holding an already used request_id) leads to a new, unique ID. | pass |
| show-input / validate-input / load-request @file (full 2.0 request) | `test_load_request_show_input_and_validate_input`. A full 2.0 body (with feedback, appointments, cold_start exited, result_limit 4, another conversation and a used request_id) is staged. `show-input` shows the terminal-managed conversation / previous_state_id, the "minted by recommend" placeholder and the loaded feedback / cold_start / result_limit; `validate-input` reports valid; the recommend returns 4 items with cold_start stored. Rejected: a 1.0 body and an invalid browsing snapshot. A stale head gives `Input invalid: STALE_REFERENCE` with no run written. | pass |
| feedback-template (one browsing record per shown id + appointments copied from the previous INPUT) | Transcript test step 3 (records per shown ID). The appointment copy is unchanged from P2 (`feedback_template(response, last_input)`); `test_contract_v2_fixtures.py` already covers it. | pass |
| set-seed (overrides the base seed only, recorded in trace) | Transcript test (trace `seed_override.value`, `selection.seed == sha256("override\|value")`, `show-selection` `seed_override: true`, removed by `set-seed off`); step-mode test (a seed change invalidates rank only). | pass |
| show-trace [request-id] [--debug] | `test_show_trace_debug_flag_and_help`: a summary trace has no `debug`, and `--debug` explains how to enable it. After `set-config trace.mode "debug"`, the default view summarises the block ("debug rows omitted") and `show-trace <id> --debug` includes the rows. An unknown option is rejected. The legacy note is still printed (`test_trace_v2.py::test_legacy_v1_trace_is_readable_with_an_explicit_note`). P6 review: `show-trace <request-id>` shows the run's own trace (by `execution.trace_id`) and names a newer replay trace; `--latest` shows that one; a replay trace is labelled (`test_export_show_trace_and_show_scores_use_the_runs_own_trace_after_a_replay`). | pass |
| export [dir] (request, response, trace, effective config) | Transcript test: exactly `{request.json, response.json, trace.json, config.json}`; config_hash = the run's, and its settings reload to that hash; the request is the run's. P6 review: after `replay <id>` of the exported run, trace.json is still the run's own trace (trace_id = `execution.trace_id`, full stage list) and the replay trace is written separately as `latest-replay-trace.json` (`test_export_show_trace_and_show_scores_use_the_runs_own_trace_after_a_replay`). | pass |
| step / run-until: scripted only; dependencies enforced; input change invalidates affected stages; hard-criteria change invalidates the pool; never a successful recommendation | `test_step_mode_enforces_dependencies_invalidates_on_input_change_and_is_never_a_recommendation`. `step rank_candidates` / `step build_pool` / `step final_gate` out of order → STALE_REFERENCE naming the predecessor. `run-until` runs in order. After `run-until final_gate`: dry-run gate passed, "INTERMEDIATE", no "success", `t.response` is None, no committed state, and 0 pool_cache rows (rolled back). Invalidations: seed → rank + gate; soft district → rank; soft geographic radius → geographic evidence + rank; hard budget → build_pool and later; geographic target → resolve_entities; `cache.*` setting → build_pool and later. `recommend` then submits the previewed request_id and returns exactly the step selection. `test_step_mode_is_scripted_only`: live controller → "scripted mode only". | pass |
| Live mode: structured progress events (tool start/end with counts + duration, model turn, gate) | `test_live_mode_prints_structured_progress_events` (fake LiveController: model turn start/end with tool_calls 4 / final answer; tool_end per pipeline tool with status, duration, pool=9 / ranked=9 / selected=9; gate_start then gate_end ok with selected / rows_read). `test_live_progress_reports_a_failed_gate_before_the_repair`: gate events error (INVALID_PROPOSAL), then ok. | pass |
| New args read with getattr defaults | Every existing test builds `argparse.Namespace` without `brief` / `script` / `config` and passes (test_terminal_repositories, test_geo_tools, test_settings, test_trace_v2). `test_script_mode_echoes_commands` covers `--script` (echo, comments, stops at `exit`, errors printed). | pass |
| Help documents that cold start cannot be exited without soft criteria | `test_show_trace_debug_flag_and_help`: the help contains "without soft criteria C = 0, so cold start cannot be exited without soft criteria" and names every debug command; `show-cold-start` prints the same note. | pass |
| Read-only live transcript (database mode + scripted controller + temporary state, no LLM) | `PYTHONPATH=. .venv/bin/python evaluation/recommendation-exploration-2026-10-23/p6/terminal_transcript.py database evaluation/recommendation-exploration-2026-10-23/p6/live-terminal-transcript.txt` (exit 0, 83,582 bytes, ~8 s). Read-only SQL / Neo4j; the state is a TemporaryDirectory; the live `data/.runtime/recommendation/state.sqlite` md5 `247b2503ac952c17f8676443f5f8355a` is unchanged before and after; the script refuses to save text matching credential patterns. Observed: pool 306 of 21,431 (excluded 20,680 fail / 445 unknown), `built (MISS)`, cold_start_exploration (3 leaders, tie group 303). Inspection of a retained item, and a recomputed out-of-pool listing (budget fail). Feedback re-rank → `reused (HIT)`, 4 leaders, new order. With the same seed and settings, the same 10 IDs under a new request_id. With cold start disabled, near_tie_sampling: the same 3 leaders and 7 different tie samples. Budget 2500 → `built (MISS)`, pool 27. Ambiguity → needs_clarification with candidates BEDOK / CLEMENTI. Step mode: STALE_REFERENCE first, then intermediate stages and the dry-run gate. Replay → stored response unchanged: True. Fixture companion: `p6/fixture-terminal-transcript.txt`. | pass |

#### Test assertions changed on purpose (P6 part 2)

None. Every existing test passes unchanged; `test_geo_tools.py::test_terminal_resolves_natively_without_typescript`
still finds `tool: <name>` (now inside `[progress] tool: <name> start`). 11 tests were added in
`recommendation/tests/test_terminal_debug.py`.

#### Rules decided in P6 part 2 (not literal in the spec)

- The step session runs the request `recommend` will submit: it creates the pending request, so
  `recommend` after `run-until final_gate` reproduces the previewed selection. Any input change
  mints a new pending request_id, which is itself a seed change, so rank is invalidated.
- Invalidation levels are in `debug.invalidation`:
  - resolve_entities: geographic criteria set or target fields; `geography` / `entity_resolution` settings;
  - build_pool: any hard criterion; `cache` settings;
  - get_geographic_evidence: a soft geographic criterion;
  - rank_candidates: everything else, including other settings, the seed, feedback and request_id.
- Step stages run in a rolled-back `StateStore.transaction()`, so pool_cache writes and LRU fills
  are discarded. `final_gate` is a step-mode dry run with its own stage record.
- The step time budget restarts for each step (`Dispatcher.started` is reset); tool calls count for
  the whole session (`step reset` restarts it).
- `load-request` stages requirements, feedback, cold_start and result_limit. The terminal keeps
  managing request_id, conversation_id, previous_state_id and requirements_version (printed).
- Inspection sources, in order: step session (INTERMEDIATE), the dispatcher retained from this
  terminal's latest run, then committed data. An out-of-pool listing is a fresh by-id read plus
  criterion evaluation, labelled `recomputed`. After a restart, `inspect-listing` re-runs the
  stored request with its stored configuration, read-only, labelled RECOMPUTED.
- `show-pool` page size = `trace.preview_rows`; `show-scores --top` defaults to it.
- `--brief` prints the summary instead of the full JSON; the response artifact file always has the
  full JSON.

#### Generic gates after P6 part 2

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **434 passed** (423 + 11 in `test_terminal_debug.py`) |
| `python -m recommendation.schemas --check` | exit 0 (contracts unchanged) |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 207 tests: **187 passed, 0 failed**, 20 skipped |
| `scripts/import-recommendation-upstream.py --check` | fails as expected (`Upstream port fingerprint changed`, P7); P6 part 2 manifest files listed in INVENTORY section M |
| live state `data/.runtime/recommendation/state.sqlite` | md5 `247b2503ac952c17f8676443f5f8355a` (unchanged); services on 8010/8091/8092 untouched |

New failures introduced by P6 part 2: none, apart from the expected upstream fingerprint check.

### P6 review round (fixes)

Each finding was checked against the code first (reviewer scripts in the scratchpad `p6rev/`
re-run before and after the fix).

| Finding | Severity | Outcome | Change |
|---|---|---|---|
| Debug rows: a tie group ≥ `debug_max_rows` crowded out the top-score rows and every exploration candidate (fixed order tie → exploration → top) | major | fixed | `slim.debug_rows` shares the budget: drawn listings first (exploration candidates in cold start, else tie members), then `trace.preview_rows` top-score rows with contributions (`selected` flag), then the other tie members and exploration candidates round robin, then more top-score rows. Each list keeps score order and reports `total` / `included`; tie members gain `drawn`, exploration candidates `score_rank`. Reviewer case plan_rows(3, 300, 400): was 200 tie / 0 exploration / 0 top, now 92 / 98 (all 7 drawn) / 10. Test rewritten to require every kind of row. |
| Replay stub returned by `export` / `show-trace <id>` / `show-scores` fallback (newest trace row of the request_id) | minor (reported twice) | fixed | `terminal/debug.py::_run_trace` resolves `response.execution.trace_id`; failed or legacy requests fall back to the newest trace. `show-trace <id>` names a newer replay trace, `--latest` shows it, a replay trace is labelled; `export` adds `latest-replay-trace.json` when a replay exists. Reviewer script: exported trace_id now equals `execution.trace_id` with the full stage list. New test `test_export_show_trace_and_show_scores_use_the_runs_own_trace_after_a_replay` (replay → export → show-trace → restart → show-scores preview has 3 rows). |
| Station exit provenance (R1c) dropped entirely | minor | fixed | `validation/gate.py` final_gate step gains `geographic_provenance` = per station criterion the exit used for each returned listing with a measured distance (from the gate's by-id evidence recomputation; ≤ result_limit entries per criterion). Pool-wide exits stay in run memory. New test `test_final_gate_records_the_nearest_exit_of_the_returned_listings_only` (two station criteria + one area: exits for exactly the measured returned listings, evidence entity stays `LTA:` station, exactly two `nearest_exits` maps in the stored trace). |
| "Trace size independent of candidates × tool calls" reported pass although live tool messages scale below the `model_result_candidates` cap | minor | fixed (record) | Outcome restated in the P6 part 1 table: pass for scripted runs and for live pools above the cap; bounded (not independent) below it, live bound ≈ `max_tool_calls` × capped per-call size. Test adds pool 40 (asserts the smaller growth) and is renamed `..._capped_by_model_result_candidates`. Shrinking the model-facing payload is not done (controller decision; OPEN_ITEMS, P7). Re-measured: per query call 40 → 35,394 B, 400 → 56,707 B, 4,000 → 56,705 B; scripted totals 63,564 / 63,762 / 63,794 B. |
| No fixed-seed weight comparison in the transcript | minor | fixed | Transcript test step 4 and `p6/terminal_transcript.py`: the same browsing feedback staged twice, `set-config ranking` 0.6/0.4 then 0.0/1.0, same seed override. Asserted: same seed and pool_version, different selection_hash, identical component scores, combined = weighted sum for every pool listing, the order and the drawn IDs change. Live transcript (database, scripted, temporary state): 0.6/0.4 → leaders 4, tie 42; 0.0/1.0 → leaders 3, tie 29, different items. |
| Step mode reported `persisted: true` for a rolled-back pool write | minor | fixed | `StepSession.step`: a pool built by the step (not a cache hit, which was persisted before) is marked `persisted = False`, the record's `outputs.persisted` is false and it gets a "rolled back" note. Step test asserts it; live transcript now prints `"persisted": false`. |
| `TEXT_LIMIT = 4000` hard-coded, not in the YAML | minor | deferred (documented) | Moving it changes config_hash and with it the generated contract examples and v2 fixtures; listed in OPEN_ITEMS for the P7 trace-limit calibration and named in the slim.py docstring. |
| OPEN_ITEMS row "Tool-step trace snapshots still embed the pool rows" still open | minor | fixed | Moved to "Resolved in P6 (slim trace)" with the P6 part 1 resolution and the version-digest change below. |
| `scrub` redacted whole JSON tool messages when a title had a bare '@' (`"title":"Suites@Changi","price":…` read as `user:pass@host:port`) | major | fixed | JSON-encoded strings are parsed and scrubbed value by value; the original string is kept byte for byte unless a value is a credential (then only that value is redacted). URL tokens stop at whitespace / quotes / backslashes / angle brackets; the scheme-less pattern's parts exclude quotes and JSON / list delimiters. Reviewer probe: every context_ref reconstructs (was: models/1, models/2 broken). Live check: 0 of 21,431 RENT rows flagged (785 contain '@'). New test `test_listing_text_with_at_signs_does_not_redact_model_messages` (5 '@' titles, live fake model, all references exact, no `[REDACTED]`; a credential inside JSON content still redacted alone); `test_trace_never_stores_credentials` unchanged and passing. |
| Older P2–P5 v2 trace: `<trace>/messages` raised KeyError('message_offset') | minor | fixed | `slim.resolve_step`: a v2 trace without the slim layout returns the last model step's full message list (empty when none). New test `test_older_v2_model_steps_resolve_the_whole_transcript_without_a_slim_layout`. |
| `preview_total` meant "rows after the cut" for scoring | minor | fixed | `slim.record(..., preview_total=)`; the scoring builder passes `len(s.ranked)`. `test_stage_records_...` asserts scoring `preview_total == 40 > len(preview)`. |
| Every tool call serialised the whole dispatcher state twice for the version digests | minor (perf, pre-P6) | fixed | `Dispatcher.identity()` (resolution, bindings, stages, searches, pool reference, row count, geo records without per-listing exits, `(criterion_id, evidence_id)` pairs, result count, returned-item scores, order, selection) feeds input/output_version; `snapshot()` stays for tests/debugging. Synthetic 21,431-listing scripted run: 1.70 / 1.62 s → 1.33 / 1.18 s (digests stubbed: 1.18 / 1.15 s). `test_geo_tools` asserts a query tool leaves the version unchanged and a stage tool changes it. |

#### Test assertions changed on purpose (P6 review round)

- `test_trace_v2.py::test_debug_rows_never_exceed_debug_max_rows` → `..._and_keep_every_kind_of_row`: the
  assertion that a small budget holds only tie members (`top_scores.rows == []`, `exploration.candidates == []`)
  encoded the old order; replaced by the shared-budget rule (intended rule change). The total cap is still asserted.
- `test_trace_v2.py::test_trace_size_is_bounded_under_repeated_tool_calls_and_does_not_scale_with_candidates` →
  `..._capped_by_model_result_candidates`: pool 40 added; the stored truncation length is min(pool, cap) instead of
  the constant 100. The 400 vs 4,000 assertions are unchanged.
- `test_geo_tools.py::test_geographic_evidence_result_is_compact_for_the_model`: docstring/comment only (the
  returned listings' exits are now recorded by the final gate); its assertions are unchanged.
- Additions only: `test_stage_records_...` (scoring preview_total), transcript step 4 (weights), step-mode
  test (persisted), `test_geo_tools` stage-order test (tool versions).

#### Generic gates after the P6 review round

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **438 passed** (434 + 4 new tests); also 438 passed with `RECOMMENDATION_TRACE_MODE=debug` exported |
| `python -m recommendation.schemas --check` | exit 0 (contracts unchanged) |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 207 tests: **187 passed, 0 failed**, 20 skipped |
| `python -m recommendation.examples.check_export` | runs; `gate: true` |
| `p6/terminal_transcript.py fixture` / `database` | exit 0 / exit 0 (83,164 / 134,009 bytes, regenerated; database run read-only with a temporary state) |
| `scripts/import-recommendation-upstream.py --check` | fails as expected (`Upstream port fingerprint changed`, P7); review-round manifest files listed in INVENTORY section M |
| live state `data/.runtime/recommendation/state.sqlite` | md5 `247b2503ac952c17f8676443f5f8355a` (unchanged); services on 8010/8091/8092 untouched |

New failures introduced by the P6 review round: none, apart from the expected upstream fingerprint check.

## P7

### P7 part 1: calibration + evaluation (Step 10 analyses, calibrated settings, trace.max_string_chars)

Scope: new offline package `recommendation/analysis/` (`stats.py`, `scenarios.py`, `live.py`,
`calibrate.py`, `simulate.py`); calibrated values in `recommendation/settings/recommendation.yaml`
(`numeric_scaling` iqr → p05_p95, `tie_max_score_span` 0.02 → 0.01, labels confirmed /
existing-default / experiment-result; ten values that P7 could not calibrate were first labelled
"provisional-kept" and are `provisional` since the P7 review round); new key `trace.max_string_chars`
replacing `tracing/slim.py` TEXT_LIMIT (loader + slim); `recommendation/examples/config.json`
synced; regenerated examples (`recommendation/examples/contracts/*.json` via
`python -m recommendation.examples.generate_contract_examples`; only hashes / scoring_version
changed) and `tests/fixtures/recommendation-v2/ten-results.json` (scratchpad `gen_ten_results.py`;
new seeds draw fixture-02 instead of fixture-04 in one exploration slot). Deliverables:
TUNING.md, CALIBRATION.md, PERFORMANCE.md, raw output under `p7/`. Not in part 1: patch
registration + upstream `--check`, live smoke run, live checklist, MIGRATION.md, docs (P7 part 2).

#### State before P7 part 1 (pre-existing failures)

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | 438 passed |
| `scripts/import-recommendation-upstream.py --check` | fails (`Upstream port fingerprint changed`), pre-existing since P2, expected until P7 part 2 registers the patches |

No other gate was failing.

#### P7 part 1 validation items

| Item (spec section 13, P7) | Proof | Outcome |
|---|---|---|
| Calibration analysis script (new) on the live DB, read-only | `python -m recommendation.analysis.calibrate --out evaluation/recommendation-exploration-2026-10-23/p7/calibration.json` (3 min 27 s; PostgreSQL READ ONLY REPEATABLE READ + Neo4j READ sessions through the production repositories; temporary state only); live state md5 `247b2503…` unchanged; YAML bytes unchanged by the analysis (`test_calibration_covers_every_p7_analysis_and_labels_its_data_basis` asserts it in fixture mode) | pass |
| Pool sizes for representative hard-criteria scenarios (≥ 6) | 9 scenarios (none, budget, budget+bedrooms, budget+bedrooms+district, property type, MRT-category distance, named station, URA region, hospital distance): pools 121–21,431, partition invariant per scenario (CALIBRATION.md §1; test asserts invariant + scenario kinds) | pass |
| Price quantiles, duplicates, outliers | CALIBRATION.md §2 (840 distinct prices, 98.6 % of rows share a price, 1,076 above Tukey far fence, max 128 M); `test_quantiles_duplicates_outliers_and_bin_balance` | pass |
| Attribute missingness | full table + per pool (floor area 39 %, coordinates 60 %; §1–§2, §4) | pass |
| Soft score distributions | `scores` section, 54 cases (§8 table: soft p0/p50/p100, exact top ties) | pass |
| Top score spread and exact / near tie counts | `tie_profile` per case (span top-1..K, gap, exact ties at s_K, B per epsilon; §8); `test_tie_profile_matches_the_select_v1_cutoff_rule` | pass |
| Browsing weight curves for candidate time scales | §6 (scales 5–120 s; design, no dwell data); `test_browsing_curves_keep_increasing_beyond_120_seconds` | pass (curves only; the time scale is not calibrated: `provisional`, pending decision) |
| Numeric vs categorical contribution to similarity | §5 drive shares per pool + leave-one-feature-out top-10 overlap (synthetic references) | pass |
| Normalisation schemes (std / iqr / p05_p95) compared | §3: spreads, saturation, outlier sensitivity, zero-variance pools, rank agreement → p05_p95 | pass (value changed) |
| quantile_bins and target_sample_size (incl. 30) by bin balance / coverage | §7: 7 × 5 grid × 36 cases × 100 seeds (effective bins, R bin balance, share of bins displayed, price span, score loss) | pass (5 / 30 kept) |
| Epsilon vs top-set stability (S distribution) and B | §8: S per perturbation fraction, B and exit decision per epsilon, pair order-flip rate by score gap → epsilon 0.01 | pass (value changed) |
| Stability trials / fraction convergence (variance of S vs trials) | §8: 30 seeds × {5, 10, 20, 40, 80} trials vs 1000-trial reference in both runs; S vs f ∈ {0.05, 0.1, 0.2, 0.3} | pass (20 kept; f is not calibrated: `provisional`, pending decision) |
| Resolution budgets (scripted / fake-model tool counts) | §10: real-catalog ambiguity, narrowing policy against the production search, real runs (scripted 4 calls; offline fake model 6–7 calls, 2–3 resolution calls, 5–6 model calls; non-narrowable → needs_clarification) | pass (values kept) |
| Trace preview / debug limits (sizes) | §11: stored trace bytes per preview_rows and debug_max_rows on live runs; `test_trace_sizes_grow_with_the_limits_and_strings_stay_below_max_string_chars` | pass (values kept) |
| Cache TTL need | §9: data_version checked on every lookup + final gate (P3 tests), fingerprint P50 142 ms → ttl 0 kept. No separate TTL experiment was run (design argument + one timing); the P7 review relabelled the value `confirmed` (spec section 2 rule) instead of experiment-result | pass (design + timing; no TTL experiment) |
| Cache vs no-cache timing | PERFORMANCE.md (per stage and end-to-end P50/P95, rows transferred) | pass |
| Provisional YAML values replaced, each with source in a provenance table | TUNING.md (one row per key; value, source, evidence); `test_tuning_provenance_table_matches_the_committed_yaml`, `test_yaml_source_tags_agree_with_tuning_md`. Replaced where P7 evidence exists (experiment-result / confirmed). **Ten values are not calibrated** and keep the spec R2 label `provisional`: the five similarity feature weights, `ranking.browsing.time_scale_seconds`, `cold_start.stability.weight_perturbation_fraction` and the three cache bounds, so user decision 2 (calibrated price weight) is not met yet and epsilon rests on the uncalibrated f. Recorded as a pending user decision (OPEN_ITEMS "Final state (P7)"); `test_provisional_settings_are_listed_as_pending_decisions` keeps the list in step and fails if this row says pass while any remains. (First reported pass under a self-introduced label "provisional-kept"; corrected in the P7 review round.) | partial |
| Simulated data reported separately from real data; no user-satisfaction claim; no claim that price sampling cuts scoring cost | every analysis section of the JSON carries `basis` (the recorded calibration.json lacked it on `cache`; fixed in code and tested in the P7 review round); CALIBRATION.md method, PERFORMANCE.md opening statement; tests assert the simulated basis label and that the calibration report never mentions satisfaction | pass |
| Evaluation: no cache vs pool cache vs cache + cold-start sampling, P50/P95 per stage + end-to-end | `python -m recommendation.analysis.simulate --sessions 6 --rounds 5 --out …/p7/evaluation.json` (6 min 23 s; 4 scenarios × 6 sessions × 5 rounds × 3 modes, interleaved); PERFORMANCE.md tables; `test_simulated_evaluation_three_modes_and_correctness` (fixture) | pass |
| Pool coverage over N rounds / sessions | PERFORMANCE.md (coverage and distinct shown / possible) | pass |
| Exposure Gini including zero-exposure listings, computed in an external script over simulated rounds | `analysis/simulate.py` (whole pool incl. never-shown); `test_gini_includes_zero_exposure_listings`; `test_runtime_modules_never_import_the_analysis_package` (no exposure history in the subagent) | pass |
| Score loss vs deterministic ranking | relative + absolute loss vs the snapshot head top K (PERFORMANCE.md: mean ≤ 1.8 %, P95 ≤ 4.9 %) | pass |
| Output correctness (no duplicates, all in pool, hard criteria pass, replay identical) | 7 checks × 120 requests × 3 modes all pass; no_cache == pool_cache selections in all 4 scenarios | pass |
| TEXT_LIMIT moved into trace config; affected examples / fixtures regenerated | `trace.max_string_chars` (required, ≥ 1, config_hash only); `test_max_string_chars_is_a_required_trace_setting_and_cuts_tool_strings`; examples + ten-results regenerated; `test_ten_results_fixture_is_valid_and_reproduced` and the TS fixture tests pass | pass |
| Tests do not depend on calibrated values (fixture configs) | transcript test pinned to `recommendation/tests/fixtures/settings-pre-p7-provisional.json`; zero-variance test passes an explicit iqr setting | pass |
| Register modified manifest files, upstream `--check` | P7 part 2 | not_done (part 2) |
| Final full test run + live smoke run against the running services | P7 part 2 | not_done (part 2) |

#### Test assertions changed on purpose (P7 part 1)

- `test_settings.py::SOURCE_TAG` (used by `test_every_yaml_value_is_annotated_with_its_source`): the
  allowed labels change from confirmed / existing-default / provisional to confirmed /
  existing-default / experiment-result / provisional-kept (intended relabelling; still requires a
  label on every value).
- `test_settings.py::test_no_duplicate_hard_coded_parameters_outside_settings`: the offline
  `recommendation/analysis/` package is exempt like the tests. Its experiment grids (0.2, 3000, …)
  are analysis inputs, not runtime defaults, and a new test proves the runtime never imports it.
  Runtime coverage is unchanged.
- `test_scoring_v2.py::test_zero_variance_feature_is_dropped_without_division_by_zero`: the
  "mostly equal values have iqr 0" assertion now passes an explicit iqr fixture setting (the
  committed scaling is p05_p95). The intent is unchanged.
- `test_terminal_debug.py::test_terminal_transcript_covers_the_p6_flows`: the terminal is started
  with the fixture settings file holding the pre-P7 values (epsilon 0.02, iqr), because its
  tie / draw assertions were written for them. No assertion was edited.
- Additions only: `recommendation/tests/test_analysis_p7.py` (14 tests).

#### Generic gates after P7 part 1

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **452 passed** (438 + 14 new); also 452 passed with `RECOMMENDATION_TRACE_MODE=debug` |
| `python -m recommendation.schemas --check` | exit 0 |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 207 tests: **187 passed, 0 failed**, 20 skipped |
| `python -m recommendation.examples.check_export` | runs; `gate: true` |
| `scripts/import-recommendation-upstream.py --check` | fails as before (`Upstream port fingerprint changed`), pre-existing, P7 part 2. Manifest files touched in part 1: `recommendation/examples/config.json`, `recommendation/examples/contracts/{success,error,no-match,clarification}.json` (config_hash / scoring_version) and `{feedback-only-update,preference-update}.json` (regenerated previous_state_id UUIDs only); `initial-search.json` byte-identical |
| live state `data/.runtime/recommendation/state.sqlite` | md5 `247b2503ac952c17f8676443f5f8355a` (unchanged); services on 8010/8091/8092 untouched |

New failures introduced by P7 part 1: none.

### P7 part 2: delivery (docs, live oracles, INVENTORY, patch registration, final verification)

Scope: MIGRATION.md (new); docs rewritten (`README.md`, `recommendation/README.md`,
`recommendation/contracts/README.md`, `docs/Recommendation_Agent_原生接入说明.md`, `contracts/releases/README.md`,
`skills/evidence-based-explanation/SKILL.md` score wording, the B-agent instruction text in `src/agents/profiles.ts`,
the stale migration-status comment in `recommendation/settings/recommendation.yaml`: comments only, config_hash
unchanged `e46e754a9c6e…`); OPEN_ITEMS.md final section; live oracles rewritten on a shared helper
(`tests/helpers/select-v1-oracle.ts`); INVENTORY final grep (`p7/inventory_check.sh`); every changed manifest file
registered (`p7/register_downstream_patches.py`, run last); `scripts/compare-recommendation-upstream.py` retired; live
smoke on a separate service from this tree (`p7/serve_smoke.py`, `p7/live_smoke.py`, `p7/live_oracle_check.ts`).

#### State before P7 part 2 (pre-existing failures)

| Gate | Result |
|---|---|
| `scripts/import-recommendation-upstream.py --check` | fails (`Upstream port fingerprint changed`), pre-existing since P2 |

All other gates were green (452 pytest, npm 187 passed / 0 failed / 20 skipped).

#### P7 part 2 validation items

| Item (spec section 13, P7 delivery) | Proof | Outcome |
|---|---|---|
| MIGRATION.md: contract 1.0 → 2.0 field by field, feedback snapshot semantics, sidecar cases, state DB v2 tables + legacy handling, evidence per (evidence_id, criterion_id), config, removed native `unverified_candidates`, V1 kept | `evaluation/.../MIGRATION.md`; `test_migration_doc_covers_every_contract_2_field_table_and_sidecar_case` (every field of the 2.0 request / feedback / response / summary / item / score / contribution models, every v2 `CREATE TABLE`, the legacy tables, the six sidecar cases, removed names, config / migration / provenance topics) | pass |
| Docs rewritten (README "最多4条" etc., recommendation/README, 原生接入说明, contracts/releases/README, skills text) | `test_delivery_docs_have_no_contract_1_limits_or_scoring` (4 docs: no weighted-v1, 最多4, explicit/implicit weights, `"result_limit":4`, 0.8 × explicit, feedback_version); `test_native_integration_doc_keeps_weighted_v1_only_in_the_labelled_history` (weighted-v1 figures only in the 2026-10-20 history table) | pass |
| OPEN_ITEMS.md final (unverified items, pending decisions incl. destination_id + destination_name and the explicit-station rule; known effects: no soft criteria → no exit, single-reference normalisation, 40 % coordinates, live trace growth, incident rows) | OPEN_ITEMS.md "Final state (P7)"; `test_open_items_final_section_lists_pending_decisions_and_known_effects` | pass |
| Live oracles rewritten to select-v1 (`tests/native-recommendation-live.test.ts`, `tests/advanced-live.test.ts`, `scripts/test_native_recommendation_ui.py`, `evaluation/followup_workflow_browser.py`; also `tests/intake-live.test.ts`, found by the final grep) | shared oracle `tests/helpers/select-v1-oracle.ts`; offline `tests/select-v1-oracle.test.ts` (5 tests): accepts the committed ten-results envelope against O computed from `recommendation/examples/listings.json`, rejects 8 kinds of violation, covers ranked / near_tie_sampling / small_pool / empty, and reproduces the native scores of the new `tests/fixtures/recommendation-v2/single-reference.json` (generator `p7/gen_single_reference_fixture.py`) within 1e-9 (self pair excluded, duration cancels, p05_p95 spread, district fallback) | pass (rewrite + offline check) |
| Rewritten native-live oracle on live data | `p7/live_oracle_check.ts` against the 8192 service (scripted, database): round 1 pool 161, all scores 0, cold start `no_soft_requirements`, 0 violations; round 2 one 90 s browsing reference, pool reused, fused-v2 oracle max \|native − oracle\| = 2.8e-17, 0 violations; round 3 budget 4500 → new pool_key, built, pool 129, 0 violations (`p7/live-oracle-check.json`) | pass |
| `tests/native-geography-live.test.ts` (select-v1 + fused-v2 score oracle) | env only, no code change: `NATIVE_GEO_LIVE=1 RECOMMENDATION_API_URL=http://127.0.0.1:8192 RECOMMENDATION_SERVICE_TOKEN=<smoke token> node --env-file=.env --import tsx --test tests/native-geography-live.test.ts` → 1 passed (180 eligible). Its report went to `p7/native-geographic-oracle-8192.json`; the historical `evaluation/reports/native-geographic-oracle.json` was restored with `git checkout` | pass |
| `tests/native-recommendation-live.test.ts`, `advanced-live`, `intake-live`, `business-live`, `full-data-live`, `location-subject-live`, browser scripts | need the app stack on 8010 running this tree; 8010 / 8092 run older code and were not restarted (no user OK) | not_done (OPEN_ITEMS "Final state (P7)") |
| Final INVENTORY grep: every entry resolved or explicitly retired | `bash evaluation/.../p7/inventory_check.sh` (tracked + untracked) → `p7/inventory-grep.txt`; every hit classified in INVENTORY section F (one new finding, `tests/intake-live.test.ts:34`, fixed); no `open-P` entry left; `test_inventory_has_no_open_entry_and_records_the_final_grep` | pass |
| Every modified manifest file registered (target, base_ported_sha256 from the manifest, patched_sha256 of final LF bytes, issue, changes), hashes computed last | `p7/register_downstream_patches.py` (fails on undescribed / unchanged / CRLF targets) → 39 entries (the DEF11 controller entry kept and extended); `test_every_changed_manifest_file_is_declared_with_base_patched_issue_and_changes` | pass |
| `scripts/import-recommendation-upstream.py --check` passes without editing the manifest or weakening the script | exit 0: `{"mode": "manifest-check", "files": 71, "business_logic_changes": 39, …}`; manifest bytes identical to the pre-run copy (`cmp`); script unchanged; `test_upstream_check_passes_without_touching_the_manifest`; `tests/test_recommendation_provenance.py` (4 enforcement cases) still pass | pass |
| `scripts/compare-recommendation-upstream.py` retired as 907cfa8-baseline-only evidence (documented, not regenerated) | default run exits 2 with a retirement notice; a historical re-run needs `--legacy-root` + `--output` and may never overwrite `evaluation/reports/native-upstream-parity.json`; `test_compare_upstream_script_is_retired`; MIGRATION.md section 10, contracts/releases/README.md, 原生接入说明 | pass |
| Live smoke (i): scripted controller, database mode, 3 rounds (initial, feedback re-rank with a snapshot, soft change cache hit) on a separate service | `p7/live_smoke.py` (8192, temporary state, own token, stopped afterwards) → `p7/live-smoke.json`: r1 pool 1,239 of 21,431 (19,664 fail, 528 unknown) built, cold_start_exploration, client 1,411.5 ms / service 1,101.8 ms (pool build 914 ms, final gate 176 ms); r2 snapshot (90 s / 0 s / missing + one MANUAL_SENT appointment) pool **reused**, behaviour scores present, exit conditions met → `ranked` (L 9), 347.6 / 325.4 ms; r3 soft change (version 2) pool **reused** (same pool_key), cold_start_exploration (L 5, G 12), 375.8 / 325.6 ms; replay of r2 via /app/recommend and /recommend identical (11 ms). Correctness per run: no duplicates, all IDs in the stored pool content, display order, hard criteria pass | pass |
| Live smoke (ii): at most 3 live-LLM runs (LiveController, .env model) | same script, `openai:gpt-4.1-mini`: L1 soft "Clementi" → `URA:AREA:CLEMENTI`, 5 model / 4 tool calls, 32,300 tokens, 11.39 s; L2 feedback re-rank, pool reused, 5 / 4, 35,079 tokens, 8.81 s; L3 ambiguous "Lee Kong Chian School of Business" (2 exact matches): `search_places` used, first answer rejected by the final gate and repaired, no bind (criterion stays ambiguous, soft → unknown), 7 / 6, 44,759 tokens, 12.21 s. All success with correct outputs; live traces 135–154 KB | pass (bind with a real LLM not observed; OPEN_ITEMS) |
| Recorded smoke evidence is consistent | `test_recorded_live_smoke_used_a_separate_service_and_met_its_expectations` | pass |
| Live state untouched; running services untouched | `data/.runtime/recommendation/state.sqlite` md5 `247b2503ac952c17f8676443f5f8355a` before and after; 8010 / 8091 / 8092 never restarted; 8192 stopped after each run | pass |

Smoke incident (recorded): the first scripted-only attempt read the trace by request_id and got the REPLAY
trace of r2 (a replay stores its own trace under the same request_id). `live_smoke.py` now reads each run's own trace
by `execution.trace_id`; the scripted part was re-run together with the live part (the live part ran once, 3 LLM runs).

#### Test assertions changed on purpose (P7 part 2)

- `tests/native-recommendation-live.test.ts`: exact top-10 prefixes, the weighted-v1 `implicit` formula, `ordering ==
  'behavioral'`, `feedback_version` and the `feedback: []` body replaced by the select-v1 / fused-v2 oracle and
  snapshot assertions (intended rule change; the test's intent — independent SQL eligibility and score oracle,
  feedback, replay, transport alignment — kept).
- `tests/native-geography-live.test.ts`: the inline select-v1 checks moved to the shared helper (same checks plus the
  tie-group bounds from the oracle's own scores).
- `tests/advanced-live.test.ts`, `tests/intake-live.test.ts`: the nearby / supermarket prefix oracles are mode-aware
  (native: select-v1 over active places / nearest exits; baseline: exact top-10, because the app requests 10 since P2).
- `scripts/test_native_recommendation_ui.py`, `evaluation/followup_workflow_browser.py`: hard-coded card counts (8, 5)
  replaced by counts read from the API state / the rendered list; the "样例浏览兴趣" label check replaced by the
  fused-score and selection-reason labels.
- Additions only: `tests/select-v1-oracle.test.ts` (5), `recommendation/tests/test_delivery_p7.py` (12).

#### Generic gates after P7 part 2 (final full run)

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **464 passed** (452 + 12 new); also 464 with `RECOMMENDATION_TRACE_MODE=debug` |
| `python -m recommendation.schemas --check` | exit 0 |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 212 tests: **192 passed, 0 failed**, 20 skipped (env-gated) |
| `scripts/import-recommendation-upstream.py --check` | **exit 0** (71 files, 39 declared downstream patches) |
| `node --import tsx scripts/validate-recommendation-examples.ts` | passed (4 request + 4 response examples, 187 schema properties documented, 30 JSON blocks) |
| `python -m recommendation.examples.check_export` / `python -m recommendation.examples.run` | `gate: true` / exit 0 |
| `node --env-file=.env --import tsx --test tests/feedback-sidecar-postgres.test.ts` / `tests/postgres-session.test.ts` (test database) | 1 passed / 2 passed |
| live smoke + native-geography-live + live oracle check (8192) | pass (above) |

New failures introduced by P7 part 2: none. Pre-existing failure resolved: the upstream `--check`.


### P7 review round (fixes)

Eight reviewer findings were checked against the code. The outcome tables above were corrected where an outcome
changed: the provisional-values item and plan Step 10 are now **partial**, and the TTL row is reworded.

| # | Finding | Verdict | Change |
|---|---|---|---|
| 1 (major) | "Provisional values replaced" was reported pass, but ten keys kept their R2 values under a self-introduced label `provisional-kept`; decision 2 (calibrated price weight) unmet | real | Fixed in the reporting, not in the values (no feedback exists to calibrate them). The ten keys use the spec R2 label `provisional` again (YAML, TUNING.md, CALIBRATION.md, recommendation/README.md); the item and Step 10 are **partial**; OPEN_ITEMS "Decisions pending for the user" lists every key, states that decision 2 is not met and that epsilon rests on f = 0.2; the new `test_provisional_settings_are_listed_as_pending_decisions` keeps the list equal to the provisional rows and fails while this item says pass with any provisional key left. Calibrating the values themselves is deferred to the user's decision |
| 2, 8 (minor, same defect) | advanced-live dropped the per-candidate evidence check in native mode and skipped null-distance candidates | real | Native mode now requires, for every shown candidate, place_proximity evidence with `route_verified === false` and `raw_distance_type 'straight_line'` + `measurement_method 'coordinate_haversine'` (known distance) or `'unavailable'` (no coordinates); a null oracle distance requires a null observed value (both modes); the baseline geodesic check is unchanged. Typecheck only (live test needs 8010) |
| 3 (minor) | `cache.ttl_seconds` labelled experiment-result without a TTL experiment; `max_model_turns` 8 kept against a measured worst case of 9 | real | ttl relabelled `confirmed` (spec section 2 rule: no TTL, invalidated by pool_key / data_version; the `confirmed` definition now covers an explicit spec rule, as already used for `duration_cap_seconds`). `max_model_turns` stays 8 and is listed under "Decisions pending for the user" (raising it changes config_hash and the regenerated examples) |
| 4 (minor) | The exposure / evaluation driver lives in `recommendation/analysis/` instead of an external script | real (disclosed deviation) | Deferred: listed as a pending user decision (keep vs move); the runtime import guard stays |
| 5 (minor) | calibration `cache` section has no `basis`; `\b4000\b` not guarded | real | `cache_facts` emits `basis` (real timing + design TTL argument); the fixture test checks every analysis section's basis; `OLD_LITERALS` gains `\b4000\b` (the slim.py docstring no longer spells the old value). The recorded `p7/calibration.json` was not re-run for the label; CALIBRATION.md says so |
| 6 (major) | `scripts/test_native_recommendation_ui.py` expected `原生融合匹配分` on a session whose last round scores every item 0 (`stable_listing_id`) | real (confirmed from tests/native-recommendation-live.test.ts: last round is a hard budget change, every browsing record missing, all scores 0) | The card and explanation expectations now depend on `native.scores[first].ordering`: `stable_listing_id` → `按原生房源ID顺序返回` on the card and `原生房源ID｜房源ID字符串` in the explanation (the texts of frontend/native-ranking.js and src/domain/explanation.ts); otherwise `原生融合匹配分`. The same rule is checked on the first card after the sample re-rank. Compiles; not run (browser journey on 8010) |
| 7 (minor) | inspect-listing on a run stored before P7 fails: stored configuration lacks the required `trace.max_string_chars` | real | `terminal/debug.py` `stored_configuration` fills only missing `trace.*` keys (never result-affecting, outside selection_hash) from the current settings and prints a note naming them; result-affecting keys are never filled. New test `test_inspection_recomputes_a_run_stored_before_trace_max_string_chars` (pre-P7 snapshot without the key → recompute labelled, note printed, score_rank equal; a missing selection key still fails) |

#### Test assertions changed on purpose (P7 review round)

- `test_settings.py::SOURCE_TAG` and `test_analysis_p7.py::SOURCES` / the YAML tag regex: `provisional-kept` → the spec
  R2 label `provisional` (the label set is the three P7 sources plus `provisional` for uncalibrated values).
- `test_analysis_p7.py::test_tuning_provenance_table_matches_the_committed_yaml`: the "provisional-kept rows state why
  they were kept" check becomes "provisional rows say not calibrated"; `test_yaml_source_tags_agree_with_tuning_md`:
  "no bare provisional label" becomes "no provisional-kept label". Both are stricter in combination with the new
  pending-decision test.
- `tests/advanced-live.test.ts`, `scripts/test_native_recommendation_ui.py`: see findings 2/8 and 6 (native values
  updated, intent kept).
- Additions: `test_provisional_settings_are_listed_as_pending_decisions`,
  `test_inspection_recomputes_a_run_stored_before_trace_max_string_chars`, the per-section basis assertion in
  `test_calibration_covers_every_p7_analysis_and_labels_its_data_basis`, `\b4000\b` in `OLD_LITERALS`.

#### Generic gates after the P7 review round

| Gate | Result |
|---|---|
| `pytest recommendation/tests tests/test_native_transport.py tests/test_recommendation_provenance.py -q` | **466 passed** (464 + 2 new); also 466 with `RECOMMENDATION_TRACE_MODE=debug` |
| `python -m recommendation.schemas --check` | exit 0 |
| `npm run -s typecheck` / `npm run -s typecheck:recommendation` | exit 0 / exit 0 |
| `npm test` | 212 tests: **192 passed, 0 failed**, 20 skipped |
| `scripts/import-recommendation-upstream.py --check` | exit 0 (71 files, 39 declared patches) after re-running `p7/register_downstream_patches.py` for the `recommendation/README.md` wording change; manifest bytes identical (`cmp`) |
| config identity | comments only in recommendation.yaml: `config_hash` / `selection_hash` unchanged (`e46e754a…` / `90e724bb…`) |
| live state `data/.runtime/recommendation/state.sqlite` | md5 `247b2503ac952c17f8676443f5f8355a` (unchanged); services on 8010/8091/8092 untouched |

New failures introduced by the P7 review round: none.

## Final summary (plan Steps 1–10)

| Step | Phase | Status | Evidence / remainder |
|---|---|---|---|
| 1 Baseline + single config | P1 | pass | baseline fixtures, INVENTORY (final grep clean, section F), `recommendation/settings/recommendation.yaml` + loader validation tests; baseline failures recorded separately |
| 2 Three-layer contracts 2.0 | P2 | pass | Python + generated schemas + strict TS mirror on shared fixtures; adapter round trip; `schemas --check`; MIGRATION.md |
| 3 Orchestrator snapshot + run consistency | P2 | pass (offline + PostgreSQL) | sidecar cases on real rows (`feedback-sidecar-postgres`), three-round no-accumulation, replay / conflict / stale checks; the live app round trip on 8010 is not_done (below) |
| 4 Pool cache + gates | P3 | pass | hit / miss / isolation / data-change tests; live read reduction (P3, PERFORMANCE.md); P7 smoke: feedback and soft changes reuse the pool, a hard change rebuilds it |
| 5 Entity resolution + geography tools | P4 | pass | resolver / distance / tool tests, shared TS fixtures, live geography; P7 live LLM runs resolved an area and kept an ambiguous name ambiguous (no first pick) |
| 6 Full-pool scoring + similarity | P5 | pass | fused-v2 tests, timing test; independent TS oracle reproduces native scores offline (1e-9) and live (2.8e-17) |
| 7 Cold start + price exploration | P5 | pass | C/B/S boundary, quota and exploration tests; live: exit met once (scripted r2), otherwise exploration |
| 8 Final selection + idempotent replay | P5 | pass | select-v1 / replay_gate tests, ≥200-seed membership test; live replay identical; live oracles rewritten (select-v1) |
| 9 Trace + terminal debugging | P6 | pass | slim trace tests, terminal transcripts (TERMINAL.md); live trace sizes in the P7 smoke |
| 10 Analysis, regression, delivery | P7 | **partial** | CALIBRATION / TUNING / PERFORMANCE (part 1); MIGRATION, docs, OPEN_ITEMS, patch registration + upstream `--check`, live smoke (part 2). Ten settings are not calibrated (`provisional`: feature weights incl. price = decision 2, browsing time scale, perturbation fraction, cache bounds) pending real feedback / a user decision (OPEN_ITEMS) |
| Live app-stack journeys (8010: native-recommendation-live, business/advanced/intake/full-data/location-subject live tests, browser scripts) | P2–P7 | **not_done** | need 8010 / 8092 restarted on this tree (user OK); the native side of these oracles ran against 8192 instead (OPEN_ITEMS "Final state (P7)") |

## Merge with main c7dc536 (2026-10-23)

Decisions applied (user): request = main's contract and md (`contract_version` 1.0, `feedback` events,
`behavior_context`); kept from the recommendation: 10 results, response contract 2.0, orchestrator-managed
`cold_start`. Details: MIGRATION.md section 0.

| Item | Proof | Outcome |
|---|---|---|
| 22 conflicted files resolved; no conflict markers | `git grep -n '^<<<<<<< '` empty; `git diff --check` | pass |
| Migration numbering collision (ours 004 vs main 004–006) | ours dropped; `migrations/007-recommendation-cold-start.sql` registered after main's 004–006 in `DomainStore.setup` | pass (not applied to the app DB) |
| Request = main shape + `cold_start`, result_limit ≤ 10; 2.0 request bodies rejected | `test_contract_v2_fixtures.py::test_contract_cases_schema` (28 cases, also parsed by the TS mirror in `tests/recommendation-v2-fixtures.test.ts`), `test_unversioned_body_is_read_as_main_contract_but_2_0_request_is_rejected`, `tests/test_native_transport.py` | pass |
| behavior_context scored: cumulative browsing (interest_eligible, > 0 s, in pool), viewing drafts (one per listing, in pool); feedback events validated, not scored | `test_contract_cases_at_service_level` (12 valid cases), `test_scoring_v2.py::test_missing_attributes_are_not_compared_and_weights_renormalise`, `::test_resaved_viewing_draft_adds_nothing`, `::test_only_the_request_behavior_context_counts`, `tests/test_browser_interest_contract.py` | pass |
| Three rounds: no native accumulation, appointments only via input, same request/different behaviour → 409 | `test_three_rounds_no_loss_no_accumulation_appointments_only_via_input`, `tests/test_behavior_context_transport.py` | pass |
| TS: nativeRequest = main mapping (sample / web_detail events, behavior_context forwarded) + Math.min(10) + cold_start; pure; projection 2.0 | `tests/native-recommendation.test.ts`, `tests/recommendation-v2-fixtures.test.ts` (flow, ten-results, contract cases) | pass |
| main readers of old score names (behavior.ts, explanation evidence, native-ranking.js) | `npm run typecheck`; `tests/explanation-*.test.ts` in `npm test` | pass |
| main's app output protocol on the new tool registry | `tests/test_recommendation_output_protocol.py` (13) | pass |
| config/recommendation-full.json as overlay (300 s only) | `tests/test_recommendation_runtime_budget.py` | pass |
| V1 exports + field index (main had not indexed behavior_context / distance_preference) | `node --import tsx scripts/validate-recommendation-examples.ts` (209 properties documented) | pass |
| Upstream provenance | `scripts/import-recommendation-upstream.py --check`: 71 files, 39 declared patches (re-registered after the last manifest edit) | pass |

Gates after the merge: `pytest recommendation/tests tests -q` 496 passed; `python -m recommendation.schemas --check`
exit 0; `npm run -s typecheck` and `npm run -s typecheck:recommendation` exit 0; `npm test` 287 passed, 0 failed,
22 skipped (env-gated live tests). Not run: env-gated live/browser tests (need the app stack on this tree; see
OPEN_ITEMS.md "Merge with main c7dc536").

Assertions changed on purpose (intended rule changes of the merge, intent kept): previous-round-only browsing →
session-cumulative (`test_earlier_round_browsing_has_no_effect` → `test_only_the_request_behavior_context_counts`);
appointment status upgrade → re-saved viewing draft (`test_appointment_status_upgrade_adds_nothing` →
`test_resaved_viewing_draft_adds_nothing`); main's `test_real_feedback_is_persistent_deduplicated_and_identified`
(feedback events scored and accumulated natively) → `test_real_feedback_is_validated_labelled_and_behavior_context_is_scored`;
main's byte-preserved kernels → kernels change only through declared patches; main's behavior-transport test: native
`/recommend` now accepts behavior_context (was 422) and the native request keeps it (was stripped); main's
output-protocol tests use the v2 pipeline tools and an empty hard budget (native now resolves URA areas).
