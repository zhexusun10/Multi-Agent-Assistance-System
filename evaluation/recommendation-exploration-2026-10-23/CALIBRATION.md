# Recommendation v2: parameter calibration (P7, Step 10)

Per-key decisions are in TUNING.md; this page records the method, the data, the results and the
limits. Raw output: `p7/calibration.json` (committed settings after P7) and
`p7/calibration-pre-p7-settings.json` (same analyses run under the pre-P7 provisional settings,
iqr and epsilon 0.02, kept so the change can be compared).

## Method

- Entry point: `recommendation/analysis/` (new, offline; `python -m recommendation.analysis.calibrate
  [--data database|fixture] [--config FILE] [--sections ...] [--out FILE]`). Nothing in the
  service, gates or terminal imports it (test `test_runtime_modules_never_import_the_analysis_package`).
- Data access is read-only and goes through the production code. `analysis/live.py` opens the
  same repositories as the service (PostgreSQL `SET TRANSACTION READ ONLY` REPEATABLE READ, Neo4j
  READ sessions) and reads the 21,431 RENT rows once. Each scenario pool is built with
  `tools/pool_builder.build_content` (production partition, native resolution and hard-geo
  evaluation; cache not involved) and scored with `ranking/scoring_v2.score_pool` after the
  rank-stage `evaluate`. Cold-start and selection metrics call `ranking/selection.py` /
  `exploration.py` directly.
- Only the trace-size and resolution-budget sections run the service. They use the
  ScriptedController, or an offline fake model (`Narrower`, a LiveController subclass that never
  calls an LLM), with TEMPORARY SQLite state files. Settings are varied in memory with
  `with_overrides`; the YAML is never written (the fixture-mode test checks its bytes).
- Fixed seed 20261023 for every RNG of the analysis. Re-running gives the same figures, apart
  from timings.
- Data basis, stated in every analysis section of the JSON (pools: per scenario). Correction (P7 review): the
  recorded `p7/calibration.json` lacks the label on its `cache` section; the code now emits
  `real (fingerprint timing, pool content sizes) + design (TTL argument: no TTL experiment)` there and a
  fixture-mode test checks every section. The recorded run was not repeated for this label alone.
  - **real**: statistics of the live table, the catalog, or pools built by production code.
  - **real listings, synthetic references**: no real feedback exists, so behaviour references
    are drawn by the fixed-seed RNG. A "previous round" of 10 listings comes from the pool's top
    30, with log-normal durations around 30 s and one 0 s record; 3 appointments come from the
    top 100. These sections describe how the scorer behaves on real attributes. They say nothing
    about user preference or satisfaction.
  - **design**: closed-form curves (browsing weight), no data.
- Run on 2026-10-23 against the live services (no restart): `.venv/bin/python -m
  recommendation.analysis.calibrate --out evaluation/recommendation-exploration-2026-10-23/p7/calibration.json`
  took 3 min 27 s. Settings identity: config_hash `e46e754a9c6e…`, selection_hash `90e724bb160a…`.

Scenarios (`analysis/scenarios.py`): 9 hard-criteria scenarios × 3 soft sets (`no_soft`;
`district`: soft district in D05/D10/D15/D19; `mixed`: soft price minimize, soft bedrooms ≥ 2
and soft MRT proximity ≤ 800 m with weight 2), each with and without synthetic references. The
values were fixed before any result was looked at.

## 1. Pool sizes for representative hard criteria (real)

| scenario | source | pool | excl. fail | excl. unknown | build ms | price p05 / p50 / p95 | distinct prices | price > Tukey far fence | missing bedrooms / floor area / coordinates |
|---|---|---|---|---|---|---|---|---|---|
| none (no hard criterion) | 21431 | 21431 | 0 | 0 | 67 | 800 / 3499 / 18800 | 840 | 1076 | 0.03 / 0.39 / 0.60 |
| budget ≤ 3000 | 21431 | 9793 | 11638 | 0 | 118 | 750 / 1200 / 2900 | 296 | 0 | 0.05 / 0.85 / 0.40 |
| budget ≤ 3000 + bedrooms ≥ 2 | 21431 | 306 | 20680 | 445 | 135 | 2300 / 2984 / 3000 | 32 | 0 | 0.00 / 0.00 / 0.09 |
| budget ≤ 4000 + bedrooms ≥ 2 + district D15/D16 | 21431 | 223 | 21126 | 82 | 147 | 2900 / 3500 / 4000 | 37 | 0 | 0.00 / 0.00 / 0.44 |
| budget ≤ 3500 + property type HDB flat | 21431 | 5853 | 15578 | 0 | 186 | 680 / 1000 / 3400 | 187 | 684 | 0.00 / 0.80 / 0.00 |
| budget ≤ 4000 + ≤ 600 m to any MRT station | 21431 | 3698 | 12223 | 5510 | 466 | 750 / 1200 / 3800 | 198 | 0 | 0.02 / 0.70 / 0.00 |
| budget ≤ 5000 + ≤ 1000 m to LTA:MRT:BISHAN | 21431 | 121 | 14013 | 7297 | 435 | 850 / 1250 / 4300 | 47 | 0 | 0.02 / 0.64 / 0.00 |
| budget ≤ 4000 + inside URA:REGION:WEST | 21431 | 1834 | 14087 | 5510 | 416 | 650 / 1000 / 3800 | 152 | 193 | 0.01 / 0.79 / 0.00 |
| budget ≤ 4000 + ≤ 1500 m to a Hospital | 21431 | 4684 | 11237 | 5510 | 377 | 700 / 1200 / 3800 | 224 | 0 | 0.02 / 0.71 / 0.00 |

The partition invariant source = pool + fail + unknown holds in every row. Hard-unknown
exclusions come only from missing listing data: bedrooms missing, or "listing has no valid
coordinates (unknown; never 0 m)" for every geographic hard criterion. 5,510 listings have no
coordinates and do not already fail the budget. Pools range from 121 to 21,431 listings, so
every pool exceeds the result limit of 10.

## 2. Price quantiles, duplicates, outliers and missingness (real, full table)

- Price: p0 299, p1 600, p5 800, p25 1,300, p50 3,499, p75 5,600, p95 18,800, p99 40,000,
  max 128,000,000. 840 distinct values. The 10 most common prices (1,000 ×760, 1,200 ×702,
  900 ×655, 3,500 ×490, 1,100 ×487, …) cover 24.7 % of rows, and 98.6 % of rows share their price
  with another row. Outliers: 197 rows above p99, 1,076 above the Tukey far fence (18,500),
  81 above 50,000, 5 above 100,000, 2 above 1,000,000.
- Missingness: price 0 %, bedrooms 2.7 %, floor_area_sqft 39.0 %, property_type 0 %,
  district_code 0 %, coordinates 59.7 % (postal code 59.7 %). Bedrooms = 0 for 8,326 rows
  (rooms / studios).
- Consequences: price bins need duplicate-edge merging (§7), the numeric spread must be robust
  to the extreme tail (§3), and comparisons must tolerate missing floor area and coordinates
  (§4, §5).

## 3. Numeric normalisation schemes compared (real pairs; rank agreement with synthetic references)

Per pool and numeric feature: the spread under std (ddof 0), iqr and p05_p95; the share of
20,000 random pool pairs whose similarity max(0, 1 − |a − b| / spread) is 0 (saturated); the
median similarity; and the spread recomputed without the top 1 % divided by the full spread
(1 = insensitive to the tail).

| finding | std | iqr | p05_p95 |
|---|---|---|---|
| full-table price spread | 874,463 | 4,300 | 18,000 |
| price spread without top 1 % / full | 0.007 | 0.98 | 0.90 |
| median price pair similarity, full table | 0.997 (no discrimination) | 0.32 | 0.84 |
| pools where bedrooms has spread 0 (zero_variance) | 0 of 9 | 5 of 9 (budget, budget_bedrooms, budget_bedrooms_district, property_type, region) | 0 of 9 |
| saturated pairs (range over pools and features) | 0–54.5 % | 27–55 % | 1–31.5 % |
| floor_area spread without top 1 % / full | 0.08–0.99 | 0.94–1.04 | 0.95–1.0 |

Where bedrooms is not suppressed by a hard criterion, iqr actually drops it in 3 pools
(budget, property_type, region; `dropped_features` in the rank trace). The behaviour top 10 under
std or p05_p95 overlaps 0.1–1.0 with the iqr order, so the choice of scheme changes the result.
**Decision: p05_p95.** It is the only scheme that is never degenerate on these pools and is
robust to the 128 M listing.

## 4. Attribute missingness and comparison coverage (real listings, synthetic references)

Pair comparison coverage is the compared feature weight divided by the active weight. In pools
where geography or price is suppressed, coverage is 0.5–0.64 whenever floor area is missing.

| min_comparison_coverage | share of (candidate, reference) pairs unavailable |
|---|---|
| 0.4 | ≤ 0.7 % in every pool |
| 0.5 | ≤ 0.7 % in every pool |
| 0.6 | ≤ 3 % (budget pool), ≤ 0.7 % elsewhere |
| 0.7 | 80–90 % in property_type, mrt, station, region and amenity pools; 3 % budget; 0.7 % unfiltered |

**Decision: keep 0.5.** Higher values make most pairs unavailable because floor area is missing;
lower values change nothing on this data.

## 5. Similarity: numeric vs categorical contribution, geography (real listings, synthetic references)

Per active feature: comparison rate, std of the feature similarity, and the "drive share"
(normalised weight × std × comparison rate, i.e. how much of the pair-similarity variation the
feature can move). Also the top-10 overlap of the behaviour order when the feature is removed
(1 = no influence).

| pool | numeric (price/bedrooms/floor area) | categorical (property_type) | geographic | note |
|---|---|---|---|---|
| none | 0.43 | 0.38 | 0.19 | removing geography changes the whole behaviour top 10 (overlap 0.0), property_type 0.1, price 0.9 |
| budget | 0.37 | 0.46 | 0.18 | price suppressed (hard); floor area compared in 5 % of pairs |
| budget_bedrooms | 0.36 | 0.38 | 0.26 | price, bedrooms suppressed |
| budget_bedrooms_district | 0.33 | 0.67 | 0 | geography suppressed (hard district) |
| property_type | 0.66 | 0 | 0.34 | property_type suppressed (hard) |
| mrt / station / region / amenity | 0.42–0.50 | 0.50–0.58 | 0 | geography suppressed (hard distance) |

- property_type is a 0/1 feature and dominates wherever price and geography are suppressed.
  Removing it changes 10–100 % of the behaviour top 10. Feature weights stay provisional
  (preference weights need real feedback); this is recorded as a known effect.
- District fallback: 60 % of listings lack coordinates. Without the fallback, geography is
  unavailable for 90 % of pairs (unfiltered pool) and 60 % (budget pool), and 57 % of budget-pool
  pairs become unavailable altogether. With it: 0 %. **Keep true.**
- Pairwise straight-line distances between pool listings with coordinates: median 11.1 km city-wide
  (p5 2.0 km, p95 21.4 km), 4.8 km inside the West region, 0.77 km inside the Bishan 1 km pool.
  At geographic_scale_m 3000, 91.5 % of city-wide pairs get geographic similarity 0, 72 % in
  the region pool and 0 % in the Bishan pool. Data cannot say what "near" means to a user, so the
  existing default is kept.

## 6. Browsing weight curves (design; no dwell data exists)

w(t) = log1p(t / scale), normalised over the references of a round.

| scale s | w(5 s) / w(60 s) (glance vs read) | w(600 s) / w(60 s) (long dwell) | w(240) − w(120) |
|---|---|---|---|
| 5 | 0.27 | 1.87 | 0.67 |
| 10 | 0.21 | 2.11 | 0.65 |
| 20 | 0.16 | 2.48 | 0.62 |
| 30 (kept) | 0.14 | 2.77 | 0.59 |
| 60 | 0.12 | 3.46 | 0.51 |
| 120 | 0.10 | 4.42 | 0.41 |

Every scale keeps increasing beyond 120 s (plan rule). A larger scale discounts glances more and
lets long dwell count relatively more. Nothing in the data can decide this, so the value is **not calibrated** (it stays `provisional`, a
pending user decision in OPEN_ITEMS.md) until real dwell times exist.

## 7. Price bins and target sample size (real prices and scores; selection code, 100 seeds per cell)

The grid is bins {2, 3, 4, 5, 6, 8, 10} × target {10, 20, 30, 40, 60}, over 36 pool/soft/reference
combinations with cold-start selection forced. Metrics: effective bins (duplicate edges merged),
share of non-empty R bins that get a displayed listing, displayed price span relative to R's,
and score loss of the shown set vs the deterministic top 10.

- Effective bins: 5 → 4.78 on average (duplicate prices merge edges); 10 → 9.1.
- Tied pools (no_soft / district, R = the tie group): share of R bins displayed is 1.0 / 0.97 at
  5 bins against 0.94–0.97 / 0.85–0.88 at 10 bins. Displayed price span 0.63 → 0.66 from 5 to
  10 bins. Score loss 0 (all tied) up to target 40.
- Distinguishable scores (mixed): R = target. Mean score loss 0.2–0.35 % at target 10,
  0.5–0.8 % at 20, 0.8–1.1 % at 30, 1.0–1.4 % at 40, 1.3–2.0 % at 60 (max 3.1 %). The share of R
  bins displayed falls as the target grows (0.67 at 5 bins / target 30): a larger R spans more
  bins than m = 7–10 drawn items can fill.
- **Decisions.** quantile_bins 5 is kept: more bins mostly add bins that get no slot (the
  largest-remainder effect in OPEN_ITEMS). target_sample_size 30 stays the confirmed initial
  value; the measured cost is about 1 % mean score loss.

## 8. Cutoff ties, epsilon, B, S and stability trials (real listings; synthetic references where marked)

Top-of-order structure (selection of rows; all 54 in the JSON). B is |G| at the cutoff (K = 10).

| case | soft p0 / p50 / p100 | top-1 to top-10 span | exact ties at s_K | B at ε 0.01 | B at ε 0.02 |
|---|---|---|---|---|---|
| any pool, no soft criteria, no references | – | 0 | whole pool | whole pool | whole pool |
| none / district / no refs | 0 / 0 / 1 | 0 | 6,431 | 6,431 | 6,431 |
| none / mixed / refs | 0.00 / 0.43 / 0.96 | 0.0078 | 2 | 10 | 12 |
| budget / mixed / refs | 0.21 / 0.46 / 0.96 | 0.0306 | 1 | 4 | 6 |
| budget_bedrooms / mixed / refs | 0.46 / 0.74 / 0.96 | 0.0402 | 1 | 2 | 9 |
| property_type / mixed / refs | 0.32 / 0.54 / 0.96 | 0.0253 | 1 | 10 | 18 |
| mrt_distance / mixed / refs | 0.49 / 0.60 / 0.96 | 0.0077 | 1 | 17 | 21 |
| station_distance / mixed / refs | 0.46 / 0.56 / 0.86 | 0.0562 | 1 | 2 | 4 |
| region / mixed / refs | 0.35 / 0.53 / 0.91 | 0.0409 | 1 | 6 | 9 |
| mrt_distance / no_soft / refs | – | 0 | 2,226 | 2,227 | 2,237 |

Without soft criteria and without references every score is equal, so B is the whole pool. With
only behaviour similarity and price/geography suppressed, the scores take few distinct values
(e.g. 2,226 exact ties in the MRT pool).

**Epsilon basis.** For every pair in the cutoff window (score ranks 5–30), we counted how often 20
perturbation trials at f = 0.2 swap the pair's order, bucketed by score gap:

| gap | (0, 0.0025] | (0.0025, 0.005] | (0.005, 0.01] | (0.01, 0.02] | (0.02, 0.03] | > 0.03 |
|---|---|---|---|---|---|---|
| flip rate, 18 mixed cases | 16.5 % (878 pairs) | 4.2 % (765) | 1.0 % (1,176) | 0.2 % (1,337) | 0.1 % (613) | 0 % |
| flip rate, all 54 cases | 9.3 % | 3.0 % | 1.0 % | 0.2 % | 0.1 % | 0 % |

Gaps above 0.005 keep their order in ≥ 99 % of trials. G = {|s_i − s_K| ≤ ε/2}, so **ε = 0.01**
groups exactly the scores within the unreliable gap, and 0.02 also grouped gaps of 0.005–0.01,
which are reliably ordered. The median per-item score change near the cutoff under the same
perturbation is 0.015 (mixed cases).

Exit reachability (C ≥ 0.8, B ≤ 5, S ≥ 0.9) over the 18 mixed cases: C = 1 in all 18 and
S ≥ 0.9 in all 18, so B decides. Exit in 18 / 16 / 11 / 7 / 2 / 0 cases at ε = 0 / 0.0025 / 0.005 /
0.01 / 0.02 / 0.03 (all 54 cases: 24 / 19 / 12 / 8 / 2 / 0). Without soft criteria C = 0, so no
case exits (R9 known effect).

**S vs perturbation fraction.** Median S over the 18 mixed cases at f = 0.05 / 0.1 / 0.2 / 0.3 is
1.0 / 0.995 / 0.975 / 0.955, with S ≥ 0.9 in 18 / 18 / 18 / 17 cases. f is not calibrated (stays `provisional`, pending user decision).

**Trials convergence** (30 independent seeds per trial count; reference 1000 trials):

| case (pool) | S ref | std(S) at 5 / 10 / 20 / 40 / 80 trials | exit decision agreement at 5 / 10 / ≥20 | ms per trial |
|---|---|---|---|---|
| none / mixed / refs (21,431) | 0.970 | 0.023 / 0.013 / 0.009 / 0.007 / 0.006 | 1.0 / 1.0 / 1.0 | 7.6–8.0 |
| budget / district / refs (9,793) | 0.061 | 0.033 / 0.022 / 0.014 / 0.012 / 0.010 | 1.0 / 1.0 / 1.0 | 3.3–5.0 |
| budget_bedrooms / mixed / refs (306) | 0.968 | 0.019 / 0.015 / 0.009 / 0.006 / 0.005 | 1.0 / 1.0 / 1.0 | 0.25 |
| same, pre-P7 settings run (306) | 0.954 | 0.035 / 0.027 / 0.021 / 0.014 / 0.009 | 0.93 / 0.97 / 1.0 | 0.7–1.1 |
| mrt / mixed / no refs; amenity / district / refs | 1.0 | 0 at every count | 1.0 | 0.6 / 1.6 |

**Decision: keep 20 trials.** 20 is the smallest count with full agreement in both runs; its
std(S) is ≤ 0.021 and it costs about 0.16 s on the largest pool (the evaluation measured the
whole diagnostics stage at P50 105 ms on that pool).

## 9. Cache TTL need (real timing + design argument; no TTL experiment)

The data version (SQL fingerprint over every RENT row: count, newest update, ordered row md5) is
checked on every cache lookup and again in the final gate, and catches updates and deletions (P3
tests). Measured fingerprint cost: P50 142 ms / P95 143 ms. A TTL therefore adds no freshness,
only earlier eviction. **ttl_seconds stays 0 (no TTL; the spec section 2 rule, labelled `confirmed`).** No separate TTL
experiment was run; the timing above is the only measurement. Pool content size (JSON): 10.1 MB for
the 21,431-listing pool, 0.1–4.7 MB for the others. This is input for the memory bounds, which
stay `provisional` (not calibrated: no memory budget or usage data).

## 10. Entity resolution budgets (real catalog; fake-model runs on live data, temporary state)

- Catalog: 188 LTA stations, 55 URA areas, 5 regions, 3,716 OSM places. 4,472 normalised name
  keys, of which 314 are shared by several entities (209 by 2, 40 by 3, 23 by 4, 9 by 5; chain
  brands up to 138). max_candidates 3 / 5 / 8 / 10 / 20 lists every candidate for 79.3 / 89.5 /
  93.3 / 93.9 / 96.8 % of the ambiguous keys.
- 3,260 display names through the production resolver: 3,037 resolved, 223 ambiguous (median
  2 candidates, p95 17). Narrowing policy simulated against the real search: 9 of the 223 can be
  narrowed to one candidate by kind/category. The informed policy (the model read the candidates)
  needs 1 search for 6 of them and 2 for the other 3. The naive policy (plain search first) needs
  3 for 6 and 4 for 3. The other 214 share kind and category (FairPrice ×138, Sheng Siong ×76,
  Kopitiam ×29, …) and can only be clarified by the user.
- Real runs (live data, temporary state): scripted path 4 tool calls / 0 resolution calls. Fake
  model: "Lee Kong Chian School of Business" 6 tool calls, 2 resolution calls (search + bind),
  5 model calls, success; "Wet Market" 7 / 3 / 6, success; "Bedok Mall" (not narrowable) 4 / 0 / 2,
  needs_clarification (never a first pick).
- **Decisions: keep** max_search_attempts 3, max_candidates 5 and max_resolution_tool_calls 6
  (two narrowable criteria). The turn and tool-call totals stay at their existing defaults;
  OPEN_ITEMS notes that two narrowable criteria handled strictly sequentially would need 9 turns.

## 11. Trace preview / debug limits (real runs, scripted, temporary state)

| setting | unfiltered pool + mixed soft | MRT pool + district soft |
|---|---|---|
| preview_rows 5 / 10 / 20 → stored trace | 75.8 / 88.2 / 111.3 KB | 70.7 / 82.7 / 105.6 KB |
| debug_max_rows 50 / 100 / 200 / 400 → debug part | 15.9 / 64.6 / 171.6 / 385.7 KB | 10.9 / 15.8 / 25.6 / 45.5 KB |
| longest string stored in a tool step | 152 chars | 91 chars |

Each extra preview row adds about 2.3 KB, independent of pool size. Debug rows are bounded by
debug_max_rows (the large tied pool fills the budget; the MRT pool does not). The longest live
title is 100 characters and the longest URL 285, so `trace.max_string_chars` 4000 (the former
TEXT_LIMIT, moved to the YAML) never cuts real data. **Decision: keep 10 / 200 / 4000.**

## Limits

- No real user feedback, dwell times or appointment histories exist. Every behaviour-similarity
  figure uses synthetic references, so feature weights, browsing scale and perturbation fraction
  stay `provisional` (not calibrated; user decision 2, a calibrated price weight, is not met yet), and nothing here measures recommendation quality or user satisfaction.
- The epsilon basis depends on the perturbation fraction (0.2, provisional). With another f the
  flip-rate table, and possibly epsilon, would move.
- Scenarios are a hand-picked representative set (9 hard × 3 soft), not a sample of real requests.
- The convergence cases were not selected near the S threshold. Agreement is lowest when S is
  near 0.9 (the pre-P7 run's 0.954 case is the closest).
- Timings come from this machine with the live services running and are indicative only.
- Data snapshot: the live database at the time of the run (21,431 RENT rows); the JSON records the
  settings identity, not the data fingerprint (P3 data_version is recorded per request in
  PERFORMANCE.md's runs).
