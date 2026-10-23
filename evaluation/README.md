# Testing and acceptance

Group 42 QA handoff, publication edition updated on 22 October 2026 in [PR 2](https://github.com/zhexusun10/Multi-Agent-Assistance-System/pull/2).
Overall system acceptance is **PENDING**. The recommendation fix and original
evidence are published in PR 2; use that PR for the current merge status.

The [pre-merge review record](pr-2-review-2026-10-22.json) documents the independent
code review, one corrected Chinese comparison regression and six added cases.
After that correction, TypeScript passed **174 tests / 19 skipped** and Python
passed **212 tests / 7 skipped**. The Python run deliberately omitted live
database and local-snapshot checks. Both typechecks, build and provenance passed.
No real model call was made during this review. The original 44-case execution
record remains unchanged and was not rerun in this pre-merge phase.

## Recommendation relevance evaluation - 23 October 2026

The [Revision 3 relevance evaluation](recommendation-relevance-2026-10-23/README.md) provides
the English report, workbook, 577 frozen ratings and complete raw evidence for
K=1,3,4,5,6,7,8,9,10. It uses 20 scenarios and the 9 October rental snapshot.
The source under test is pinned at `d5af7e1`; this is an offline ranking analysis,
not a live-model test of later main changes or a human acceptance signoff.

## Read these first

| Material | Purpose |
| --- | --- |
| [English publication report v3.7](recommendation-fit-2026-10-21/01_Latest_Testing_and_Acceptance_Report.docx) | Recorded results, failure analysis and the 22 October publication snapshot. Its pre-merge wording is retained; use PR 2 for the current merge status and the review record above for later checks. |
| [Acceptance tracker](recommendation-fit-2026-10-21/02_Latest_Acceptance_Tracker.xlsx) | Recorded cases, defects, evidence references and outstanding acceptance work. Its original v3.6 execution record is unchanged. |
| [140-case inputs and results](acceptance-history/README.md#the-140-case-extension) | Original extension catalogue, per-suite receipts and five failed cases. |
| [Recommendation repair and retest](recommendation-fit-2026-10-21/README.md) | Four repaired issue groups, original 36/44 baseline, current 44/44 retest and supporting regressions. |
| [Complete original evidence package](acceptance-history/Group42_Latest_Acceptance_8913189.zip) | The primary raw-evidence download: 303 original files, including provider traces, executed harnesses, SQL/graph checks, browser screenshots and both recommendation runs. |

The comprehensive ZIP is the unchanged original v3.6 evidence package. Its
embedded report and tracker describe their recorded execution time. The direct
report above is the v3.7 publication edition. Keep the extracted archive root
intact when following `evidence/` and `tools/` paths in either report or tracker.

## Recorded outcomes

| Profile | Result | Interpretation |
| --- | --- | --- |
| 140-case extension | **135 passed / 5 failed** | 60 intent, 24 knowledge, 18 reply, 32 HTTP and 6 process outage/recovery checks. XI20, XK23, XR05, XA08 and XA09 remain failed in the original receipts. |
| Recommendation fit | **36/44 before; 44/44 after repair** | The same 24 structured, 8 projection and 12 deployed dialogue cases were repeated. All eight previous failures now pass. |
| Supporting code regressions | **26 passed** | 19 TypeScript intake, 3 controller and 4 provenance checks; separate from the 44-case catalogue. |
| Original remediation regression profiles | TypeScript **168 passed / 19 skipped**; Python **216 passed / 3 skipped** | Recorded test-time profiles; typechecks, build and upstream provenance also passed. |
| Pre-merge review regressions | TypeScript **174 passed / 19 skipped**; Python **212 passed / 7 skipped** | Six added comparator cases pass. This offline profile skips live databases and absent local snapshots; it does not replace the original 44-case real-data/model evidence. |
| Human UAT and signoff | **NOT_RUN / PENDING** | Automated browser checks are not participant acceptance. |

These profiles overlap and must not be added into a single accuracy or success
rate. Authored dialogues and synthetic cases do not estimate population accuracy
or user satisfaction. Original failures, skipped checks and limits remain visible.

## What remains before final acceptance

- Check the integration outcome in PR 2 and complete the post-merge application smoke test.
- Resolve and retest the earlier open issue groups **DEF04 to DEF08**. The
  recommendation changes verify DEF09 to DEF12 locally and do not close those earlier issues.
- Complete participant UAT and relevance review using real participants.
- Agree and verify performance acceptance thresholds; complete Windows verification.
- Record the team's final acceptance decision and signoff.

External actions in these QA profiles remained simulated. Human response and
signature fields remain unfilled. The earlier publication cleanup ran no new
product tests or model calls. The separate pre-merge review above adds offline
regression checks without altering historical outcomes.

## Raw evidence and historical archive

Use the [phase and evidence guide](acceptance-history/README.md) for the earlier
data, retrieval, model, browser, fault and performance records. Key JSON receipts
and screenshots remain directly browsable even when also present in the full ZIP.

The [historical archive](archive/README.md) contains the initial design pack,
earlier-source repository QA, six report snapshot sets and the original focused
download packages. All seven original published ZIPs retain their exact bytes;
six secondary packages have moved into the archive. This simplifies the main
reading path without discarding original delivery packages or rewriting history.

Use [the preservation and file-hash inventory](acceptance-history/EVIDENCE_MANIFEST.json)
to map earlier paths to current paths and verify retained files. Historical
publication metadata is preserved under `archive/publication-v1/`. Older reports
and the original execution tracker describe their original dates and scopes.

The repository's existing team reports elsewhere in `evaluation/` remain as
original project context; inherited team evidence is not counted as a new QA run.
