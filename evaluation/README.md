# Group 42 testing and acceptance handoff

**Acceptance: DEFERRED.** The latest consolidated evidence covers 40 basic
recommendation scenarios and a retained 60-case complex suite. Start with the
[English final report](expanded-final-acceptance-2026-10-24/Group42_Expanded_Final_Test_Acceptance_Report_EN.pdf)
and [results workbook](expanded-final-acceptance-2026-10-24/Group42_Expanded_Recommendation_And_Complex_Results_EN.xlsx).

| Need | Read |
| --- | --- |
| Current results, scope and remaining work | [24 October evaluation guide](expanded-final-acceptance-2026-10-24/README.md) |
| Precision, judged-pool Recall and NDCG for K=1–10 | [Current metric CSV](expanded-final-acceptance-2026-10-24/Final_TopK_Metrics_EN.csv) |
| Inputs, judgments, model receipts, scripts and complex failures | [Original 251-file evidence ZIP](expanded-final-acceptance-2026-10-24/Group42_Expanded_Final_Acceptance_2026-10-24.zip) |
| Earlier tests, original failures and superseded reports | [Historical archive guide](archive/README.md) |
| Locate any pre-cleanup file and verify its bytes | [Preservation manifest](CLEANUP_MANIFEST_EN.json) and [read-only verifier](verify_material_preservation.py) |

## What these results establish

The basic benchmark uses **unmodified main `4d687e3`**, 21,431 rental listings
from the 9 October snapshot, 40 scenarios and 1,382 scenario/listing judgments.
Precision and NDCG average 36 ranking scenarios; judged-pool Recall averages
35. Four other scenarios are controls. The structured native recommendation
test does not cover natural-language intake, explanation or browser completion.
Independent human relevance review remains open. The extension is schema-aware;
it is not a new blind benchmark. Cohort methods and pool limits are in the report.

The complex suite uses the same source **plus a local timeout patch**. Its 39/60
final-operation completions are a diagnostic; 21 final failures remain, and
fresh-list cases did not complete explanation. The patch was not applied to
production source by the evidence publication.

PRs [2](https://github.com/zhexusun10/Multi-Agent-Assistance-System/pull/2) and
[3](https://github.com/zhexusun10/Multi-Agent-Assistance-System/pull/3) are merged.
Main now also contains later English-edition and continuation changes; the
benchmark above does **not** validate those later changes. Participant UAT,
independent relevance review, unresolved defects and final team signoff remain
open. A completed report is not a completed product acceptance.

## Evidence organization

Current report formats and the metric CSV stay directly accessible. Historical
loose copies already present byte-for-byte inside retained archives were removed;
unique superseded publication files were archived. Original evidence, failed
receipts and earlier judgments remain unchanged. Overlapping historical suites
must not be summed into one success rate. No test or model call was rerun for
this cleanup.

From the repository root, verify all pre-cleanup file identities without network
access, model calls or file writes:

```sh
python3 evaluation/verify_material_preservation.py
```
