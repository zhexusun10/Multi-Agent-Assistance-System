# Recommendation relevance evaluation - Revision 3

This publication contains the English preliminary report, scoring workbook,
scenario-level metrics and the complete original evidence for the 23 October
2026 offline recommendation evaluation. Overall system acceptance remains
**PENDING**. Publication is for review; production behavior is unchanged.

## Start here

| Material | Contents |
| --- | --- |
| [English preliminary report](Group42_Offline_Recommendation_Evaluation_EN.pdf) | Eight pages: all cutoffs, per-scenario results, definitions, findings and limits. |
| [Comparison and scoring workbook](Group42_K_Comparison_EN.xlsx) | Nine cutoffs, 180 scenario/K records and 577 frozen rating records. |
| [Aggregate metrics](Aggregate_Metrics.csv) | Unrounded Precision, judged-pool Recall and NDCG. |
| [Scenario metrics](Per_Scenario_Metrics.csv) | Numerators, denominators, missing slots and explicit N/A reasons. |
| [Marginal results](Marginal_Results.csv) | Relevance of each additional position from five through ten. |
| [Top-ten IDs and grades](Top10_Rankings.csv) | All 173 analyzed positions and their static hard-condition checks. |
| [Expanded ratings](Expanded_Ratings.csv) | All 577 labels, reasons, provenance and source-review flags. |
| [Human review template](Human_Review_Template.csv) | 577 blank human-review fields; no human approval is implied. |
| [Scenarios](Scenarios.json) and [coverage](Scenario_Coverage.csv) | Same 20 requests; 25-31 judged candidates per scenario. |
| [Source manifest](Source_Manifest.json) and [verification](Verification.json) | Pinned version/snapshot hashes and independent calculation checks. |

## Results

| K | Precision | Judged-pool Recall | NDCG |
| --- | --- | --- | --- |
| 1 | 94.44% | 9.55% | 1.0000 |
| 3 | 94.44% | 28.64% | 1.0000 |
| 4 | 93.06% | 36.23% | 1.0000 |
| 5 | 92.22% | 43.82% | 1.0000 |
| 6 | 91.67% | 51.41% | 0.9966 |
| 7 | 89.68% | 57.62% | 0.9924 |
| 8 | 88.89% | 64.23% | 0.9956 |
| 9 | 87.65% | 70.18% | 0.9942 |
| 10 | 86.67% | 76.14% | 0.9964 |

Precision and NDCG are macro means over 18 primary scenarios; Recall uses 17,
excluding S03 because its judged pool has no grade-2/3 item. S19 and S20 are
controls. Grade >= 2 counts as relevant. U is unknown, not a negative. Empty
positions receive no credit in fixed-K Precision. K=2 was not requested.

The sixth position adds 16 relevant results out of 17, matching the fifth.
The seventh adds 14 of 17. Compare five and six as candidate default display
sizes, prioritizing validation of six, with optional expansion to ten. This
is a provisional product choice, not a demonstrated universal optimum.

## Frozen scope and limits

- **Tested source:** `d5af7e1b58b2e7064bf781f2a7a2eb19afc6de57`.
- **Snapshot:** 9 October 2026; 21,431 rental records.
- **Judgments:** 577 scenario-listing pairs across 431 distinct listing IDs;
  505 numeric grades and 72 U labels. Revision 3 adds K=6,7,8,9 without changing labels.
- **Execution:** the original structured, scripted native service returned up
  to four recommendations. Larger K values are prefixes of the saved internal
  full ranking. No larger HTTP/service response, live model or browser flow is
  claimed as tested. The production response limit remains four.
- **Provenance:** grades were assigned by one language model assistant using
  source facts and the documented rubric. The assessor also prepared the
  benchmark. No independent human relevance round has been completed.
- **Recall scope:** the fixed expanded judged pool, not all 21,431 database rows.
- **Verification:** 540 case/metric cells and 27 macro results independently
  reconciled. All 577 labels and earlier cutoff results are preserved.

This publishing branch starts from newer main `c7dc536`. The scores describe
the pinned tested version above; they are not a regression run of subsequent
main changes. The PDF is the frozen pre-publication report. Its publication
wording describes preparation time; this README and the PR carry upload status.
No production code changes or paid model requests accompany this publication.

## Complete original evidence

The original **128,892,483-byte ZIP** contains **220 files**, including complete
native traces, all input/label files, reproduction scripts and preserved report
revisions. Its bytes are stored across seven ordered parts in this directory.
Each part is below the [GitHub browser-upload limit](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository).

Clone this branch (or download all seven `.part001`-`.part007` files together
with `evidence_parts.json` and `restore_evidence.py`). In this directory run:

```sh
python restore_evidence.py
```

The script validates each part, reconstructs the exact ZIP under `restored/`,
and checks its SHA256 before declaring success. It uses Python's standard
library and no network. On systems where Python is named `python3`, use that
command instead. To check parts without creating a ZIP:

```sh
python restore_evidence.py --verify-only
```

Expected original ZIP SHA256:

```text
c987a84e6d0f8b8065a4861cd333ff89e3bc173a8c3dd375c8141f62abf5e397
```

[Part manifest](evidence_parts.json) records order, byte counts and hashes.
After extraction, start with `START_HERE_EN.txt`. `history/v1/` preserves all
154 original-run files. `history/v2/` archives 24 previous publication/calculation
files while 169 unchanged files remain shared; `Revision_Manifest.json` maps all
193 revision-2 files. The original 1 GB database is not duplicated.

## Earlier preparation packages

- [Original 20 x 25 benchmark and English scoring form/guide](Group42_Recommendation_Relevance_2026-10-23.zip).
- [Original 500-label first-pass scoring package](Group42_AI_Assisted_Ratings_2026-10-23.zip).

These are preserved historical packages. Use the current workbook and expanded
ratings above for the latest 577-label, nine-cutoff analysis.

## Remaining acceptance work

Independently validate the labels, verify source unit/scope flags and evaluate
held-out needs. A future increase in production result count requires contract,
final-gate, model-flow and UI acceptance. Earlier project acceptance items remain
tracked in the [main QA entry](../README.md); these evaluation results do not
close them or represent real-user satisfaction.
