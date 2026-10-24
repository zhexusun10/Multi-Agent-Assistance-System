# Recommendation v2: open items

Items that are unverified, interim or waiting for a later phase. Each one is closed (with the
command and result) in VALIDATION.md of the phase that resolves it.

## Cold-start detector mpd-v1 (2026-10-24)

- **Decision** (user): cold start is active when D, the mean pairwise difference of the top 30 official combined
  scores, is below `cold_start.detector.min_mean_pairwise_difference` 0.01. 0.01 is a starting value, **not
  calibrated**: how often real pools fall below it has not been measured offline or live (pending decision below).
- The remembered exit no longer bypasses the check; the request field is still accepted; migration 007's column and
  data are kept, with no new writes. Stored legacy C/B/S responses are accepted as stored.
- New-request seeds change (cold_start is in `selection_hash`); stored runs replay unchanged from `selection_input`.
- The cold-start figures in CALIBRATION.md §8, PERFORMANCE.md and `p7/*.json` describe the superseded C/B/S detector
  and were not re-run. The real-browser refinement run of 2026-10-24 (docs) was made under C/B/S and was not re-run.
- **Known effect**: while D stays below the threshold, a requirement card can be offered in later rounds until the
  applicable questions run out (or the user dismisses cards for the session). D does not prove that the
  requirements are sufficient or that the recommendations are correct.
- **Not run** (AGENTS.md cost rule): paid model / live tests (`native-recommendation-live`, `check_refinement_real`).

## Merge with main c7dc536 (2026-10-23)

- **Not run** (need the full app stack on this tree): main's browser/behaviour live scripts
  (`scripts/test_browser_interest_ui.py`, `scripts/test_session_behavior_ui.py`, …), `tests/browser-interest-live.test.ts`,
  `tests/native-recommendation-live.test.ts` (adapted to main's semantics: a sample duration is a validated, unscored
  event) and the other env-gated live tests. 8010/8091/8092 were not restarted.
- **Not applied to the app database**: migrations 004–007 run at the next graph-service start (`DomainStore.setup`).
- **Known effect**: browsing is session-cumulative (main contract) instead of previous-round only; a listing shown in
  several rounds carries all its time. Only `interest_eligible` summaries count, so browsing contributes nothing until
  main's gate (2 listings ≥ 15 s, 60 s total) opens.
- **Known effect**: viewing intent = saved timed VIEWING draft (`viewing_draft_saved`), not a sent/confirmed request;
  MANUAL_SENT/MANUAL_REPLIED/CONFIRMED statuses are no longer an input.
- **Calibration**: `CALIBRATION.md` / `PERFORMANCE.md` and `p7/*.json` were produced with the pre-merge request shape;
  the simulator (`recommendation/analysis/simulate.py`) now emulates main's orchestrator but was not re-run.
- **Historical scripts** written for the pre-merge request shape and not updated: `p5/live_selection.py`,
  `p6/terminal_transcript.py`, `p7/live_smoke.py`, `p7/live_oracle_check.ts` (their recorded outputs stay as evidence).

## Final state (P7)

Status of everything that is not a plain pass at delivery. VALIDATION.md "P7 part 2" has the commands and outcomes.

### Not run (not_done), with the reason

| Test / script | Status | Reason, and what ran instead |
|---|---|---|
| `tests/native-recommendation-live.test.ts` (`NATIVE_LIVE=1`) | not_done | It drives the full app on 8010 (session API, workflow, sidecar, evidence store), and 8010 / 8092 run older code that may not be restarted without the user's OK. Its rewritten select-v1 / fused-v2 oracle ran live against a separate native service from this tree on 8192 (`p7/live_oracle_check.ts`: 3 rounds, 0 violations, max \|native − oracle\| 2.8e-17). The app-side parts (sidecar from real workflow rows, explanation, follow-up, UI adoption) are covered only by the offline TS/PostgreSQL tests. |
| `tests/advanced-live.test.ts`, `tests/intake-live.test.ts`, `tests/business-live.test.ts` (`npm run test:live`, `BUSINESS_LIVE=1`) | not_done | Need the app stack on 8010 running this tree (`TEST_APP_URL` could point elsewhere, but no second app stack exists). advanced-live and intake-live oracles were rewritten mode-aware (select-v1 for native, top-10 for baseline). The other intake-live assertions (unverified counts) describe baseline semantics. |
| `tests/full-data-live.test.ts`, `tests/location-subject-live.test.ts` | not_done | Same reason (app on 8010). Limits were changed to 10 in P2 without running them. |
| `scripts/test_native_recommendation_ui.py`, `evaluation/followup_workflow_browser.py`, `scripts/test_full_data_ui.py`, `scripts/test_location_subject_ui.py` | not_done | Browser journeys against 8010. Card counts are now read from the API state / the rendered list, not hard-coded. |
| `tests/native-geography-live.test.ts` (`NATIVE_GEO_LIVE=1`) | **pass** (run in P7) | Pointed at the 8192 service by environment only (`RECOMMENDATION_API_URL`, `RECOMMENDATION_SERVICE_TOKEN`; no code change): 1 passed, 180 eligible, select-v1 oracle and fused-v2 score oracle held. The historical `evaluation/reports/native-geographic-oracle.json` was restored; the new output is `p7/native-geographic-oracle-8192.json`. |

### Unverified items

- End-to-end app + native on contract 2.0 (8010 → 8092) with a real browser and a real orchestrator round trip: not run (above).
- Nine settings are still `provisional` (not calibrated; spec R2 values): similarity feature weights, browsing `time_scale_seconds`, cache index / retention / memory bounds (the perturbation fraction stopped being a setting on 2026-10-24). They need real feedback or dwell data, so the P7 item "replace provisional values with calibrated ones" is **partial** (pending decision below); no user-satisfaction or relevance claim is made.
- `bind_entity` with a real LLM: in the P7 smoke run L3 (soft target "Lee Kong Chian School of Business", 2 exact matches) the model called `search_places` but did not bind, so the soft criterion stayed `ambiguous` (contributes unknown; C below threshold), which the rules allow. Its first final answer came before the pipeline had run; the final gate rejected it and the bounded repair completed the run (6 tool calls, 7 model calls, 44,759 tokens). The bind path itself is verified with the offline fake model (P4, CALIBRATION.md section 10).
- Replay under another CPython version: `selection_input.python_version` is recorded, not enforced (P5 note).

### Decisions pending for the user

| Item | Detail |
|---|---|
| Criterion may carry `destination_id` + `destination_name` (P4 review, contract change) | The TS adapter now sends an app target's `place_id` with its name. An ID and a name that natively resolve to different entities give `needs_clarification` (`id_name_conflict`) instead of silently using the ID. Option B (one target field, plan rule deferred) remains available by reverting the validator and the adapter line. |
| Explicit-station token rule follows TS (P4 review; the explicit-station rule) | Native uses the TS rule exactly (MRT/LRT/地铁站/地铁). Whether `station`, 站, 轻轨 and 捷运 should also mark an explicit station request needs a decision; if yes, change TS `isExplicitStation`, the Python port and `tests/fixtures/place-mention-rules.json` together. |
| Deployment of contract 2.0 | 8092 and 8010 must be restarted on this tree together (MIGRATION.md section 11); not done without the user's OK. |
| Incident rows in the live state DB | Test conversations `c1`, `v2-fixture`, `v2-other` written in P2 stay in `data/.runtime/recommendation/state.sqlite` (md5 unchanged since; never deleted). Removal is the user's decision. |
| Cold-start threshold (mpd-v1, 2026-10-24) | `cold_start.detector.min_mean_pairwise_difference` 0.01 is the user's starting value, not calibrated; how often D of the top 30 scores falls below it on real pools has not been measured. Adjust after real use. |
| Uncalibrated `provisional` settings (P7 review; spec P7 rule and user decision 2) | These keys keep their spec R2 / section 2 values because no real feedback / dwell data exists to calibrate them: `ranking.similarity.feature_weights.price`, `ranking.similarity.feature_weights.property_type`, `ranking.similarity.feature_weights.bedrooms`, `ranking.similarity.feature_weights.floor_area_sqft`, `ranking.similarity.feature_weights.geography`, `ranking.browsing.time_scale_seconds`, `cache.max_entries_per_conversation`, `cache.pool_retention_per_conversation`, `cache.memory_max_entries`. User decision 2 ("price is a behaviour-similarity feature; its weight is calibrated") is therefore **not met yet** (price stays 0.20), and `selection.tie_max_score_span` 0.01 was derived under the uncalibrated f = 0.2 of `python -m recommendation.analysis.calibrate --sections stability` (an analysis value in `recommendation/analysis/stability.py` since 2026-10-24, no longer a setting; re-derive when it changes). The user decides: (a) accept these values until real feedback exists (the P7 item then stays partial with this acceptance recorded), or (b) name a calibration target to fit them now (for example drive-share targets from CALIBRATION.md §5). `test_provisional_settings_are_listed_as_pending_decisions` keeps this list equal to the `provisional` rows of TUNING.md. |
| `controller.max_model_turns` 8 vs the measured sequential worst case 9 (P7 review) | CALIBRATION.md §10: two narrowable ambiguous criteria handled strictly one tool call per turn need 9 model turns; with parallel calls 6. 8 (existing-default) is kept; raising it (e.g. to 10, within the ceiling 16) changes config_hash (not selection_hash) and the regenerated examples. Only 9 of 3,260 catalog names are narrowable. |
| Location of the evaluation driver (P7 review) | Spec P7 asks for exposure Gini "in an external evaluation script over simulated rounds, not in the subagent". `recommendation/analysis/simulate.py` (and the exposure / coverage statistics in `analysis/stats.py`) ship inside the `recommendation/` package but are never imported by the runtime (`test_runtime_modules_never_import_the_analysis_package`); the subagent keeps no exposure history. Keep (user accepts), or move the driver under `evaluation/recommendation-exploration-2026-10-23/` or `scripts/` keeping the import guard. |
| Historical evidence manifest | `evaluation/acceptance-history/EVIDENCE_MANIFEST.json` (publication 2026-10-22) records `recommendation-downstream-patches.json` as byte-preserved at that publication; the file now declares this branch's patches. The historical record is not edited; a later publication should record the new disposition. |

### Known effects (documented, not defects)

- Under the superseded C/B/S detector (P7), no soft criteria → C = 0 → cold start **cannot be exited** (R9); the exit held in 7 of 18 mixed-soft calibration cases and in one live-smoke round. Since mpd-v1 (2026-10-24) every evaluated round follows D of the top 30 scores: with no soft criteria and no behaviour signal every score is equal, so D = 0 and cold start stays active; while D stays below 0.01 a requirement card can be offered in later rounds until the applicable questions run out. D measures score spread only, not whether the requirements are sufficient.
- Single-reference normalisation: with one usable browsing reference the browsing score is that pair's similarity whatever the duration (tested offline in `tests/select-v1-oracle.test.ts` against the committed `single-reference.json` fixture and live in `p7/live_oracle_check.ts`).
- Coordinates cover about 40 % of RENT listings (60 % missing): geographic hard criteria exclude the rest as `excluded_hard_unknown` (counted, warned), soft distances contribute unknown, and behaviour geography falls back to district equality.
- Live-mode trace growth: linear in model turns; about 35–57 KB per query call, bounded by `controller.max_tool_calls` × the capped per-call size (≈ 1.1 MB). Measured in the P7 smoke: 135–154 KB per live run (5–7 model steps), 72–89 KB per scripted run.
- When the whole pool ties, `selection_input.head` stores the whole pool (≈ 1 MB per run snapshot for 21,431 listings).
- 轻轨 is not stripped by `normalizePlace` (TS parity), so "…轻轨站" names stay unresolved.
- The P2 incident rows in the live state DB are kept untouched (test conversations only, removal is the user's decision); no P7 run wrote to that file (md5 `247b2503…` before and after).

## P7 part 1 (calibration + evaluation): resolved items, decisions and known effects

| Item | Resolution / effect |
|---|---|
| `TEXT_LIMIT` code constant (P6) | Resolved: moved to `trace.max_string_chars` (4000, existing-default) in recommendation.yaml; `tracing/slim.compact` takes it from the settings. Live tool steps never stored a string longer than 152 characters (CALIBRATION.md §11). Examples and the ten-results fixture were regenerated for the new config_hash. |
| Zero-variance drop under iqr (P5 part 1) | Resolved by calibration: iqr spread of bedrooms is 0 in 5 of 9 representative pools, so `numeric_scaling` is now `p05_p95`, which is never zero there (CALIBRATION.md §3). A pool whose 5th–95th percentile range is constant still drops the feature (intended rule). |
| Largest-remainder bins without a slot (P5 part 2) | Evaluated: at 5 quantile bins, 97–100 % of non-empty candidate bins get a displayed listing in tied pools, against 85–97 % at 10 bins; with distinguishable scores and target 30 about two thirds do (m = 7–10 items for 30 candidates). `quantile_bins` 5 kept; the rule is unchanged (CALIBRATION.md §7). |
| Provisional settings | **Partial.** Former `provisional` values with P7 evidence are now `experiment-result`; `cache.ttl_seconds` is `confirmed` (spec section 2 rule). Ten values stay `provisional` (not calibrated) because real feedback / dwell data is missing: feature weights, browsing time scale, perturbation fraction, cache memory/index/retention bounds (pending decision in "Final state (P7)"). The first P7 part 1 report labelled these a fourth source, "provisional-kept", and the item pass; the P7 review corrected both. |
| Epsilon depends on the perturbation fraction | `selection.tie_max_score_span` 0.01 comes from order-flip rates under f = 0.2 (provisional, not calibrated). Changing f should be followed by re-running `python -m recommendation.analysis.calibrate --sections stability`. |
| Cold start rarely ends without soft criteria or with large ties | With C = 0 (no soft criteria) it never ends (R9). With mixed soft criteria the exit test held in 7 of 18 calibration cases at epsilon 0.01 (2 of 18 at 0.02); B is the deciding condition (CALIBRATION.md §8). Known effect, product decision. |
| property_type dominates behaviour similarity when price / geography are suppressed | 0/1 feature; drives 38–67 % of pair-similarity variation in hard-filtered pools (CALIBRATION.md §5). Feature weights stay provisional (not calibrated) until real feedback exists. |
| Two narrowable ambiguous criteria handled strictly sequentially need 9 model turns | `controller.max_model_turns` is 8 (existing-default). A model that issues the searches of both criteria in one turn needs 6. Only 9 of 3,260 catalog names are narrowable at all (CALIBRATION.md §10). Not changed; listed as a pending decision (P7 review). |
| Exposure metrics | Computed only in the external evaluation (`recommendation/analysis/simulate.py`), over simulated users; the subagent keeps no exposure history (test `test_runtime_modules_never_import_the_analysis_package`). PERFORMANCE.md. |
| Not done in P7 part 1 | Done in P7 part 2 (see "Final state (P7)" above): patch registration and the upstream `--check`, the live smoke run, the live checklist, MIGRATION.md and the docs rewrite. |

## Resolved in P6 (slim trace)

| Former interim item | Resolution |
|---|---|
| Tool-step trace snapshots still embed the pool rows (format unchanged; P2 until P6) | P6 part 1 (`slim-v1`): tool steps store arguments + a compact result, no dispatcher snapshot. P6 review: the per-call version digests no longer serialise the pool either (`Dispatcher.identity()`; 21,431-listing synthetic scripted run 1.70 / 1.62 s → 1.33 / 1.18 s, stubbed digests 1.18 / 1.15 s). |

## Resolved in P5 part 2 (cold start + select-v1)

| Former interim item | Resolution |
|---|---|
| Selection is the ranked prefix (`ranked-prefix-v1`), cold start not evaluated | `ranking/selection.py` + `ranking/exploration.py` (select-v1): tie group, C/B/S diagnostics, cold-start price exploration, near-tie sampling, R3 seeds, R6 selection_input; `cold_start.*` / `selection.*` in effect (VALIDATION.md P5 part 2). `exit_conditions_met` can now be true, so the orchestrator's exit storage (P2) becomes reachable. |

## Decisions and known effects of P6 part 2 (terminal)

| Item | Decision / effect |
|---|---|
| Step mode scope | Scripted controller only. Stages run in rolled-back state transactions (no commit, no pool_cache writes); `final_gate` is a dry run. The session previews the exact request `recommend` will submit. The time budget restarts per step; `controller.max_tool_calls` counts per session (`step reset`). |
| Invalidation granularity | Conservative levels (`terminal/debug.py::invalidation`): any change to a hard criterion invalidates the pool, even when the canonical pool_key would not change (e.g. only a hard criterion ID). Re-running build_pool then reports a cache HIT. Settings sections other than geography / entity_resolution / cache invalidate rank only. |
| Retained vs recomputed data | Inspection uses the dispatcher retained in memory from this terminal's latest run. After a restart, or for a listing outside the pool, the data is re-read from the current source and labelled `recomputed`; it may differ from the original run if the data changed. |
| Live progress events | Emitted by the terminal's controller wrapper (tool start/end, model turns, gate), not by the service; the HTTP sidecar has no progress stream (unchanged). |
| Seed override | `set-seed` sets `Dispatcher.seed_override` through the terminal's controller wrapper only, and is recorded in the trace (`seed_override.value`). The service API never sets it. |
| Live LLM transcript | Not recorded: the P6 live transcript uses the scripted controller (no LLM), as the task asked. Live-model progress events are tested with a scripted fake model only. |

## Decisions and known effects of P6 part 1 (slim trace)

| Item | Decision / effect |
|---|---|
| Live transcript size | Model steps store each message once (`messages_added`), so the transcript is linear in model turns. Tool messages carry what the model saw (≤ `controller.model_result_candidates` IDs and query results per call), so a live query call adds ~35 KB with a 40-listing pool and ~57 KB at the 100-ID cap (measured, VALIDATION P6 review). Below the cap the growth per call follows min(pool, model_result_candidates); the live bound is about `controller.max_tool_calls` × the capped per-call size (20 × ~57 KB ≈ 1.1 MB), never the pool size above the cap. Tool steps keep ≤ `trace.preview_rows` rows. Shrinking the model-facing payload is a controller decision, not a trace change (P7: review together with the trace limits). |
| Debug row budget | `trace.debug_max_rows` caps all debug rows of a trace **in total**. Since the P6 review the budget is shared: the drawn listings first (exploration candidates in cold start, else tie members), then `trace.preview_rows` top-score rows with contributions, then the other tie members and exploration candidates round robin, then more top-score rows. Each list keeps score order and reports `total` / `included`. Only the latest rank stage of a run is kept. |
| `TEXT_LIMIT` is a code constant (resolved in P7 part 1, see above) | `tracing/slim.py` `TEXT_LIMIT = 4000` (characters kept of one string in compacted tool-step data / arguments) is not in recommendation.yaml. Moving it would change config_hash and with it the generated contract examples and fixtures; it is listed here for the P7 calibration of the trace limits (move to `trace.max_string_chars` with a source annotation then, or keep it as a documented bound). |
| Station exit provenance (R1c) | The pool-wide nearest exits stay in run memory (`Dispatcher.geo_records`). Since the P6 review the final_gate step records `geographic_provenance` = per station criterion, the exit used for each RETURNED listing with a measured distance (≤ result_limit entries per criterion), from the gate's by-id evidence recomputation. |
| Replay traces | A replay of a committed request saves its own trace (input_validation + final_gate, mode immutable_replay) under the same request_id. Since the P6 review `export`, `show-trace <request-id>` and the `show-scores` fallback read the run's own trace by `response.execution.trace_id`; `show-trace <id> --latest` and `export` (`latest-replay-trace.json`) offer the replay trace separately. |
| Extra stage `response_assembly` | Not in the spec's list; added so that failures after the final gate (assembly, snapshot, commit) locate a stage instead of `controller`. |
| Rank sub-stage failure | The failed sub-stage gets the error record; scoring keeps its full record; selection sub-stages that finished before the failure get duration-only ok records (no selection object exists). |
| Trace format | Still `"format": 2` (store contract). Slim traces add `"layout": "slim-v1"`. P2–P5 v2 traces (step snapshots, full messages) stay readable through the same references. |
| Credentials | `finalize` applies `tracing.trace.redact` (unchanged manifest file) and `slim.scrub`: secret-like key names (password/pwd/secret/api key/access key/private key/authorization/credential/dsn/access/refresh/auth/session/service token/bearer, separators ignored) and strings carrying a URL password or `user:pass@host:port`. `total_tokens`/`tokens` are not matched. Since the P6 review JSON-encoded strings (model-facing message content) are scrubbed value by value and kept byte for byte when no value is a credential (compact JSON around a title such as 'Suites@Changi' had redacted whole tool messages); URL tokens stop at quotes / backslashes and the scheme-less pattern excludes quotes and JSON delimiters. Live check: 0 of 21,431 RENT rows (785 contain '@') are flagged. Known effect: a data string literally shaped `a:b@c:123` is still redacted (that value only). |

## Decisions and known effects of P5 part 2 (cold start + select-v1)

| Item | Detail |
|---|---|
| Exploration summary semantics | `selection.exploration`: `candidate_count` = \|R\|, `sample_size` = \|A\| (stage A); per bin `count` = candidates of R in the bin, `quota` = the bin's stage-A allocation (its members in A; = count when \|R\| <= target), `sampled` = the bin's displayed items (stage-B allocation). So sum(count) = \|R\|, sum(quota) = \|A\|, sum(sampled) = m. Bins listed: every numeric bin of the pool's edges, plus the unknown-price bin when a candidate has no price. The contract shape is unchanged (no schema description added). |
| Largest-remainder bins can get no slot (P7: evaluated, see above) | A bin whose proportional share m × count / \|A\| is below 1 and whose remainder is not among the largest gets quota 0 (only exact remainder ties are random). Its candidates are not explored in that pool; e.g. one candidate alone in the top price bin with m = 7 of 30 (`test_every_tie_group_member_is_selected_across_seeds_and_ids_are_not_fixed`, L033). Intended by the spec rule; P7 calibration of `quantile_bins` / `target_sample_size` should look at it. |
| R may reach below the tie group | R = the first max(\|G\|, min(target_sample_size, \|pool\| − \|L\|)) items after the leaders, so when \|G\| < 30 exploration candidates include listings with clearly lower scores than the cutoff (plan scenario "leaders 3 + ties 4–20": R = ranks 4–33). Spec rule, unchanged. |
| C counts unresolved soft targets as unknown | C is the soft evidence coverage (spec section 8): an unresolved/ambiguous SOFT geographic target lowers C (reason `c_below_min`), never `no_soft_requirements`, and does not turn the run into a clarification; the resolution problem is reported in `response.resolution`. So a request whose soft target cannot be resolved stays in cold start until the coverage threshold is met. |
| Reason string | `selection.cold_start.reason` is a "; "-joined list of tokens: `cold_start_disabled` / `empty_pool` / `small_pool` (not evaluated), `no_soft_requirements`, `c_below_min`, `b_above_max`, `s_below_min`, `exit_conditions_met`, `exited_by_orchestrator`, `reentered_pool_rebuilt`. |
| Diagnostics are evaluated even after the orchestrator's exit | When `request.cold_start.exited` holds for this pool_key, C/B/S are still computed and reported (cold start is not active); `exit_conditions_met` then describes this round only. |
| Cold start disabled | `cold_start.enabled: false` → diagnostics not evaluated (`cold_start_disabled`), never an exit claim; near-tie sampling still applies (examples/config.json experiment file). |
| Seed override | `Dispatcher.seed_override` (default None) replaces the R3 base seed and is recorded (`seeds.override`, trace `seed_override`); only the terminal set-seed sets it (done in P6 part 2: `terminal/debug.py`, recorded as trace `seed_override.value`). |
| Python version | `selection_input.python_version` is recorded; replay does not refuse another version (random.Random sample/random are stable across CPython 3.x for a given seed, not re-verified here). |
| Stored head size | When the whole pool ties (e.g. no soft criteria and no behaviour signal), `selection_input.head` holds the whole pool (R6): about 4,700 entries for the live hard-geography pool, 21,431 for an unfiltered table (≈1 MB JSON per run snapshot). |
| Live exact-prefix oracles | `tests/native-geography-live.test.ts` was rewritten in the P5 review round as a select-v1 oracle (leaders == O prefix, score_rank == position in O, drawn items within \|G\| or \|R\| after the leaders with matching reasons, exact prefix only for `ranked` / `small_pool`); not run. `tests/native-recommendation-live.test.ts` (also weighted-v1 formulas, `feedback_version`, v1 feedback body) and `tests/advanced-live.test.ts` (app on 8010; baseline-style tie-break) still compare an exact prefix and are rescheduled to P7 together with their live runs (INVENTORY section P). All env-gated. |
| Tie-group boundary in IEEE doubles | `selection.tie_group` evaluates \|s_i − s_K\| ≤ ε/2 on the computed doubles without a tolerance (pinned in its docstring, like R11). A difference that is nominally ε/2 in decimal can fall on either side (abs(0.31 − 0.30) = 0.010000000000000009 > 0.01, abs(0.41 − 0.4) = 0.009999999999999953 ≤ 0.01); replay repeats the same comparison on the stored head, so results are reproducible. Tested by `test_tie_group_boundary_is_evaluated_on_ieee_doubles_without_tolerance`. |

## Resolved in P5 part 1 (full-pool fused scoring)

| Former interim item | Resolution |
|---|---|
| Appointment signals are validated but carry no score | In-pool appointment listings are the appointment-branch references (`ranking/scoring_v2.py`); the transitional warning is removed (VALIDATION.md P5 part 1) |
| weighted-v1 legacy constants (INVENTORY section L) override YAML keys | `recommendation/ranking/legacy.py` deleted; every `ranking.*` key is in effect (`settings/pending.py` lists only cold_start, selection, trace.mode, trace.debug_max_rows) |

## Decisions and known effects of P5 part 1

| Item | Detail |
|---|---|
| Self pairs (R1d) | A listing that is its own only reference gets no behaviour score, so with one appointment the booked listing itself can fall below listings similar to it (fixture flow round 2: fixture-07 moved from 2nd to 5th). Intended by R1d; visible in `behavior_score: null` for that item. |
| Zero-variance drop is per pool and per scaling (P7: scaling now p05_p95, see above) | With `numeric_scaling: iqr`, a numeric feature whose middle 50 % is constant (e.g. bedrooms in a narrow hard-filtered pool) has IQR 0 and is dropped (`dropped_features: {bedrooms: zero_variance}` in the rank_candidates trace step); `std` would keep it. Live full RENT table: IQRs price 4,300, bedrooms 3, floor_area_sqft 958 (nothing dropped). P7 calibration should check representative hard-filtered pools. |
| Single-reference normalisation | With one available browsing reference the browsing score is that pair's similarity whatever the duration (weights are normalised first, so the cancellation is exact). Documented in `scoring_v2.py`, in `context.scoring_policy.browsing.single_reference`, tested. |
| Coverage gate uses exact float comparison | A pair is unavailable when compared weight / active weight < `min_comparison_coverage` (no tolerance). Deterministic; values that land within rounding of the threshold follow IEEE arithmetic. |
| Contribution volume | Returned items carry one Contribution per (reference, active feature) per branch plus one per soft criterion; appointment history is not capped by the contract, so a very long appointment history enlarges each returned item (not the trace preview count). |
| Selection unchanged (part 1 only) | Superseded by P5 part 2 (select-v1); recorded for history. |
| Docs still describe weighted-v1 | `README.md`, `recommendation/README.md`, `docs/Recommendation_Agent_原生接入说明.md` (INVENTORY section N, open-P7). |
| Historical score oracle | `baseline/capture_scores.py` imports the removed `ranker.score`; it still runs against a 4d36507 archive (`PARITY_ROOT`) and `baseline/score-parity.json` is kept as evidence, no longer asserted. |

## Decisions and known effects of P4 part 1 (native geography)

| Item | Detail |
|---|---|
| 轻轨 is not stripped by `normalizePlace` (TS parity) | "武吉班让轻轨站" normalises to "武吉班让轻轨" and therefore matches no station (unresolved, never guessed). Fixing it needs a change on both sides (TS `normalizePlace` + the shared fixture) or an LRT alias entry; not done in P4. |
| `frontend/app.js` still renders `NATIVE_GEOGRAPHY_GAP` | The adapter no longer emits it (P4); the frontend filter keeps the code so archived exchanges that carry it still show the warning (old-name compatibility). |
| Place catalog freshness | OSM places are cached per process by Neo4j `Dataset.snapshot_sha256`; edits to Place nodes without a new snapshot hash are not seen until restart (same limit as the data version). |

## Resolved in P4 part 2

| Former interim item | Resolution |
|---|---|
| Ambiguous targets could only be clarified by the user | `search_places` + `bind_entity` (since the P4 review: single candidate of a kind/category-narrowed search for the ambiguous criterion's own name in the same run); otherwise still needs_clarification (VALIDATION.md P4 part 2 and P4 review round) |
| Tool names unchanged (`retrieve_listings` with an implicit resolution stage) | Pipeline tools `resolve_entities` -> `build_pool` -> `get_geographic_evidence` -> `rank_candidates` with dependency checks and invalidation, six query tools, `bind_entity`, budgets and the new live prompt |

## Decisions and known effects of P4 part 2 (tools, bind, controller)

| Item | Detail |
|---|---|
| `search_places` has an optional `criterion_id` (not in the spec signature) | Needed to count `entity_resolution.max_search_attempts` per criterion. `bind_entity` accepts only a candidate of a search made WITH that criterion_id, so the per-criterion limit cannot be bypassed by untagged searches (those count only against `controller.max_resolution_tool_calls`). |
| Bind only narrows the criterion's own name (P4 review) | `bind_entity` needs an ambiguous-by-name criterion (not an ID/name conflict), a search for that criterion whose query normalises to the criterion's own `destination_name` (after the TS NUS/NTU map), a single (kind/category-filtered) result, membership in ALL the name's exact matches and valid coordinates. An unresolved name is therefore never bound (it has no exact match), and a model can no longer turn 'NUS ISS' into the NUS campus or an ambiguous name into an unrelated area. Every binding stays visible (`resolution.method = bound_search`, reason `single_search_candidate (search #n)`, trace snapshot `bindings`/`searches`); the criterion itself never changes. Known effect: model-side translations or alternative spellings of a name can be searched (information only) but never bound. |
| `validate_hard_constraints` folded into `rank_candidates` | The spec's pipeline has four stages; criterion results are computed inside `rank_candidates` for the pool rows. `get_geographic_evidence` is required before ranking even without soft geographic criteria (no-op), so the order is always the same. |
| Budget semantics | Every call counts against `controller.max_tool_calls` (exhaustion ends the run, LOOP_EXHAUSTED, as before). Query and bind calls that actually run also count against `max_resolution_tool_calls`: calls rejected with INVALID_ARGUMENT (schema or the tool's own checks), STALE_REFERENCE (stage order / other run), UNSUPPORTED_OPERATION or a budget error are not charged (P4 review); a BIND_REJECTED bind was evaluated and is charged. Exhaustion is a tool error (`RESOLUTION_BUDGET_EXHAUSTED`) so the pipeline can still finish. Per-criterion searches: `SEARCH_BUDGET_EXHAUSTED`. |
| Listing-based query tools need `build_pool` | Listing IDs must belong to the current pool (the only listing IDs tools show the model), at most `controller.model_result_candidates` per call. Entity IDs for `distance_to` must have been returned by a tool of the run (resolution results/candidates, search results, nearest stations, amenities, planning areas). |
| Tool argument ceilings are code constants | `MAX_RADIUS_M` 50 km, `MAX_AMENITIES_PER_LISTING` 20, query/ID length 200 (`recommendation/tools/arguments.py`): validation bounds of model-supplied values, not tunable algorithm parameters, so not in the YAML. |
| Platform NEAR_MRT text | Read by ID (READ ONLY) from `properties.nearest_mrt / mrt_distance_m / mrt_walking_mins`, shown by `nearest_mrt` only for listings without coordinates, labelled `platform_reported` and never used for criteria. Not available in fixture mode (`available: false`). |
| Terminal resolution commands | Resolution runs natively from the existing terminal commands (`requirements` + `recommend`, scripted, no node/TS); `show-resolution` and the entity-ambiguity transcript are done in P6 part 2 (`test_terminal_debug.py`, `p6/*-terminal-transcript.txt`). |

## P4 review round: resolved items and decisions pending for the user

| Item | Status |
|---|---|
| ID + name in one criterion (plan Step 5: invalid ID -> name search with reason; ID/name conflict -> ambiguous) | Resolved by implementing it end to end: contract 2.0 `Criterion` accepts a category alone, or `destination_id` and/or `destination_name` (Python validator; the generated JSON schemas and the zod mirror never encoded "exactly one", so they are unchanged); the TS adapter sends an app target's `place_id` together with its `name`. **Pending user confirmation (contract change):** an app target whose name natively resolves to a different entity than its `place_id` now gets `needs_clarification` (`id_name_conflict`) instead of silently using the ID. TS resolves names with the same rules, so this needs a real disagreement (e.g. TS picked a fuzzy Data-API hit that differs from the native exact match). Option B (keep the old one-target rule, plan rule deferred) remains available by reverting the validator and the adapter line. |
| Explicit-station rule | Native now uses the TS rule exactly (MRT/LRT/地铁站/地铁; no NFKC), checked by the shared fixture `tests/fixtures/place-mention-rules.json` run by a TS test and a Python test (TS helpers `isExplicitStation` / `stationMode` exported, behaviour unchanged). Effect: 'Bukit Panjang Station', 'Clementi Station', 'Punggol Station' resolve to the URA area on both sides (they were station requests / ambiguous natively). **Pending user decision:** whether `station`, 站, 轻轨 and 捷运 should also mark an explicit station request — if yes, change TS `isExplicitStation` and the Python port together and regenerate the shared fixture. |
| Generic MRT name ('MRT', '地铁站', 'nearest MRT station') | Native ports TS `isGenericMrt`: a name-only generic mention resolves to category `MRTStation` (method `name->category`); with a verified ID it never conflicts. Same shared fixture. |
| OSM place without coordinates as destination | `unresolved` with reason `destination_without_coordinates (<id>)` (resolver, bound_resolution); bind_entity rejects such an entity. A hard criterion then gives needs_clarification (R8), like a category without located places. Latent in the live graph (0 of 3,716 active places lack coordinates). |
| Missing-coordinate reason text | Listings without coordinates now get "listing has no valid coordinates (unknown; never 0 m)" in criterion evidence, unknown_reasons and warnings (the tool text). Live responses that showed the old NEAR_PLACE text change wording only. |
| get_geographic_evidence model payload | The ToolResult sent to the model carries per-criterion summaries (criterion_id, resolution status/entity/method/reason, method, listings, known, unknown); per-listing nearest-exit provenance stays in run memory (`Dispatcher.geo_records`); since P6 the slim trace stores none of it (`test_geographic_evidence_result_is_compact_for_the_model`). |
| pool_version vs resolution method | Pool content keeps only the resolved status/entity per hard-geo key, so an ID, a name and an alias of one entity give the same pool_version. `POOL_FORMAT_VERSION` -> `pool-v4` (also covers the explicit-station, generic-MRT, ID+name and missing-coordinate changes); cached `pool-v3` entries are simply never matched again. |

## Known limits of the data version (P3, spec R5; documented, not defects)

- The SQL fingerprint scans every RENT row server-side (~150–175 ms measured live per query, two queries per cache-hit run: lookup + final gate). A cache hit saves row transfer, normalisation and hard-geo evaluation, not this scan.
- Neo4j Listing coordinates are not hashed (SQL coordinates are canonical; since P4 no geographic fact reads graph edges); the catalog version covers knowledge/locations, the hash-checked datasets and the Neo4j `Dataset.snapshot_sha256` (OSM places).
- When the fingerprint or the catalog version cannot be read, the pool is rebuilt and never indexed (`cache: "bypassed"`, warning); the final gate then falls back to a full re-read compared with the digest of every source row.
- Same-key evidence: two criteria with the same canonical key (for example a hard and a soft distance to the same target) share R4 evidence IDs. Since the P3 review the response carries one Evidence per (evidence_id, criterion_id), so an `evidence_id` may appear more than once in `response.evidence`, each time with a different `criterion_id` (contract comment in `recommendation/contracts/response.py`; MIGRATION.md must state it in P7). replay_gate resolves by (id, criterion) and falls back to id only for responses stored before the change.

## Live checklist (P7 outcome: see "Final state (P7)"; only native-geography-live ran, against 8192, pass)

| Test / script | Edit made without running it |
|---|---|
| `tests/native-recommendation-live.test.ts` | result limit 4 → 10 |
| `tests/native-geography-live.test.ts` | ranking prefix 4 → 5 (P2); P5: fused-v2 score oracle `soft_weight × 1/(1+d/1000)` and the select-v1 selection oracle (P5 review round) |
| `tests/full-data-live.test.ts` | limit 5 → 10 |
| `tests/location-subject-live.test.ts` | limit 5 → 10 |
| `scripts/test_full_data_ui.py`, `scripts/test_location_subject_ui.py` | limit 5 → 10 (browser) |
| `scripts/test_native_recommendation_ui.py`, `evaluation/followup_workflow_browser.py` | P7: card counts read from the API state / rendered list (not run) |
| `tests/advanced-live.test.ts`, `tests/intake-live.test.ts` | P7: nearby / supermarket oracles mode-aware (select-v1 for native, top-10 for baseline) (not run) |

The running 8092 service still runs the pre-P2 code. The TS side of this tree must not be
deployed against it until it is restarted on this tree, which needs the user's OK. P7 ran a
separate instance from this tree on 8192 (temporary state, own token) and stopped it afterwards.

## Recorded incident

Seven live-controller, database-mode attempts from ad-hoc runs wrote test conversations `c1`,
`v2-fixture` and `v2-other` into `data/.runtime/recommendation/state.sqlite` (VALIDATION.md, P2
incident). The rows stay in place. Whether to remove them is the user's decision.
