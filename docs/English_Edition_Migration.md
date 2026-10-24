# English edition: scope, isolation and verification

Date: 24 October 2026. Chinese baseline: `4d687e3b27e016f622dd487909fc99f56096265c`.

## Separate sibling projects

- Preserved Chinese project: `D:\Real Estate Agent\Multi-Agent-Assistance-System`.
- English project: `D:\Real Estate Agent\Multi-Agent-Assistance-System-English`, local branch `english-version`.
- Chinese HEAD and its clean worktree were checked after migration. No Chinese source/configuration was overwritten.
- The English copy has its own dependencies, ignored `.env`, conversation/test databases, native SQLite state, process records and logs. Source objects were cloned locally; runtime dependencies/data needed by this workstation were copied separately.
- Existing PostgreSQL/Neo4j property data and Chroma infrastructure are shared. English full startup checks/starts existing containers and verifies data; it does not recreate those containers or reproject the shared listing graph.

| Component | English | Chinese |
| --- | --- | --- |
| Frontend / API proxy | 8020 | 8010 |
| Graph service | 3011 | 3001 |
| Read-only property API | 8099 | 8089 |
| Retrieval | 8101 | 8091 |
| Native Recommendation | 8102 | 8092 |
| Conversation database | multi_agent_assistance_en | multi_agent_assistance |

English intent vectors use `RETRIEVAL_NAMESPACE=english`. The translated knowledge has a distinct content-hashed generation. Neither replaces the Chinese retrieval index. The normal English Recommendation controller remains `live`; deterministic scheduling is used only by the isolated acceptance harness.

## Language changes

The interface, accessibility labels, prompts, independent-review instructions, role contracts, four application skills, backend clarification/error text, property introductions/recovery, follow-up drafts, summaries and current operating guides are English. There are 39 translated intake examples, 12 Explanation examples and 42 intent templates. Eight curated knowledge articles were translated with unchanged article IDs, source URLs and review dates.

Dates use English/Singapore presentation; time calculations retain `Asia/Singapore` and ISO wire values. Longer buttons, tabs, cards and headings wrap appropriately across desktop/tablet/mobile. Known English place names are preferred for display. Photo dialogs and viewing-status bindings recognise the English labels, preserving the existing attention tracking.

The length caps needed for English prose are 1,000 characters per Explanation paragraph and 5,200 overall, with the existing 700-English-word cap. Model request counts, repairs, timeouts, grounding, source/number checks, mandatory per-home facts and independent review were retained.

## Preserved contracts and behaviour

No API field, enum, criterion ID, SQL column, tool name, hard/soft semantic, weighting rule, ranking formula, cold-start threshold, Top5 boundary, booking confirmation or permission rule was renamed or simplified. All 132 tracked files under `recommendation/` were checked byte-for-byte against the preserved Chinese baseline and were identical.

The 39 intake and 12 Explanation examples were also compared after excluding language-bearing text: fields, IDs, enum values, numerical values, statuses and support references were retained. Local regression exercises the new English strings while keeping original multilingual user-input cases.

Chinese input vocabulary, negation/number/date parsers, place aliases and exact source-format quotation aliases intentionally remain. Removing them would change functionality. User text, exact evidence quotations, original source category values, place names lacking an English source name, historical design documents and recorded evaluation fixtures remain in their original language. They are data/compatibility/history, not untranslated interface instructions. Historical paid-test reports were not relabelled as English live-model results.

## Verification completed

All checks below used **zero paid model requests**.

| Check | Result and scope |
| --- | --- |
| TypeScript build/typecheck | Passed. |
| Node regression | 331 passed, 22 intentionally opt-in/skipped, zero failed (353 total). Includes three English-specific quotation/name/language tests. |
| Native Recommendation and Python application tests | 510 passed. |
| Data-module Python tests | 153 passed, five skipped optional cases. |
| Isolated PostgreSQL refinement test | Passed: persistence, structured merge, budget preservation, stale/cross-session rejection, idempotence and exhaustion. |
| Real refinement integration | Passed against actual SQL/KG, native HTTP, application workflow, API proxy, Chromium and SSE without API interception. Initial eligible pool 239 from 21,431 records; five homes and active card; structured submission returns five new homes, preserves the budget and stops asking when the native signal is inactive. |
| Explanation in the real integration | Existing evidence recovery deliberately exercised with source validation; not a paid writer/reviewer pass. |
| English layout/interaction | Passed at 1600×1000, 1024×900 and 390×844. Fresh live English session/bootstrap/deletion checked; populated layout uses saved real state and real SQL/KG details. Top5, no horizontal overflow, full details, photo-dialog reading identity, active-view status and staged English date selection checked. Zero JavaScript errors/unexpected API routes. |
| Native provenance | Existing upstream manifest check passed; all 132 native files identical to the Chinese baseline. |
| Chinese preservation and service health | Original baseline HEAD/worktree preserved; Chinese8010 and English8020/3011/8099/8101/8102 healthy. English retrieval has 42 templates and 62 translated knowledge chunks. |

Paid model generation/review was intentionally not repeated for this language-only migration, respecting the standing cost constraint. English prompt quality is covered by contract/few-shot/grounding regressions and inspection, not a claim that a new direct live LLM run passed. Snapshot photo availability still depends on the original PropertyGuru CDN; the UI keeps its existing load-failure fallback.

## Local evidence and operation

Generated logs/screenshots remain ignored under `data/.runtime/`: `english-test-complete.jsonl`, `english-python-tests.log`, `english-data-tests.log`, `english-db-test.log`, `english-parity.json`, `english-ui/report.json`, desktop/tablet/mobile screenshots and the isolated `refinement-real-*` run. Current real/refinement reports also appear under `evaluation/reports/` in this English copy.

Open Docker Desktop, then run:

```powershell
Set-Location 'D:\Real Estate Agent\Multi-Agent-Assistance-System-English'
.\scripts\start-app.ps1 -SkipDataImport
```

Open http://127.0.0.1:8020/. See the root deployment guide for shutdown, component restarts and independent database setup. The English branch is local; publishing it over the Chinese GitHub main requires a separate explicit request.
