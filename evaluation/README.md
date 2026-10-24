# Group 42 testing and acceptance handoff

**Final acceptance: COMPLETED FOR THE AGREED PROJECT SCOPE.** Revision 3,
25 October 2026, is the current final edition. Start with the
[English final report](final-acceptance-2026-10-25/Group42_Final_Test_Acceptance_Report_EN.pdf)
and [results workbook](final-acceptance-2026-10-25/Group42_Final_Acceptance_Results_EN.xlsx).

| Need | Read |
| --- | --- |
| Final scope, completed decision and file index | [25 October final guide](final-acceptance-2026-10-25/README.md) |
| Precision, judged-pool Recall and NDCG at K=1–10 | [Final metric CSV](final-acceptance-2026-10-25/Final_TopK_Metrics_EN.csv) |
| Confirmed protocol and current compatibility evidence | [Acceptance decision](final-acceptance-2026-10-25/Acceptance_Decision_EN.json) and [eight-test receipt](final-acceptance-2026-10-25/Compatibility_Verification_EN.json) |
| Inputs, judgments, model receipts, scripts and complex failures | [Unchanged original 251-file ZIP](expanded-final-acceptance-2026-10-24/Group42_Expanded_Final_Acceptance_2026-10-24.zip) |
| Earlier report edition and original benchmark scope | [24 October historical guide](expanded-final-acceptance-2026-10-24/README.md) |
| Earlier tests, original failures and superseded reports | [Historical archive guide](archive/README.md) |
| Locate original files and verify their bytes | [Preservation manifest](CLEANUP_MANIFEST_EN.json) and [read-only verifier](verify_material_preservation.py) |

## Final acceptance scope

Completed acceptance covers the 40 dataset-supported structured native scenarios
and their expected outcomes, frozen relevance grades and K=1–10 metrics, stored
mandatory-condition/source checks and current saved-result display compatibility.
Participant UAT, independent human relevance scoring and separate human signatures
are not prerequisites under the requester-confirmed final protocol.

The recorded live benchmark uses main `4d687e3`, 21,431 rentals from the 9 October
snapshot and 1,382 scenario/listing judgments. Precision and NDCG average 36 ranking
scenarios; judged-pool Recall averages 35. Four scenarios are controls. At K=5,
Precision is 90.56%, pool Recall 37.33% and NDCG 0.9830; at K=10 they are 85.00%,
59.32% and 0.9541. These recorded values and their denominators are unchanged.
Pool Recall is not full-corpus Recall; cohort labeling methods differ.

Current-main review uses `35304d0`: eight deterministic offline compatibility
tests pass and 27 selected native core files are unchanged from the live
benchmark. Saved-result expansion preserves the initial batch, order and data.
Legacy sessions without a reserved tail may refresh and change identities. The
structured benchmark does not measure natural-language intake or browser
satisfaction; this finalization does not claim a new full live end-to-end run.

## Historical studies and evidence

The 60-case complex experiment is retained as a separate non-blocking study.
Its historical 39/60 final-operation completions and 21 failures remain unchanged.
Unverifiable user needs reflect data limits; timeout, validation, provider and
explanation failures remain technical observations. They are not all reclassified
as missing-data failures or passes. The historical timeout patch remains local.

PRs [2](https://github.com/zhexusun10/Multi-Agent-Assistance-System/pull/2),
[3](https://github.com/zhexusun10/Multi-Agent-Assistance-System/pull/3) and
[5](https://github.com/zhexusun10/Multi-Agent-Assistance-System/pull/5) are merged.
This final edition is published separately through a pull request; its branch
publication does not assert a merge into main. Frozen older reports retain
their original deferred or NOT_RUN decisions as historical records.

All ten whole evidence ZIPs, seven split parts, original grades and failure
receipts remain unchanged. No historical raw evidence is removed or repackaged.
The final report, workbook and CSV are directly accessible; original evidence is
stored once in its existing archive. Overlapping suites must not be summed into
one success rate. No paid model call accompanies this publication.

Verify the retained original identities from the repository root:

```sh
python3 evaluation/verify_material_preservation.py
```
