# Recommendation v2: tuning and parameter provenance (P7, Step 10)

Single parameter source: `recommendation/settings/recommendation.yaml` (loader
`recommendation/settings/loader.py`). This page explains each value. The YAML comment on each
line carries the same source label as the table below (`recommendation/tests/test_analysis_p7.py`
checks that the two agree and that the table holds every key at its committed value).

Committed identity after P7: `config_hash` `e46e754a9c6e6eafd4e2522f7bee4f26de37f956de50d3be6be173549710b2c8`,
`selection_hash` `90e724bb160ae68524bd5d9b899959716d46f509627096c5696c7ec66d5a18a7`.

Source labels:

- `confirmed`: a rule fixed by the user's plan or the confirmed decisions (spec section 1, R1), or an
  explicit rule of the authoritative spec, cited in the evidence column (used for `cache.ttl_seconds`, like the
  explicit "null = uncapped" rule of `ranking.browsing.duration_cap_seconds`).
- `existing-default`: the value of the previous weighted-v1 policy, RunLimits or a former code
  constant, kept as it was.
- `experiment-result`: chosen in P7 from a measurement on the live data, the live catalog or a
  reproducible run of `python -m recommendation.analysis.calibrate` / `.simulate`. The value may
  be new or the old one, which is then marked "kept". Method and figures are in CALIBRATION.md;
  the raw output is in `p7/calibration.json` / `p7/evaluation.json`.
- `provisional`: the spec R2 label for a design choice that is **not calibrated**. P7 could not calibrate
  these values: doing so needs real user feedback or dwell data, which does not exist. They are not
  calibrated values and do not satisfy the P7 rule "each value from confirmed / existing default /
  experiment result"; that item is therefore reported as **partial** in VALIDATION.md, and every provisional
  key is listed under "Decisions pending for the user" in OPEN_ITEMS.md (a test keeps the two in step). In
  particular, user decision 2 ("price is a behaviour-similarity feature; its weight is calibrated") is **not
  met yet**: the price weight is still the R2 value 0.20. The evidence column gives what the data does show.

What changed in P7: `ranking.similarity.numeric_scaling` iqr → p05_p95 and
`selection.tie_max_score_span` 0.02 → 0.01. A new key, `trace.max_string_chars` (4000), replaces
the code constant `tracing/slim.py` TEXT_LIMIT. Every other value is unchanged. Former `provisional`
labels became `experiment-result` (kept) where a P7 measurement supports the value, `confirmed` for
`cache.ttl_seconds` (spec section 2 rule), and stay `provisional` (not calibrated, pending) for the ten
keys that need real feedback: the five similarity feature weights, `ranking.browsing.time_scale_seconds`,
`cold_start.stability.weight_perturbation_fraction` and the three cache bounds. `selection.tie_max_score_span`
is an experiment result derived under the provisional f = 0.2; it must be re-derived whenever f changes.

Changing values: use `RECOMMENDATION_CONFIG` / `--config` or the terminal `set-config` for
experiments (both record their config_hash). A change to a result-affecting section changes
`selection_hash` and with it the selection seeds of NEW requests only; a stored request always
replays its stored response. Re-run the analyses after any change to scoring or selection code:
`.venv/bin/python -m recommendation.analysis.calibrate --out <file>` (about 3.5 min live),
`.venv/bin/python -m recommendation.analysis.simulate --out <file>` (about 6.5 min live).

## Provenance table

| key | value | source | evidence |
|---|---|---|---|
| `recommendation.default_result_limit` | `10` | confirmed | plan: default 10 results, fewer when the pool is smaller |
| `recommendation.max_result_limit` | `10` | confirmed | equals the native contract maximum (cross-layer test) |
| `ranking.soft_weight` | `0.6` | confirmed | plan formula total = 0.60 soft + 0.40 behaviour |
| `ranking.behavior_weight` | `0.4` | confirmed | plan formula |
| `ranking.soft.numeric_bounds.price` | `[0.0, 20000.0]` | existing-default | Legacy compatibility only; current soft utilities use pool min/max |
| `ranking.soft.numeric_bounds.bedrooms` | `[0.0, 10.0]` | existing-default | Legacy compatibility only; current soft utilities use pool min/max |
| `ranking.soft.numeric_bounds.floor_area_sqft` | `[0.0, 10000.0]` | existing-default | Legacy compatibility only; current soft utilities use pool min/max |
| `ranking.behavior.appointment_weight` | `0.6` | confirmed | plan formula |
| `ranking.behavior.browsing_weight` | `0.4` | confirmed | plan formula |
| `ranking.behavior.missing_branch_contribution` | `0.0` | confirmed | plan: a missing branch contributes 0 |
| `ranking.behavior.redistribute_missing_branch_weight` | `false` | confirmed | plan: branch weights never redistributed |
| `ranking.behavior.counted_appointment_signals` | `["viewing_draft_saved"]` | confirmed | Main contract (merge with main c7dc536): `behavior_context.book_viewing_listings` = saved timed VIEWING drafts; equals the TS constant and main's schema literal (cross-layer test). Replaces the earlier `counted_appointment_statuses` (MANUAL_SENT / MANUAL_REPLIED / CONFIRMED). |
| `ranking.behavior.browsing_requires_interest_eligible` | `true` | confirmed | Main contract: only `listing_view_durations` with `interest_eligible=true` (main's 15 s / 2 listings / 60 s gate) are scoreable browsing facts. |
| `ranking.behavior.reference_scope` | `"in_pool"` | confirmed | decision 3 |
| `ranking.browsing.transform` | `"log1p"` | confirmed | plan: logarithmic growth, no cap |
| `ranking.browsing.duration_cap_seconds` | `null` | confirmed | null = explicitly uncapped |
| `ranking.browsing.time_scale_seconds` | `30.0` | provisional | Not calibrated (R2 value). No dwell data exists (the frontend has no browsing timer this phase), so the curve cannot be fitted. CALIBRATION.md §6 tabulates the trade-off: at 30 s a 5 s glance weighs 0.14 of a 60 s read, 600 s weighs 2.8×, and 120 → 240 s still adds +0.59. Scales of 10–60 s are all plausible; not calibratable until real dwell data exists. |
| `ranking.similarity.suppression` | `"none"` | confirmed | User revision: hard constraints do not suppress similarity features. |
| `ranking.similarity.feature_weights.price` | `0.2` | provisional | Not calibrated (R2 value): preference weights cannot be fitted without real feedback, so user decision 2 ("its weight is calibrated") is not met yet (OPEN_ITEMS pending decision). Live figures (§5): price is suppressed in every scenario with a budget (8 of 9), and in the unfiltered pool it drives 18 % of pair-similarity variation |
| `ranking.similarity.feature_weights.property_type` | `0.2` | provisional | Not calibrated (R2 value): no feedback to fit. property_type is a 0/1 feature, so it drives 38–67 % of the variation where price and geography are suppressed; removing it changes 10–100 % of the behaviour top 10 (§5). That is a known effect, not a reason to change the value without feedback |
| `ranking.similarity.feature_weights.bedrooms` | `0.16` | provisional | Not calibrated (R2 value): no feedback to fit (existing 0.20 × 0.8). Compared in 95–99 % of pairs |
| `ranking.similarity.feature_weights.floor_area_sqft` | `0.2` | provisional | Not calibrated (R2 value): no feedback to fit. Missing for 39 % of all listings and 64–85 % in six pools, so it is compared in only 5–46 % of pairs there; renormalisation over the compared features handles this (§4) |
| `ranking.similarity.feature_weights.geography` | `0.24` | provisional | Not calibrated (R2 value): no feedback to fit (existing 0.30 × 0.8). Suppressed in the geographic hard scenarios (decision 1) |
| `ranking.similarity.numeric_scaling` | `"minmax"` | confirmed | User revision: fit the range on the current full eligible pool; not a new empirical calibration. |
| `ranking.similarity.geographic_scale_m` | `3000.0` | existing-default | Legacy configuration compatibility only; current geography similarity uses full-pool distances per reference. |
| `ranking.similarity.geography_district_fallback` | `true` | experiment-result | Kept. 60 % of listings lack coordinates. Without the fallback, geography is unavailable for 90 % of pairs in the unfiltered pool and 60 % in the budget pool, and 57 % of budget-pool pairs fall below the coverage gate. With the fallback both are 0 % (§5) |
| `ranking.similarity.min_comparison_coverage` | `0.5` | experiment-result | Kept. At 0.4–0.5 at most 0.7 % of pairs are unavailable in any pool. At 0.7, 80–90 % become unavailable in 5 of 9 pools because floor_area_sqft is missing. 0.5 is the largest value that keeps pairs comparable when one of two remaining features is missing (§4) |
| `geography.station_distance_method` | `"nearest_exit"` | confirmed | R1c |
| `cold_start.enabled` | `true` | confirmed | plan Step 7 |
| `cold_start.reenter_on_pool_rebuild` | `true` | confirmed | decision 4 |
| `cold_start.min_evidence_coverage` | `0.8` | confirmed | C threshold |
| `cold_start.max_boundary_tie_group_size` | `5` | confirmed | B threshold |
| `cold_start.min_top_k_stability` | `0.9` | confirmed | S threshold |
| `cold_start.price_sampling.target_sample_size` | `30` | confirmed | Initial experimental value, not a statistical guarantee. §7 compared 10/20/30/40/60: mean score loss vs the deterministic top 10 grows with the target (mixed soft sets: 0.2–0.35 % at 10, 0.8–1.1 % at 30, 1.3–2.0 % at 60), and a larger target spreads the draw over more of R |
| `cold_start.price_sampling.quantile_bins` | `5` | experiment-result | Kept. Duplicate edges merge 5 bins into 4.8 on average (98.6 % of rows share a price). In tied pools ≥ 97 % of non-empty R bins get a displayed listing up to 6 bins, against 85–96 % at 10 bins. More bins widen the displayed price span only slightly (0.63 → 0.66) (§7) |
| `cold_start.stability.trials` | `20` | experiment-result | Kept. Across 30 seeds per trial count, std(S) at 20 trials is 0.009–0.021 and the exit decision agrees with a 1000-trial reference in 100 % of cases (93 % at 5 trials). Cost ≤ 8–11 ms per trial on the 21,431-listing pool (§8) |
| `cold_start.stability.weight_perturbation_fraction` | `0.2` | provisional | Not calibrated (R2 value). What counts as a "reasonable perturbation" is a design choice. The data shows little sensitivity: median S over the 18 mixed-soft cases is 1.0 / 0.995 / 0.975 / 0.955 at f = 0.05 / 0.1 / 0.2 / 0.3, and S ≥ 0.9 in 18 / 18 / 18 / 17 cases. The epsilon basis (below) depends on this value |
| `selection.tie_max_score_span` | `0.01` | experiment-result | Changed from 0.02 (§8). Under the configured perturbation (f = 0.2), how often two cutoff-window scores swap order depends on their gap: 16.5 % at gap ≤ 0.0025, 4.2 % at (0.0025, 0.005], 1.0 % at (0.005, 0.01], 0.2 % at (0.01, 0.02]. G holds the scores within epsilon/2 of s_K, so 0.01 groups exactly the gaps that are not reliably ordered (≤ 0.005). At 0.02, B ≤ 5 holds in only 2 of 18 mixed-soft cases (7 of 18 at 0.01), so cold start almost never ends |
| `selection.random_policy` | `"uniform_without_replacement"` | confirmed | plan |
| `selection.seed_namespace` | `"recommendation-selection-v1"` | confirmed | R3 |
| `cache.enabled` | `true` | confirmed | plan Step 4 |
| `cache.ttl_seconds` | `0` | confirmed | Spec section 2 rule: `ttl_seconds` null/0 = no TTL, the pool is invalidated only by pool_key / data_version (R2 encodes it as 0 and listed it provisional pending P7). No TTL experiment was run. Supporting design facts: every lookup and every final gate compares data_version, the SQL fingerprint over all RENT rows (detects updates and deletions, P3 tests), so a TTL could add no freshness, only earlier eviction; measured fingerprint cost P50 142 ms (§9) |
| `cache.max_entries_per_conversation` | `2` | provisional | Not calibrated (spec section 2 schema value; no source given there). No usage data on how often users switch between hard-criteria sets. The largest pool is about 10 MB of JSON content (§9) |
| `cache.pool_retention_per_conversation` | `4` | provisional | Not calibrated (R2 value): an inspection-retention bound with no usage data (must be ≥ max_entries) |
| `cache.memory_max_entries` | `4` | provisional | Not calibrated (R2 value): no process-memory budget is defined. Largest pool content about 10 MB of JSON (§9) |
| `entity_resolution.max_search_attempts` | `3` | experiment-result | Kept. Of 3,260 catalog display names, 223 resolve ambiguous and only 9 can be narrowed by kind/category. The informed policy narrows all 9 within 2 searches (6 within 1); the naive one needs up to 4. The other 214 share kind and category (chain brands) and need a clarification (§10) |
| `entity_resolution.max_candidates` | `5` | experiment-result | Kept. 5 lists every candidate for 89.5 % of the 314 ambiguous name keys (3 → 79 %, 8 → 93 %, 10 → 94 %); the median ambiguous name has 2 candidates (§10) |
| `controller.max_model_turns` | `8` | existing-default | RunLimits, kept unchanged. The offline fake model used ≤ 6 turns, issuing one tool call per turn, for one narrowable criterion. Measured against the kept value: two narrowable criteria handled strictly sequentially would need 9 turns (6 with parallel calls); narrowable names are 9 of 3,260 (§10). Whether to raise it (e.g. to 10, ceiling 16) is listed under "Decisions pending for the user" in OPEN_ITEMS.md |
| `controller.max_tool_calls` | `20` | existing-default | RunLimits; at most 7 calls observed (§10) |
| `controller.max_resolution_tool_calls` | `6` | experiment-result | Kept. A narrowable criterion used 2–3 resolution calls (≤ 2 searches + 1 bind). 6 covers two such criteria (§10) |
| `controller.timeout_seconds` | `90.0` | existing-default | RunLimits; live scripted runs take ≤ 2.3 s at P95 (PERFORMANCE.md) |
| `controller.repairs` | `2` | existing-default | RunLimits |
| `controller.model_result_candidates` | `100` | existing-default | RunLimits |
| `trace.mode` | `"summary"` | confirmed | plan Step 9 |
| `trace.preview_rows` | `10` | experiment-result | Kept. Equals one result page. Trace bytes at 5/10/20 rows: 75.8/88.2/111.3 KB (unfiltered pool) and 70.7/82.7/105.6 KB (MRT pool), about +2.3 KB per row, independent of pool size (§11) |
| `trace.debug_max_rows` | `200` | experiment-result | Kept. Debug part at 50/100/200/400 rows: 15.9/64.6/171.6/385.7 KB in the large tied pool, so 200 rows bound a debug trace at about 260 KB (§11) |
| `trace.max_string_chars` | `4000` | existing-default | Former tracing/slim.py TEXT_LIMIT, moved to the YAML in P7. Longest string ever stored in a live tool step: 152 characters; longest title 100, URL 285 (§11), so real data is never cut |
