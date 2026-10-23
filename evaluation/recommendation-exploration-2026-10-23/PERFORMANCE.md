# Recommendation v2: performance and exposure evaluation (P7, Step 10)

Three service modes compared on the live data over SIMULATED multi-round sessions. Raw output:
`p7/evaluation.json` (committed settings after P7); `p7/evaluation-pre-p7-settings.json` is the
same evaluation under the pre-P7 provisional settings (iqr, epsilon 0.02).

**Not claimed.** Price sampling does not reduce scoring cost: every mode scores the full pool
every round (the `scoring` stage is the same order of magnitude in all three modes), and cold
start adds the stability diagnostics. Nothing here measures user satisfaction. The users are
rule-based simulators, and the "simulated match" row describes the simulator's own hidden
preference, not real users.

## Method

- Command: `.venv/bin/python -m recommendation.analysis.simulate --sessions 6 --rounds 5 --out
  evaluation/recommendation-exploration-2026-10-23/p7/evaluation.json` (6 min 23 s, 2026-10-23,
  live PostgreSQL / Neo4j read-only, services on 8010/8091/8092 untouched).
- Modes (in-memory overrides of the committed YAML):
  - `no_cache`: cache.enabled false, cold_start.enabled false
  - `pool_cache`: cache.enabled true, cold_start.enabled false
  - `cache_cold_start`: cache.enabled true, cold_start.enabled true
- Each request goes through `RecommendationService` with the ScriptedController, the shared
  read-only repositories and one TEMPORARY state file per mode. For each round the three modes run
  back to back, so database warm-up affects them alike.
- Plan: 4 scenarios (`budget_bedrooms` + mixed soft, `mrt_distance` + district soft,
  `property_type` with no soft criteria, `none` + mixed soft) × 6 sessions × 5 rounds = 120
  requests per mode, plus a replay of every request.
- Simulated orchestrator (R7): previous_round is the last response's shown IDs with exactly one
  browsing record each, appointments are every listing booked so far (one entry per listing; a
  later CONFIRMED is a status upgrade), and the cold-start exit is stored only after a response
  with evaluated and exit_conditions_met.
- Simulated user: a hidden target price, property type and districts drawn from the pool with a
  fixed seed. Browsing time grows with the match (4 s + 90 s × match × log-normal noise), 10 % of
  records are missing, low matches are often 0 s, and a viewing is booked when the match ≥ 0.75
  (probability 0.5). Durations depend only on (session, round, listing), so identical selections
  get identical feedback in every mode.
- Metrics are computed in the external script, never in the subagent:
  - stage durations from the slim trace stage records; end-to-end = `execution.duration_ms`
    (service) and wall clock;
  - coverage = share of the pool shown at least once over all sessions and rounds;
  - exposure Gini over the WHOLE pool including never-shown listings (plus Gini over the shown
    listings only);
  - score loss = 1 − Σ shown combined scores / Σ deterministic top-K scores of the same run,
    the top K coming from the run snapshot's selection_input head (the full-pool order O);
  - correctness checks per response: status ok, no duplicates, every listing in the pool version
    (IDs from an independent production pool build, and `pool_version` equal), every hard
    criterion pass, select-v1 replay of the stored selection_input reproduces the IDs, re-submitted
    request returns the identical stored response; across modes, no_cache and pool_cache
    selections are identical.

## Summary

- Correctness: **7 checks × 120 requests × 3 modes all pass**, and no_cache == pool_cache
  selections in all 4 scenarios, so the cache never changes a result.
- Pool cache: rows transferred per request drop from 21,431 to 0 on a hit. pool_build_or_load P50
  drops from 745–1,198 ms to 147–200 ms. The remaining ~150 ms is the data-version fingerprint
  query (R5: a hit saves transfer, normalisation and hard-geo evaluation, not the server-side
  scan). End-to-end rounds 2–5 P50 drop from 949 / 1,477 / 1,186 / 1,981 ms to
  335 / 503 / 439 / 1,302 ms. Round 1 (miss) is 130–240 ms slower with the cache (fingerprint +
  cache write). Because 1 in 5 requests is a miss, the all-rounds P95 of the cache modes is a
  round-1 figure.
- Cold start (cache_cold_start vs pool_cache): the cold_start_diagnostics stage (20 stability
  trials) adds P50 4 / 15 / 30 / 105 ms (pool 306 / 3,698 / 5,853 / 21,431). price_sampling adds
  ≤ 3 ms. Scoring is unchanged because the full pool is scored in both. Exposure barely moves on
  these scenarios: distinct shown / possible 0.787 vs 0.777 (MRT), 0.780 vs 0.793
  (property_type), 0.200 vs 0.157 (unfiltered pool), 0.087 vs 0.097 (budget_bedrooms, where
  cold start exits in round 1 and never explores). Near-tie sampling already spreads exposure
  when ties are large. Score loss vs the deterministic top K stays ≤ 1.8 % on average in every
  mode (P95 ≤ 4.9 %).
- Exposure Gini including zero-exposure listings is 0.947–0.999 in every mode. 30 rounds × 10
  slots can show at most 300 of 306–21,431 listings, so this figure is dominated by pool size;
  the shown-only Gini (0.17–0.53) and the distinct-shown share are the comparable figures.

## Results per scenario

The P95 over "all rounds" in the cache modes is a round-1 (cache miss) figure.

#### budget_bedrooms/mixed (pool 306; no_cache == pool_cache selections: True)

| ms, P50 / P95 | no_cache | pool_cache | cache_cold_start |
|---|---|---|---|
| end-to-end (service, all rounds) | 949 / 997 | 337 / 1110 | 344 / 1113 |
| end-to-end round 1 | 951 / 985 | 1107 / 1133 | 1107 / 1134 |
| end-to-end rounds 2-5 | 949 / 999 | 335 / 342 | 341 / 377 |
| wall clock (all rounds) | 968 / 1012 | 357 / 1119 | 360 / 1120 |
| stage input_validation | 1.0 / 1.3 | 1.1 / 1.5 | 1.0 / 1.5 |
| stage entity_resolution | 1.9 / 2.6 | 1.8 / 2.2 | 1.9 / 2.4 |
| stage pool_build_or_load | 744.8 / 797.0 | 146.6 / 907.3 | 146.5 / 913.6 |
| stage geographic_evidence | 12.9 / 14.6 | 22.3 / 25.4 | 22.6 / 25.5 |
| stage scoring | 3.4 / 34.6 | 5.7 / 8.0 | 6.0 / 7.9 |
| stage cold_start_diagnostics | 0.0 / 0.0 | 0.0 / 0.0 | 4.3 / 5.8 |
| stage price_sampling | 0.0 / 0.0 | 0.0 / 0.0 | 0.0 / 0.0 |
| stage final_selection | 0.1 / 0.1 | 0.2 / 0.2 | 0.1 / 0.2 |
| stage final_gate | 179.2 / 183.4 | 150.6 / 182.2 | 152.8 / 179.6 |
| stage response_assembly | 1.8 / 2.0 | 2.9 / 3.5 | 2.7 / 3.5 |
| rows transferred (P50) | 21431 | 0 | 0 |

| outcome | no_cache | pool_cache | cache_cold_start |
|---|---|---|---|
| pool cache outcomes | bypassed 30 | built 6, reused 24 | built 6, reused 24 |
| selection modes | near_tie_sampling 28, ranked 2 | near_tie_sampling 28, ranked 2 | near_tie_sampling 25, ranked 5 |
| responses with exit_conditions_met | 0 | 0 | 24 |
| pool coverage (shown / pool) | 0.0948 | 0.0948 | 0.0850 |
| distinct shown / min(pool, slots) | 0.097 | 0.097 | 0.087 |
| exposure Gini incl. zero-exposure | 0.9555 | 0.9555 | 0.9565 |
| exposure Gini shown only | 0.530 | 0.530 | 0.488 |
| score loss vs top-K, relative mean / P95 / max | 0.0005 / 0.0012 / 0.0013 | 0.0005 / 0.0012 / 0.0013 | 0.0004 / 0.0012 / 0.0014 |
| score loss per item, absolute mean | 0.0003 | 0.0003 | 0.0003 |
| SIMULATED match of shown (mean) | 0.464 | 0.464 | 0.464 |
| correctness checks passed | 30/30 each of 7 | 30/30 each of 7 | 30/30 each of 7 |

#### mrt_distance/district (pool 3698; no_cache == pool_cache selections: True)

| ms, P50 / P95 | no_cache | pool_cache | cache_cold_start |
|---|---|---|---|
| end-to-end (service, all rounds) | 1480 / 1573 | 515 / 1631 | 533 / 1628 |
| end-to-end round 1 | 1486 / 1560 | 1621 / 1662 | 1620 / 1641 |
| end-to-end rounds 2-5 | 1477 / 1568 | 503 / 530 | 526 / 598 |
| wall clock (all rounds) | 1496 / 1588 | 528 / 1648 | 553 / 1645 |
| stage input_validation | 1.0 / 1.2 | 0.9 / 1.2 | 1.0 / 1.3 |
| stage entity_resolution | 2.7 / 6.2 | 2.9 / 3.7 | 2.7 / 3.3 |
| stage pool_build_or_load | 1198.3 / 1246.5 | 200.2 / 1334.9 | 200.5 / 1361.6 |
| stage geographic_evidence | 36.5 / 37.4 | 37.4 / 70.5 | 36.9 / 63.9 |
| stage scoring | 24.9 / 101.1 | 80.3 / 102.6 | 77.3 / 103.6 |
| stage cold_start_diagnostics | 0.0 / 0.0 | 0.0 / 0.0 | 15.4 / 27.4 |
| stage price_sampling | 0.0 / 0.0 | 0.0 / 0.0 | 0.7 / 1.1 |
| stage final_selection | 0.6 / 0.9 | 0.6 / 0.9 | 0.6 / 0.7 |
| stage final_gate | 174.8 / 203.9 | 170.7 / 187.3 | 171.4 / 189.0 |
| stage response_assembly | 4.0 / 4.8 | 3.2 / 4.8 | 3.4 / 5.1 |
| rows transferred (P50) | 21431 | 0 | 0 |

| outcome | no_cache | pool_cache | cache_cold_start |
|---|---|---|---|
| pool cache outcomes | bypassed 30 | built 6, reused 24 | built 6, reused 24 |
| selection modes | near_tie_sampling 30 | near_tie_sampling 30 | cold_start_exploration 30 |
| responses with exit_conditions_met | 0 | 0 | 0 |
| pool coverage (shown / pool) | 0.0630 | 0.0630 | 0.0638 |
| distinct shown / min(pool, slots) | 0.777 | 0.777 | 0.787 |
| exposure Gini incl. zero-exposure | 0.9478 | 0.9478 | 0.9470 |
| exposure Gini shown only | 0.171 | 0.171 | 0.169 |
| score loss vs top-K, relative mean / P95 / max | 0.0004 / 0.0013 / 0.0067 | 0.0004 / 0.0013 / 0.0067 | 0.0002 / 0.0011 / 0.0028 |
| score loss per item, absolute mean | 0.0003 | 0.0003 | 0.0001 |
| SIMULATED match of shown (mean) | 0.230 | 0.230 | 0.224 |
| correctness checks passed | 30/30 each of 7 | 30/30 each of 7 | 30/30 each of 7 |

#### property_type/no_soft (pool 5853; no_cache == pool_cache selections: True)

| ms, P50 / P95 | no_cache | pool_cache | cache_cold_start |
|---|---|---|---|
| end-to-end (service, all rounds) | 1181 / 1237 | 444 / 1346 | 500 / 1334 |
| end-to-end round 1 | 1167 / 1202 | 1331 / 1362 | 1317 / 1353 |
| end-to-end rounds 2-5 | 1186 / 1241 | 439 / 488 | 492 / 563 |
| wall clock (all rounds) | 1203 / 1255 | 458 / 1361 | 521 / 1352 |
| stage input_validation | 0.9 / 2.8 | 1.0 / 2.8 | 1.0 / 2.7 |
| stage entity_resolution | 0.0 / 0.0 | 0.0 / 0.0 | 0.0 / 0.0 |
| stage pool_build_or_load | 933.4 / 1002.0 | 159.1 / 1092.9 | 160.4 / 1095.9 |
| stage geographic_evidence | 1.1 / 1.2 | 1.2 / 1.2 | 1.2 / 1.2 |
| stage scoring | 26.2 / 119.2 | 35.7 / 120.9 | 30.8 / 136.6 |
| stage cold_start_diagnostics | 0.0 / 0.0 | 0.0 / 0.0 | 30.4 / 43.6 |
| stage price_sampling | 0.0 / 0.0 | 0.0 / 0.0 | 1.0 / 3.1 |
| stage final_selection | 0.8 / 2.1 | 0.8 / 2.4 | 0.8 / 2.3 |
| stage final_gate | 168.8 / 185.5 | 170.6 / 194.9 | 170.1 / 192.1 |
| stage response_assembly | 3.3 / 6.3 | 2.6 / 7.8 | 2.7 / 8.3 |
| rows transferred (P50) | 21431 | 0 | 0 |

| outcome | no_cache | pool_cache | cache_cold_start |
|---|---|---|---|
| pool cache outcomes | bypassed 30 | built 6, reused 24 | built 6, reused 24 |
| selection modes | near_tie_sampling 30 | near_tie_sampling 30 | cold_start_exploration 30 |
| responses with exit_conditions_met | 0 | 0 | 0 |
| pool coverage (shown / pool) | 0.0407 | 0.0407 | 0.0400 |
| distinct shown / min(pool, slots) | 0.793 | 0.793 | 0.780 |
| exposure Gini incl. zero-exposure | 0.9664 | 0.9664 | 0.9672 |
| exposure Gini shown only | 0.174 | 0.174 | 0.179 |
| score loss vs top-K, relative mean / P95 / max | 0.0180 / 0.0487 / 0.0690 | 0.0180 / 0.0487 / 0.0690 | 0.0158 / 0.0356 / 0.0433 |
| score loss per item, absolute mean | 0.0020 | 0.0020 | 0.0031 |
| SIMULATED match of shown (mean) | 0.525 | 0.525 | 0.605 |
| correctness checks passed | 30/30 each of 7 | 30/30 each of 7 | 30/30 each of 7 |

#### none/mixed (pool 21431; no_cache == pool_cache selections: True)

| ms, P50 / P95 | no_cache | pool_cache | cache_cold_start |
|---|---|---|---|
| end-to-end (service, all rounds) | 1977 / 2137 | 1320 / 2201 | 1491 / 2266 |
| end-to-end round 1 | 1936 / 2011 | 2178 / 2236 | 2250 / 2285 |
| end-to-end rounds 2-5 | 1981 / 2138 | 1302 / 1373 | 1489 / 1550 |
| wall clock (all rounds) | 2004 / 2176 | 1335 / 2222 | 1509 / 2292 |
| stage input_validation | 0.9 / 1.6 | 0.9 / 1.1 | 1.0 / 2.0 |
| stage entity_resolution | 2.9 / 4.3 | 2.9 / 3.9 | 2.9 / 3.4 |
| stage pool_build_or_load | 890.2 / 932.4 | 161.6 / 1038.9 | 162.2 / 1030.5 |
| stage geographic_evidence | 553.0 / 655.4 | 429.0 / 634.4 | 598.7 / 651.5 |
| stage scoring | 367.0 / 475.8 | 352.4 / 550.9 | 378.7 / 553.2 |
| stage cold_start_diagnostics | 0.0 / 0.0 | 0.0 / 0.0 | 104.7 / 146.0 |
| stage price_sampling | 0.0 / 0.0 | 0.0 / 0.0 | 2.7 / 3.2 |
| stage final_selection | 4.4 / 6.3 | 4.8 / 7.9 | 4.6 / 6.6 |
| stage final_gate | 181.9 / 216.5 | 186.2 / 214.5 | 181.0 / 198.3 |
| stage response_assembly | 8.9 / 13.5 | 9.6 / 14.1 | 10.2 / 12.5 |
| rows transferred (P50) | 21431 | 0 | 0 |

| outcome | no_cache | pool_cache | cache_cold_start |
|---|---|---|---|
| pool cache outcomes | bypassed 30 | built 6, reused 24 | built 6, reused 24 |
| selection modes | near_tie_sampling 30 | near_tie_sampling 30 | cold_start_exploration 30 |
| responses with exit_conditions_met | 0 | 0 | 0 |
| pool coverage (shown / pool) | 0.0022 | 0.0022 | 0.0028 |
| distinct shown / min(pool, slots) | 0.157 | 0.157 | 0.200 |
| exposure Gini incl. zero-exposure | 0.9989 | 0.9989 | 0.9985 |
| exposure Gini shown only | 0.507 | 0.507 | 0.462 |
| score loss vs top-K, relative mean / P95 / max | 0.0027 / 0.0047 / 0.0060 | 0.0027 / 0.0047 / 0.0060 | 0.0065 / 0.0104 / 0.0135 |
| score loss per item, absolute mean | 0.0018 | 0.0018 | 0.0042 |
| SIMULATED match of shown (mean) | 0.144 | 0.144 | 0.124 |
| correctness checks passed | 30/30 each of 7 | 30/30 each of 7 | 30/30 each of 7 |

## Comparison with the pre-P7 provisional settings (same script, `p7/evaluation-pre-p7-settings.json`)

Under iqr / epsilon 0.02 the correctness checks also all passed. The timing picture is the same
(that run partly overlapped with calibration runs on the same machine, so its timings are less clean):
rounds 2–5 P50 with the cache were 341 / 528 / 485 / 1,422 ms. Cold start exited in round 1 for
budget_bedrooms in 12 of 30 responses, against 24 of 30 after P7 (smaller tie groups at
epsilon 0.01). Mean relative score loss without cold start was 0.16 % / 0.02 % / 7.1 % / 0.67 %,
against 0.05 % / 0.04 % / 1.8 % / 0.27 % after P7. The property_type case (no soft criteria,
behaviour-only scores ≤ 0.4) lost most at epsilon 0.02 because its tie group spanned a large
relative score range.

## Limits

- Simulated users only: the exposure, coverage and match figures depend on the simulator's
  rules. They show how the selection mechanisms behave, not how real users respond.
- 6 sessions × 5 rounds per scenario; P95 over 30 values per mode and scenario (24 for rounds
  2–5) is a coarse estimate.
- One machine, live services running concurrently (not isolated). Absolute milliseconds are
  indicative; the relative mode comparison is what the interleaving protects.
- The fingerprint query scans every RENT row server-side on every hit and in every final gate
  (about 150 ms each, by design: R5).
