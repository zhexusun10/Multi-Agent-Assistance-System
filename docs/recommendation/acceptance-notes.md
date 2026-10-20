# Discovery and acceptance record — 18 October 2026

## Repository facts and implementation choices

- Existing application: TypeScript/LangGraph runtime, provider factory in `src/model.ts`, FastAPI gateway and Python preprocessing. No existing recommendation implementation or business requirements/explanation/frontend contracts were present in the checkout. The IDE's mentioned `src/recommendation/state.ts` was not on disk.
- Standalone service/contracts use Python/Pydantic under the requested directory names. Live tool calling uses a small subprocess bridge to the already installed TypeScript provider factory. No additional agent framework or runtime role assignment was introduced.
- `data/storage/models.py` (now `dataPreprocessing/storage/models.py`) establishes PostgreSQL `properties` schema; `graph/sync.py` and `graph/places.py` establish integer graph listing IDs (normalized to public strings), `Place` and `NEAR_PLACE`, active place categories and relationship properties. Database adapters own their SQL/Cypher and do not import preprocessing application code.
- Read-only checked-in inventory: **475 rental CSV rows**, **3,716 place records**. These are observed export counts, not asserted live database counts. Ten rental rows contain invalid numeric source values, including negative bedroom sentinels; normalization preserves these as unknown with explicit warnings.
- The full listing snapshot/local SQLite archive is not required for offline tests. Source exports/datasets remain unchanged under `data/`; no synced `sources/` files were changed. No import, scraping, live write, commit, push or deployment was performed.
- Current listing schema lacks evidence for furnishing, lease duration, current availability and destination travel-time checks. Such hard criteria cannot pass. Posting time and sales transaction history are not rental-availability or rent-history evidence.
- All RENT rows are read each new run. This deliberately avoids a complex cache reuse policy for the current small dataset. Historical request retries replay immutable original facts through a replay gate; new requests refresh sources and compare final re-reads. Cross-database atomic consistency is not claimed.
- Explicit feature statements conservatively disable that entire inferred similarity feature globally; district/destination statements disable implicit geography. This is documented, versioned behavior. Weights/scales are initial engineering defaults, not learned relevance parameters.
- Local SQLite serializes conversation updates, retains full pools and immutable responses/traces, and rejects stale state references. The future orchestrator still owns authoritative request ordering and must discard superseded responses.
- Windows launch scripts were path-updated but cannot be executed on this macOS host. Compose preserves the original default project name `data`; externally supplied project names must be retained by operators.

## Checks performed

Commands run from the repository root with `.venv/bin/python` on Python 3.11.16. The host's unqualified `python3` was 3.9; the local virtual environment was corrected before testing.

| Check | Observed result |
| --- | --- |
| `python -m pytest dev/recommendationSubagent/tests dataPreprocessing/tests -q` | 190 passed, 7 skipped; integration-dependent data checks skipped. One dependency deprecation warning from FastAPI/Starlette test client. |
| Recommendation-only suite | 40 passed: contracts, gate rejection, feedback constraints/dedup, immutable replay, scope changes, concurrent stale-state rejection, reference isolation, terminal staging/resume, metrics, adapter query mapping, no-match/unknown/outage/loop exhaustion. |
| `python -m unittest discover -s tests -p 'test_*.py'` | 7 passed. |
| `npm run typecheck`, `npm run typecheck:recommendation`, `npm run build` | Passed, including the TypeScript model bridge. |
| `npm test` | 13 passed, 2 PostgreSQL integration tests skipped because no test database was configured. |
| `python -m dev.recommendationSubagent.schemas --check` | Published schemas match authoritative models. |
| `python -m dev.recommendationSubagent.examples.run` | Fixture/scripted search, viewing feedback, retry and tighter budget passed. First IDs 01/03/05/07; feedback reordered to 03/05/01/07; tighter cap returned 03/01/02. IDs have the `fixture-` prefix. |
| `python -m dev.recommendationSubagent.examples.check_export` | Explicit offline export/scripted check read 475 rows; 2 candidates survived cheap hard filters; both remained unverified; `needs_clarification`, 0 verified recommendations, final gate passed. Selected criteria were not relaxed. |
| Both preprocessing `--help` commands | Passed without ingestion or live database mutation. |
| `git diff --check` | Passed. Dataset/export diff empty. |

The fixture walkthrough exercises 80/20 fusion but does not claim recommendation quality. Full-data no-results are a valid observed result, not an excuse to weaken constraints. Hard-constraint violations in returned fixture recommendations: zero, enforced by tests and the final gate. Latency/tool counts are measured per run in responses; token usage remains null unless a provider reports it.

## Live acceptance — blocked, not passed

Exact configuration probe:

```bash
.venv/bin/python -m dev.recommendationSubagent.terminal --data-mode database --controller live --check-connections
```

Observed: no `PROPERTYGURU_READ_DATABASE_URL` or `PROPERTYGURU_DATABASE_URL`; no `NEO4J_URI`; no selected provider key. No root `.env` was present. Listing/Neo4j report `NOT_CONFIGURED`; LLM reports configured=false/contacted=false. `openai:gpt-4.1-mini` is only the configured default identifier, **not a successfully invoked model**. No database identities or server versions were verified live.

| Required live walkthrough step | Status and concrete blocker |
| --- | --- |
| 1. Connect/check SQL, Neo4j and representative IDs | Blocked: database configuration absent. |
| 2. Database/live initial search | Blocked: database configuration and model credentials absent. |
| 3. Actual geographic lookup/relationship evidence | Blocked: Neo4j not configured. `examples.live_geography` is implemented to select a real graph destination when connected. |
| 4. Live feedback and fusion | Blocked by steps 1–3; fixture equivalent passed. |
| 5. Live duplicate feedback and tighter constraints | Blocked by steps 1–3; fixture equivalent passed. |
| 6. Inspect actual live-model trace | Blocked: no successful live invocation. Scripted and fake-controller trace checks passed. |
| 7. Restart live conversation | Blocked by live prerequisites; SQLite/terminal restart and history tests passed offline. |
| 8. Live outage behavior and malformed terminal input | Live outage not tested; injected repository outage and malformed terminal input passed offline with preserved state. |
| 9. Record live identities/counts/model | Blocked: no live run exists to report. |

Use the documented terminal after configuring existing services. Do not treat the implemented adapters, schema mocks, export check or fixture walkthrough as proof of live integration. No teammate module is required to complete those outstanding checks.

## Offline completion follow-up

The user confirmed that live services remain unavailable and requested continued offline work. No live connection or provider execution was attempted in this follow-up.

Completed remaining implementation details:

- Validated, application-owned scoring policy and run limits, loaded by `--config`/`RECOMMENDATION_CONFIG`; deterministic policy fingerprints and immutable configuration snapshots.
- Globally disabled similarity features, validated positive scales/finite weights, fixed configurable utility bounds, and bounded model-facing candidate previews with complete stored pools.
- Per-model invocation timing, structured failure records, accurate failed-run call/usage summaries, content-block parsing, malformed tool-call recovery and shared turn budgets across repairs.
- Deadline enforcement through the final database refresh; failed requests do not commit profile/state changes.
- Terminal rejection of invalid feedback envelope shapes without altering accepted input, plus old/new score deltas.
- Repeatable `terminal.acceptance` runner exporting step-specific requests/responses, SQLite state and traces. Offline results are explicitly `not_run_offline` for live acceptance; missing live prerequisites report blocked rather than passed.

The recommendation suite now includes 40 passing tests; together with preprocessing, 190 pass and 7 integration-dependent checks skip. The existing 7 API tests, schema consistency, bridge typecheck, sample flow, read-only 475-row export check and whitespace checks also pass. No TypeScript runtime logic changed in this follow-up; prior Node runtime test/build results remain recorded above.

New acceptance tests verify recoverable errors in the actual live-controller dispatcher using scripted model responses (not a live provider), failed model-call trace accuracy, global feature disabling, changed scaling, model/tool budgets, final-refresh timeout rollback, summary truncation without pool loss, and offline/blocked acceptance reports.

## Local CSV database acceptance — 19 October 2026

Supersedes the earlier database-configuration blocker for this Mac. Docker PostgreSQL and Neo4j are running; the user authorized testing with the checked-in small CSV. The guarded `dataPreprocessing.csv_sample` importer loaded 475 rentals and 3,716 places into the initially empty local stores and verified matching SQL/graph listing IDs. An identical rerun reused SQL records and rebuilt the owned graph projection; 53,050 proximity relationships exist. Source assets remain unchanged.

Actual graph inspection corrected the previous assumed ID type: PostgreSQL/Neo4j IDs are integers; the public recommendation contract uses strings. The geographic repository now converts query IDs to integers and result keys back to strings. Sample records carry `postgresql:properties:partial_csv_sample` provenance.

The database/scripted acceptance flow passed all eight steps, including geographic evidence, feedback, deduplication, a stricter budget, trace inspection and restart. It used the separate explicit SGD 4,000 sample requirements and a query-derived school destination; it did not relax the original SGD 3,000 example. Initial and geographic searches each returned four verified listings. Geographic evidence included 46 actual NEAR_PLACE facts and 904 coordinate calculations across two criteria, with known mappings for all 475 listings.

Artifacts are in `dev/recommendationSubagent/.runtime/csv-database-acceptance/`; importer report is `dataPreprocessing/.runtime/csv-sample-import.json`. Regression results: 193 passed, 7 skipped. This is real database acceptance on a partial dataset using a scripted controller. Live model acceptance remains untested because no provider key is configured. See [CSV manual test instructions](../testing/csv-sample.md).
