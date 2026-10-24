# Expanded recommendation and complex scenario acceptance results

The 24 October 2026 evaluation contains 40 basic scenarios and the retained
60-case complex suite. Basic recommendation results use unmodified main
`4d687e3b27e016f622dd487909fc99f56096265c` and the 9 October listing snapshot.
Overall system acceptance is **DEFERRED**: the complex suite still has 21 final
failures, and independent participant acceptance and relevance review remain open.

## Start here

| Material | Contents |
| --- | --- |
| [English final report PDF](Group42_Expanded_Final_Test_Acceptance_Report_EN.pdf) | Eleven pages covering basic metrics, failure analysis, complex recovery and acceptance limits. |
| [Editable English report](Group42_Expanded_Final_Test_Acceptance_Report_EN.docx) | The same report in Word format. |
| [Results and review workbook](Group42_Expanded_Recommendation_And_Complex_Results_EN.xlsx) | Eight sheets with all scenarios, judgments, ranking formulas, repeat trials and the complex tracker. |
| [All K and cohort metrics](Final_TopK_Metrics_EN.csv) | K=1–10 for the combined, retained and extension cohorts. |
| [Detailed metrics and denominators](Expanded_Metrics_Summary_EN.json) | Complete metric definitions, original-pool views and source-recall diagnostics. |
| [All 40 basic scenarios](Combined_Scenario_Register_EN.json) | English requests and canonical requirements. |
| [All 1,382 judgments](All_Ratings_EN.json) | Frozen grades, reasons and review provenance. |
| [Actual returned rankings](Returned_Rankings_EN.json) | Listing identities, grades and source checks. |
| [60-case complex tracker](Complex_Case_Tracker_EN.json) | Individual outcomes and remaining failures. |
| [Complex execution summary](Complex_Execution_Summary.json) | Recorded completion and turn counts. |
| [Independent arithmetic and source verification](Independent_Verification_EN.json) | 440 scenario/K rows, 90 aggregates, source fields and hard-condition checks. |
| [Complete original evidence ZIP](Group42_Expanded_Final_Acceptance_2026-10-24.zip) | All 251 original files, including raw responses, traces, frozen inputs, executed scripts and two preserved prior evidence archives. |
| [Publication manifest](Publication_Manifest_EN.json) and [file checksums](PUBLICATION_SHA256SUMS.txt) | Published scope, original archive identity and byte integrity. |

The direct files provide a short reading path. Full raw evidence is stored once
in the sealed ZIP; the 251 extracted files are not duplicated throughout this
repository. After extracting it, start at `START_HERE_EN.txt`. The archive's
`reproduction/verify_expanded_metrics.py` checks the recorded calculations and
source evidence without network access, model calls or file writes.

## Combined recommendation results

| K | Precision at K | Judged-pool Recall at K | NDCG at K |
| --- | --- | --- | --- |
| 1 | 97.22% | 12.80% | 1.0000 |
| 2 | 94.44% | 19.88% | 0.9939 |
| 3 | 93.52% | 26.97% | 0.9916 |
| 4 | 91.67% | 32.15% | 0.9877 |
| 5 | 90.56% | 37.33% | 0.9830 |
| 6 | 89.35% | 42.27% | 0.9749 |
| 7 | 88.10% | 46.74% | 0.9700 |
| 8 | 87.15% | 51.20% | 0.9645 |
| 9 | 86.11% | 55.35% | 0.9589 |
| 10 | 85.00% | 59.32% | 0.9541 |

Precision and NDCG use 36 ranking scenarios. Recall uses 35 because S03 has no
grade-2/3 item in its judged pool. S19, S20, S34 and S36 are controls. A grade
of 2 or 3 counts as a hit; an unverified condition remains unknown. Missing
positions receive no credit in fixed-K Precision. K=1–10 are prefixes of the
actual native top-ten response, without stitching separate calls or reranking.
These structured tests exercise the native recommendation module; they do not
measure natural-language intake, explanations or browser completion.

The common pool contains 619 unchanged prior records, 600 pre-run extension
pairs and 163 returned identities assigned their already frozen source grades:
1,274 numeric grades and 108 unknown labels. This is a provisional, schema-aware
extension, not an additional independent blind benchmark. The two cohorts have
different documented label-construction methods. Aggregating them describes
the expanded sample; it does not establish a code improvement. Judged-pool
Recall is not full-corpus Recall. The extension's separate known-relevant
source Recall at K=10 is 24.13%.

## Complex cases and remaining work

The retained complex evaluation uses the same main commit plus the local
timeout patch. It records 39/60 final-operation completions (65.00%), 34/60
every-turn completions (56.67%) and 83/116 completed turns (71.55%). Final
intake timeouts fell from 33 to 6. Thirty-five completed final outcomes were
clarification or unsupported-criterion responses. The six final cases that
showed fresh lists all subsequently failed explanation. The 65% figure is
therefore a flow-completion diagnostic, not full recommendation acceptance.

Twenty-one final failures remain across intake timeouts, semantic/schema
checks, a native contract rejection, provider availability and explanation.
The exact local timeout patch and before/after evidence are inside the ZIP.
The patch remains **local only**; this PR publishes evidence and documentation
and does not apply the patch to production source.

## Execution and publication status

Only the 20 new basic scenarios, four setup requests and eight additional
repeats were executed for the extension: 32 native requests and 158 provider
requests, all HTTP 200. Provider-reported usage was USD 0.079645074. The 20
retained basic results and 60 complex results were reused without a new run.
No model calls accompany this upload.

These materials are published for review in
[PR 3](https://github.com/zhexusun10/Multi-Agent-Assistance-System/pull/3),
on `codex/recommendation-evaluation-rev3`, and are not merged by this upload.
The sealed report, workbook and ZIP retain their evidence-cutoff wording,
including statements that the package had not yet been uploaded. This README
and the publication manifest carry the later GitHub publication state.
Earlier reports and failed receipts remain unchanged. Use the
[23 October evaluation](../recommendation-relevance-2026-10-23/README.md)
for the older `d5af7e1` run and the [main QA entry](../README.md) for prior
testing and unresolved acceptance work. Historical profiles overlap and must
not be summed into a single success rate.

The original ZIP is 18,256,502 bytes, with SHA256:

```text
3befb560e837c2d8ce1f1e45013316361c386b5f4b9a347e4c8025e8bd76fe84
```
