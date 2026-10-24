# Historical QA archives

Start at the [current QA handoff](../README.md) for the consolidated report.
These original packages preserve earlier scopes, including failures and
superseded reports. They overlap and must not be added into one pass rate.
All ten pre-existing whole ZIPs and all seven original evidence parts remain
byte-for-byte unchanged. No Git history was rewritten.

| Recorded scope | Preserved material |
| --- | --- |
| Design stage: 60 planned cases, NOT_RUN | [Original QA pack](Group42_QA_Pack.zip) |
| Early repository baseline `907cfa8` | [Repository QA v2](Group42_Repository_QA_v2.zip) |
| Earlier report editions | [Original report revisions](Historical_Report_Revisions.zip) |
| 140-case extended dataset, source `8913189` | [Frozen test dataset](Group42_Test_Dataset_8913189.zip) |
| Recommendation-fit 36/44 baseline | [Original baseline package](Group42_Recommendation_Fit_Tests_8913189.zip) |
| Recommendation-fit 44/44 retest | [Original fix package](Group42_Recommendation_Fit_Fix_8913189.zip) |
| Comprehensive earlier acceptance, 303 files | [Original latest-at-the-time package](../acceptance-history/Group42_Latest_Acceptance_8913189.zip) and [scope guide](../acceptance-history/README.md) |
| Historical report publication edition v3.7 | [Relocated Word report](Group42_Testing_and_Acceptance_Report_2026-10-22.docx) |
| 23 October, 20 scenarios, 577 judgments | [Historical relevance guide and seven archive parts](../recommendation-relevance-2026-10-23/README.md) |
| 24 October, 40 basic + 60 complex scenarios | [Consolidated evaluation guide](../expanded-final-acceptance-2026-10-24/README.md) |
| Superseded publication indexes, receipts and READMEs | [Historical publication files](Historical_Publication_Files_2026-10-24.zip) |
| Frozen original case source | [Original rental cases v1](rental-cases.v1.ts) |

The new publication ZIP stores original repository paths as member names. It
contains unique old publication metadata and the original guides changed during
cleanup, including their historical status wording. It does not replace or
repackage the original raw-evidence archives. The current final PDF, Word report,
workbook and metric CSV remain directly available at the QA entry.

Use [CLEANUP_MANIFEST_EN.json](../CLEANUP_MANIFEST_EN.json) to look up **any
pre-cleanup filename**, its original size/hashes and its exact preserved location.
Archive-member paths are data paths, not executable instructions. Verify all
93 original file identities from the repository root with:

```sh
python3 evaluation/verify_material_preservation.py
```

The original published packages retain their historical manifests unchanged.
Some old outer-package digest metadata was generated at a different packaging
step; this cleanup verifies retained bytes against the GitHub source snapshot
and gives each removed loose file an independently checked member location.
