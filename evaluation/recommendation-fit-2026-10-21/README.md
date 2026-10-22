# Recommendation fit remediation and acceptance evidence

Recorded tests: 21 October 2026, evidence version 3.6. Published report edition: 3.7.
Start at [the unified QA entry](../README.md) for the full testing handoff. The exact tested source is
`89131897f56bbc176c30246a451f118f7de0209f` plus the ten source, test and provenance
files recorded in [source parity](source-parity.json). Those ten files match
the recorded test checkout at publication commit
`fa56a71fa0bf4077f6cfe340d1c04f61536192ad`. Their original bytes remain in the
unchanged focused and comprehensive ZIPs. The [later pre-merge review](../pr-2-review-2026-10-22.json)
adds a narrow spoken-comparator fix and six regressions in `rental-language.ts`
and its intake test file; it does not rerun or amend this 44-case execution record.

## Review material

- [English acceptance report](01_Latest_Testing_and_Acceptance_Report.docx)
- [English acceptance tracker](02_Latest_Acceptance_Tracker.xlsx)
- [Current 44-case catalogue](04_Recommendation_Fit_Retest_Catalogue.txt)
- [Original 36/44 baseline catalogue](03_Recommendation_Fit_Catalogue.txt)
- [Machine-readable outcomes](english-case-rows.json) and [summary](acceptance-summary.json)
- [Archived original focused evidence and reproduction bundle](../archive/Group42_Recommendation_Fit_Fix_8913189.zip)

The archived focused ZIP retains all recorded structured, projection and dialogue receipts, model
audits without credentials, the original eight failed dialogue turns, executed
harnesses, independent oracle, code-check logs, the verified patch and its file
manifest. Extract it before following `evidence/` and `tools/` paths in the report
or tracker. The standalone JSON files here are unchanged recorded receipts.

The direct report is publication edition **v3.7**, recording that the fixes and
evidence are published in [PR 2](https://github.com/zhexusun10/Multi-Agent-Assistance-System/pull/2).
Use that PR for the current merge status; the report retains its pre-merge
publication snapshot. The tracker, original v3.6 reports inside the
ZIPs and JSON receipts retain the original test-time record. Version 3.7 changes
no result, table, unresolved issue, human review or signature. No test or model
call was rerun for this publication update.

## Problem and fix

| Issue | Corrected behaviour | Implementation |
| --- | --- | --- |
| DEF09 / RN04-06 | Bedroom minimum remains `>= 3` when the same sentence also caps rent, including subsequent district and area refinements. | Attribute-local comparison parsing in `src/domain/rental-language.ts`. |
| DEF10 / RN07-09 | NUS resolves independently of radius/update wording; successful resolution clears its own obsolete question; explicit furnishing and lease requirements survive. | `src/domain/geo-intake.ts`, `src/adapters/locations.ts`, `src/domain/requirements.ts`, `src/domain/rental-language.ts`. |
| DEF11 / RN10 | A preferred district stays soft. Controller repair retains tool history and a completed empty ranking passes through the existing final gate. | `src/domain/rental-language.ts`, `recommendation/agent/controller.py`. |
| DEF12 / RN12 | Explicit district cancellation removes the district while preserving budget and bedrooms; negated or unsupported cancellation stays rejected. | `src/domain/rental-language.ts`; original removal evidence checks retained. |

The original 71-file upstream fingerprint manifest is retained unchanged. One
exact controller override is separately declared in
`contracts/releases/recommendation-downstream-patches.json` and enforced by
`scripts/import-recommendation-upstream.py --check`.

## Recorded validation

| Scope | Result |
| --- | --- |
| Frozen structured recommendation cases | 24/24 passed with real PostgreSQL/Neo4j data and a scripted controller. |
| Production TypeScript projection cases | 8/8 passed over the fresh RF01 response; no extra model calls. |
| Deployed natural-language dialogue turns | 12/12 passed with real ClinePass DeepSeek calls and the live native controller. |
| Previously failing dialogue turns | RN04, RN05, RN06, RN07, RN08, RN09, RN10, RN12 all passed. |
| User hard-condition violations in verified dialogue results | 0 across 36 displayed occurrences. |
| Added supporting regressions | 26 passed: 19 TypeScript, 3 controller and 4 provenance checks. |
| Full TypeScript regression | 168 passed, 19 skipped, 0 failed. |
| Full Python regression | 216 passed, 3 skipped. |
| Both TypeScript typechecks, build and upstream provenance | Passed; one declared downstream business change. |

The 26 supporting checks are not added to the 44-case catalogue. The authored
dialogues include dependent refinement turns; 44/44 is a contract-fit regression
result, not a population accuracy or participant satisfaction estimate. The
frozen catalogue SHA-256 is
`a7758611aea5beff3beefdf398d2c3c2f6e3c59998e1f2ef927be15c56151fbb`.

For a quick check from the project root after installing project dependencies:

```sh
node --import tsx --test tests/recommendation-fit-regression.test.ts
python -m pytest recommendation/tests/test_fit_controller_regression.py tests/test_recommendation_provenance.py -q
python scripts/import-recommendation-upstream.py --check
npm run typecheck
npm run typecheck:recommendation
npm run build
```

For the full recorded 44-case profile, extract the [primary evidence archive](../acceptance-history/Group42_Latest_Acceptance_8913189.zip) and follow
`tools/recommendation_fit_fix/README.txt`: use the fixed 21,431-listing snapshot,
NUS graph, retrieval generation and isolated application databases. Harnesses
retain their original environment paths; adjust paths only when reproducing on
another machine, and preserve the frozen expectations and oracle assertions.
Real-model runs need a private local environment; no credentials are included.

## Acceptance still pending

DEF09-DEF12 are locally verified fixes awaiting team integration review. Earlier
DEF04-DEF08 remain open. Participant UAT, agreed performance acceptance, Windows
verification and final team signoff are outstanding. Overall system acceptance
remains **PENDING**. All external actions stayed in simulation.

Archived original focused-package SHA-256:
`35e130682c594712e68bb8c1a216e210807cac6dc9e3bfea5389dd84512c024d`.

## Complete earlier testing and acceptance evidence

The [historical QA index](../acceptance-history/README.md) now publishes the
140-case extension and all retained earlier delivery packages, original results,
provider audits, browser/data/retrieval evidence, historical report revisions and
per-file checksums in this same PR. Recorded failures and NOT_RUN/UAT decisions
remain unchanged. These overlapping profiles must not be added into one score.
