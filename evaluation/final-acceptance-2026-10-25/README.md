# Final testing and acceptance — 25 October 2026

**Final status: COMPLETED FOR THE AGREED PROJECT SCOPE.** Revision 3 adopts
the requester-confirmed acceptance protocol. Start with the English final report
and workbook below. Return to the [QA handoff](../README.md) for the full index.

| Material | Purpose |
| --- | --- |
| [Final English report](Group42_Final_Test_Acceptance_Report_EN.pdf) | Eleven-page final acceptance report, recorded metrics and scope. |
| [Editable report](Group42_Final_Test_Acceptance_Report_EN.docx) | The same final report in Word format. |
| [Final results workbook](Group42_Final_Acceptance_Results_EN.xlsx) | Eight sheets with retained grades, rankings, formulas and complex-case history. |
| [K=1–10 metric CSV](Final_TopK_Metrics_EN.csv) | Unchanged Precision, judged-pool Recall and NDCG for all three cohorts. |
| [Acceptance decision](Acceptance_Decision_EN.json) | Confirmed scope, completed disposition and retained limits. |
| [Current-main compatibility receipt](Compatibility_Verification_EN.json) | Eight passing offline tests and 27 unchanged native core-file identities. |
| [Final file validation](Final_Validation_EN.json) | Formula, numeric-cache, document and visual checks. |
| [Evidence references](Evidence_References_EN.json) | Immutable original archive link, exact size and hashes. |
| [Checksums](SHA256SUMS.txt) | SHA256 for the other nine files in this folder. |

## What is completed

Final acceptance covers the 40 registered, dataset-supported structured native
scenarios and their expected outcomes; frozen recommendation judgments;
Precision, judged-pool Recall and NDCG at K=1–10; recorded source-condition
checks; and current saved-result display compatibility. Participant UAT,
independent human relevance scoring and separate human signatures are not
prerequisites under the confirmed final protocol. Frozen labels remain the
final reference; optional override fields remain solely for reproducibility.

The live ranking benchmark remains pinned to main `4d687e3` and the 9 October
snapshot of 21,431 rentals. It contains 1,382 scenario/listing judgments. Metrics
average 36 ranking scenarios for Precision/NDCG and 35 for pool Recall; four
other scenarios are controls. At K=5, Precision is 90.56%, pool Recall 37.33%
and NDCG 0.9830. At K=10, the values are 85.00%, 59.32% and 0.9541. Pool Recall
is not full-corpus Recall. Cohort labeling methods differ as documented.

The current-main review is pinned to `35304d0`. Eight deterministic offline
tests pass, and 27 selected native core files match the recorded live source.
Saved-result continuation preserves the first batch, original order and data.
Legacy sessions without a reserved tail may refresh from saved requirements
and can return different identities. This finalization adds no paid model calls
and makes no claim of a new complete live-provider or browser run.

## Historical evidence remains unchanged

The 60-case, 116-message complex study is a separate, non-blocking experiment.
Its historical 39 completed and 21 failed final operations remain recorded.
Dataset-unverifiable needs are limitations; recorded timeout, validation,
provider and explanation failures remain technical observations. They have
not been rewritten as passes or attributed wholesale to missing listing data.
The historical local timeout patch was not integrated by this publication.

The [original 251-file evidence ZIP](../expanded-final-acceptance-2026-10-24/Group42_Expanded_Final_Acceptance_2026-10-24.zip)
retains all original inputs, judgments, responses, scripts, failures and prior
report editions. It remains 18,256,502 bytes with SHA256
`3befb560e837c2d8ce1f1e45013316361c386b5f4b9a347e4c8025e8bd76fe84`.
The [24 October guide](../expanded-final-acceptance-2026-10-24/README.md) is
historical; its frozen reports retain their original deferred status. All ten
original whole archives and seven evidence parts are preserved. Earlier
140-case and 44-case results remain in the [historical archive](../archive/README.md).
Overlapping suites must not be summed into one pass rate.

From the repository root, verify preserved material without network or models:

```sh
python3 evaluation/verify_material_preservation.py
```

After extracting the original 251-file ZIP, run its
`reproduction/verify_expanded_metrics.py` to reproduce saved metrics offline.
Publication uses the repository's pull-request workflow; a published branch
does not itself establish that it has been merged into main.
