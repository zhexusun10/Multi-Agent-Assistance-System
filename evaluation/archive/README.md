# Historical testing and acceptance archive

Start with the [current QA entry](../README.md) for the published report,
tracker, results and outstanding acceptance work. This archive preserves earlier
delivery packages and reporting history. The primary full evidence download is
[the unchanged comprehensive v3.6 package](../acceptance-history/Group42_Latest_Acceptance_8913189.zip).

## Original historical work

| Package | Recorded scope | How to use it |
| --- | --- | --- |
| [Initial QA design pack](Group42_QA_Pack.zip) | Design pack v1.2; 60 planned synthetic cases, evaluator, proposed gates, owner guides and blank UAT materials. Product tests were NOT_RUN. | Original planning and reusable templates; published holdout inputs are no longer blind. |
| [Earlier repository QA v2](Group42_Repository_QA_v2.zip) | Source `907cfa8`; original collection errors, isolated patch candidate, data audit, logs and seven baseline oracles. | Preserve the earlier source and original/candidate distinction. `team_historical` is inherited team evidence. |
| [Historical report revisions](Historical_Report_Revisions.zip) | Six pre-update report/tracker/README/manifest sets, 24 unchanged files. | Audit the sequence of decisions; use the unified QA entry and PR 2 for the current review and merge status. |

## Original focused downloads

These self-contained packages are kept for original delivery provenance and
focused reproduction. Their substantive evidence is already covered by the
primary comprehensive package and the directly browsable catalogues.

| Package | Coverage already available in the main materials |
| --- | --- |
| [Recommendation fix package](Group42_Recommendation_Fit_Fix_8913189.zip) | 69 of 71 members exactly match comprehensive-package content; the other two are its original README and manifest. |
| [Recommendation baseline package](Group42_Recommendation_Fit_Tests_8913189.zip) | 46 of 48 members exactly match comprehensive-package content; the other two are its original README and manifest. The original eight failures remain preserved. |
| [140-case data export](Group42_Test_Dataset_8913189.zip) | 22 of 24 members match comprehensive-package or standalone catalogue content; the other two are its original README and manifest. |

## Original publication and report status

[The original publication metadata](publication-v1/README.md) preserves the
previous phase index, manifest, validation and reading guides. The original v3.6
Word report and tracker remain byte-preserved at the root of the comprehensive
ZIP and the focused fix ZIP. Version 3.7 changes only the directly published
report's version, publication status and related temporal wording; original
tables, outcomes and human fields are unchanged.

Every archive above has the same SHA-256 as before it moved. Internal paths and
embedded metadata remain untouched. Older claims such as NOT_RUN, HTTP 402,
failed or not pushed describe their original phase. Use the [current inventory](../acceptance-history/EVIDENCE_MANIFEST.json)
for outer ZIP hashes and the old-to-current path map. Some historical outer-ZIP
digest metadata differs from the retained ZIP and remains explicitly recorded;
member-level verification remains the integrity check for those original records.

All seven original published ZIPs remain in this branch, so this reorganization
reduces competing primary downloads rather than repository byte size. No Git
history was rewritten. No product test or model call was run for this cleanup.
