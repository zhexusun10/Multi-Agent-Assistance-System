# Consolidated recommendation and complex-scenario evaluation

**Historical evidence edition, recorded 24 October 2026.** Its frozen reports
retain the original DEFERRED decision. The current decision is **COMPLETED FOR
THE AGREED PROJECT SCOPE**, issued 25 October; use the
[final report and workbook](../final-acceptance-2026-10-25/README.md) or
[QA handoff](../README.md). Basic live results below remain pinned to main
`4d687e3b27e016f622dd487909fc99f56096265c` and the 9 October listing snapshot.

| Material | Contents |
| --- | --- |
| [English final report](Group42_Expanded_Final_Test_Acceptance_Report_EN.pdf) | Eleven pages: ranking metrics, complex failures and acceptance limits. |
| [Editable report](Group42_Expanded_Final_Test_Acceptance_Report_EN.docx) | The same frozen report in Word format. |
| [Results workbook](Group42_Expanded_Recommendation_And_Complex_Results_EN.xlsx) | Eight sheets: scenarios, judgments, formulas, repeats and complex tracker. |
| [Metric CSV](Final_TopK_Metrics_EN.csv) | K=1–10 for the combined, retained and extension cohorts. |
| [Complete original evidence ZIP](Group42_Expanded_Final_Acceptance_2026-10-24.zip) | All 251 original files, including raw receipts, scripts and two prior archives. |
| [Current file checksums](PUBLICATION_SHA256SUMS.txt) | SHA256 for the six other files in this folder. |

## Reading the results

There are 40 basic scenarios and 1,382 scenario/listing judgments: 619 unchanged
prior records, 600 pre-run extension pairs and 163 returned identities assigned
their frozen source grades. Labels comprise 1,274 numeric grades and 108 unknowns.
Precision and NDCG average 36 ranking scenarios; Recall averages 35 because S03
has no relevant item in its judged pool. S19, S20, S34 and S36 are controls.
Grades 2–3 count as relevant; an unverified requirement stays unknown. Missing
positions receive no fixed-K Precision credit. Each K is a prefix of the actual
native top-ten response, not a combination of separate calls.

At K=5 the combined results are **90.56% Precision, 37.33% judged-pool Recall
and 0.9830 NDCG**. At K=10 they are **85.00%, 59.32% and 0.9541**. The CSV and
workbook provide all cutoffs and denominators. Judged-pool Recall is not
full-corpus Recall. The extension's separate known-relevant source Recall at
K=10 is 24.13%. The cohorts use different documented labeling methods;
their aggregate describes this expanded sample, not a code improvement.

The retained complex suite uses the same main commit plus a **local-only timeout
patch**. It records 39/60 final-operation completions, 34/60 every-turn completions
and 83/116 completed turns; 21 final failures remain. Most completed outcomes
were clarifications or unsupported-criterion responses. Six cases returned fresh
lists and then failed explanation. The 65% final-operation rate is therefore
a flow diagnostic, not end-to-end recommendation acceptance.

## Finding the raw files

Extract the ZIP and start at `START_HERE_EN.txt`. Paths below are inside its
top-level `Group42_Expanded_Final_Acceptance_2026-10-24/` directory:

| Path inside the ZIP | Evidence |
| --- | --- |
| `metrics/Combined_Scenario_Register_EN.json` | All 40 basic requests and requirements |
| `metrics/All_Ratings_EN.json` | All judgments, reasons and provenance |
| `metrics/Returned_Rankings_EN.json` | Returned identities and source checks |
| `metrics/Expanded_Metrics_Summary_EN.json` | Metric definitions and denominators |
| `evidence/Complex_Case_Tracker_EN.json` | All 60 complex outcomes |
| `evidence/Complex_Execution_Summary.json` | Recorded completion and turn counts |
| `reports/Independent_Verification_EN.json` | Arithmetic and source verification |
| `reproduction/verify_expanded_metrics.py` | Saved-result verification without model calls |

Only the 20 extension scenarios, four setup requests and eight extra repeats
were newly executed: 32 native requests and 158 provider requests, all HTTP 200;
provider-reported cost USD 0.079645074. Retained basic and complex results were
reused. This cleanup makes no model calls and changes no recorded metric.

The evidence was merged in
[PR 3](https://github.com/zhexusun10/Multi-Agent-Assistance-System/pull/3).
Frozen report and ZIP bytes retain their original pre-publication wording
and historical acceptance decision. The [25 October final decision](../final-acceptance-2026-10-25/Acceptance_Decision_EN.json)
supersedes this edition for the agreed scope. Participant UAT and independent
human scoring/signatures are no longer final prerequisites; the complex study
is retained as a non-blocking experiment with its original failures unchanged.

The original ZIP is 18,256,502 bytes. SHA256:
`3befb560e837c2d8ce1f1e45013316361c386b5f4b9a347e4c8025e8bd76fe84`.
