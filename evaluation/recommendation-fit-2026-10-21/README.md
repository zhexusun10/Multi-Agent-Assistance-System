# Recommendation fit remediation and acceptance evidence

Recorded on 21 October 2026, report version 3.6. The exact tested source is
`89131897f56bbc176c30246a451f118f7de0209f` plus the ten source, test and provenance
files in this change. The baseline branch was checked before publication and had
no newer commits. The six modified existing files and four added files match the
recorded test checkout byte for byte; see [source parity](source-parity.json).

## Review material

- [English acceptance report](01_Latest_Testing_and_Acceptance_Report.docx)
- [English acceptance tracker](02_Latest_Acceptance_Tracker.xlsx)
- [Current 44-case catalogue](04_Recommendation_Fit_Retest_Catalogue.txt)
- [Original 36/44 baseline catalogue](03_Recommendation_Fit_Catalogue.txt)
- [Machine-readable outcomes](english-case-rows.json) and [summary](acceptance-summary.json)
- [Complete focused evidence and reproduction bundle](Group42_Recommendation_Fit_Fix_8913189.zip)

The ZIP retains all current structured, projection and dialogue receipts, model
audits without credentials, the original eight failed dialogue turns, executed
harnesses, independent oracle, code-check logs, the verified patch and its file
manifest. Extract it before following `evidence/` and `tools/` paths in the report
or tracker. The standalone JSON files here are unchanged recorded receipts.

The report, tracker, archive and JSON capture the local retest **before GitHub
publication**. Statements that the fix was not pushed, and integration was
pending, describe that time. This branch publishes the same tested files for
review; merging and final team acceptance remain separate decisions. No previous
test outcome, unresolved issue, human review or signature has been rewritten.

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

For the full recorded 44-case profile, extract the evidence archive and follow
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

Evidence archive SHA-256:
`35e130682c594712e68bb8c1a216e210807cac6dc9e3bfea5389dd84512c024d`.
