# Complete testing and acceptance evidence

This folder extends the same PR with the retained 140-case extension and the
earlier testing/acceptance work. It is an evidence publication, with **no new
product tests, model calls or changed test outcomes**. Current report v3.6 and
the recommendation fixes remain in [the current acceptance folder](../recommendation-fit-2026-10-21/).
Overall system acceptance remains **PENDING**.

## Start here

1. Review the [140-case readable catalogue](extended140_Case_Catalogue.txt) or
   [machine-readable catalogue](extended140_Case_Catalogue.json).
2. Review the [original extension summary](extended140_acceptance_summary.json),
   the [five failed cases and issue evidence](extended140_open_issues.json), then
   the per-suite receipts below.
3. Download the [complete consolidated evidence archive](Group42_Latest_Acceptance_8913189.zip)
   for provider audits, executed harnesses, database/graph checks, original failed
   attempts, browser screenshots, JUnit/logs and both fit baseline/retest records.
4. Use [PHASE_INDEX.json](PHASE_INDEX.json) for machine-readable phase scope and
   [EVIDENCE_MANIFEST.json](EVIDENCE_MANIFEST.json) for file and archive-member
   SHA-256 digests. [VALIDATION.json](VALIDATION.json) records publication checks.

## The 140-case extension

| Suite | Original individual outcomes | Original receipt |
| --- | --- | --- |
| Intent / XI01-XI60 | 59 passed / 1 failed; the predeclared >=95% suite threshold was met | [intent-result](extended140_intent_result.json) |
| Knowledge / XK01-XK24 | 23 passed / 1 failed; suite FAIL | [knowledge-result](extended140_knowledge_result.json) |
| Reply / XR01-XR18 | 17 passed / 1 failed; suite FAIL | [reply-result](extended140_reply_result.json) |
| HTTP boundary / XA01-XA32 | 30 passed / 2 failed; suite FAIL | [boundary-result](extended140_api_boundary_result.json) |
| Actual process outage/recovery | 6 passed / 0 failed | [fault-recovery-result](extended140_fault_recovery_result.json) |

The retained result is **135/140 passed, five individual failures**: XI20, XK23,
XR05, XA08 and XA09. These remain visible. The separate DEF04 challenge/repeats
are not new members of the 140-case catalogue. Frozen [model-case inputs](extended140_frozen_cases.json)
and [HTTP-case inputs](extended140_api_boundary_cases.json) are unchanged.

## Historical work and downloadable originals

| Stage | Contents and scope | Download |
| --- | --- | --- |
| Initial design pack v1.2 | Plan/report template, Excel tracker, presentation, evaluator, fixtures, owner guide and participant cards; 60 planned synthetic cases. Product tests and human UAT were NOT_RUN. | [Initial QA pack](Group42_QA_Pack.zip) |
| Early repository QA v2 | Pinned `907cfa8` static preflight, original Python collection errors, separately labelled patch candidate, TS/Python logs and JUnit, data audit and seven full-pool baseline oracles. Includes a clearly labelled prior-team-evidence folder. | [Repository QA v2](Group42_Repository_QA_v2.zip) |
| Latest comprehensive pack v3.6 | 303 retained files covering pinned `8913189` data/SQL/Neo4j, native integration, eight offline retrieval follow-ups, live hybrid retrieval, initial ClinePass evaluation, the 140-case extension, browser/performance/fault evidence and original/current fit runs. | [Complete acceptance pack](Group42_Latest_Acceptance_8913189.zip) |
| Original 140-case dataset export | All inputs, expectations, results, issue records and exact executed harnesses; 24 retained files. Full request audits/screenshots are in the comprehensive pack. | [140-case export](Group42_Test_Dataset_8913189.zip) |
| Original recommendation-fit baseline | 48 retained files documenting the original 36/44 outcome and eight failed dialogue turns. The current 44/44 retest stays in the existing current folder. | [Fit baseline pack](Group42_Recommendation_Fit_Tests_8913189.zip) |
| Historical report revisions | Original report/tracker/README/manifest snapshots from before retrieval, live-model, subscription, 140-case extension, fit baseline and fit remediation updates. | [Historical report snapshots](Historical_Report_Revisions.zip) |

After extraction, keep each archive's root directory intact. `evidence/` and
`tools/` paths inside a report or tracker refer to that extracted package. Exact
executed harnesses retain their original environment paths; adapt paths for a
new run while keeping frozen expectations and oracle logic unchanged.

## Other receipts available without downloading an archive

- [Native full-snapshot SQL acceptance](history_native_sql_acceptance.json)
- [Eight offline retrieval tests](history_retrieval_offline_acceptance.json)
  and [actual hybrid retrieval integration](history_live_retrieval_acceptance.json)
- [Initial live-model acceptance](history_initial_model_acceptance.json): the
  default intent model-channel assertion failed, configured intent passed 42/42,
  RAG passed 21/22 and DEF04 remained open; seven deployed operations completed.
- [Performance and response-contract observations](history_performance_observation.json):
  30 retrieval samples and six workflows, with the recorded failed contract and
  no agreed latency/capacity acceptance threshold.
- [Early repository defects](history_offline_preflight.json), which belong to
  the earlier `907cfa8` source layout and are not silently reapplied to the later
  integrated layout.
- Original [desktop](history_native_desktop.png), [mobile](history_native_mobile.png)
  and [knowledge-browser](history_knowledge_browser.png) screenshots.

## How to interpret the work

Profiles overlap. Before/after fit runs are not 88 independent cases; the 26
supporting regressions are separate code checks; native/browser/performance
observations are not extra unique cases in the 140-case catalogue. Do not sum
all historical counts into one accuracy or success rate. The actual profiles,
failures, skips, fallback and environment limitations are retained per phase.

The initial design pack's 40 synthetic holdout inputs are now published for team
review and must not be described as a blind holdout in a future evaluation.
Participant UAT/relevance forms remain templates, with no invented responses or
signatures. Earlier DEF04-DEF08, participant UAT, agreed performance acceptance,
Windows verification and final team signoff remain outstanding. External
actions in these QA profiles stayed simulated.

## Integrity and preservation

The five original delivered ZIPs are byte-preserved. Every member was checked
against its retained local delivery folder, and available embedded per-file
manifests were verified. Directly browsable receipts are unchanged copies.
Historical report members are unchanged; only their new containing archive was
created. The duplicate retained older consolidated folder is covered by the
comprehensive archive and report snapshots, as recorded in the manifest.

Use the new inventory's outer-file checksums to verify downloaded ZIPs. Some
historical embedded metadata records an earlier enclosing-archive digest; that
packaging metadata is retained verbatim and its mismatch is explicitly recorded
in the new archive validation. It does not replace member-level verification.
Older statements such as NOT_RUN, failed, not pushed and pending remain records
of their original time. Publication is not a new acceptance decision.

Credential, private-key and session-token checks cover archive contents and
nested Office/ZIP payloads. Private runtime environments, databases, model
weights and browser profiles are outside these retained delivery packages.
