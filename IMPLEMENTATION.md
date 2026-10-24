# Implementation and operations — English edition

The Chinese predecessor remains unchanged in the sibling project. Older design and evaluation transcripts under `docs/` and `evaluation/` remain historical references, not new English test evidence.

## Architecture and ownership

The execution chain remains browser → same-origin FastAPI → TypeScript LangGraph/master and `WorkflowService` → role workers. PostgreSQL persists sessions, operations, tasks, events, references, actions, artifacts and checkpoints. No replacement Python application orchestrator was added.

`a.ts`, `b.ts`, `c.ts` and `d.ts` use `createRoleRunner` with separate session/role checkpoints. `master.ts` manages intent, dependencies and recovery; `domain/workflow.ts` coordinates testable business transitions.

## A: requirement interpretation and refinement

The model interprets complete semantics using strict structured output and two selected few-shot examples. JSON/Zod schemas, verbatim quotations, field/enum/unit rules, effective-state preview, authorised removals, independent semantic review and bounded repair validate its proposal. Rules normalise explicit facts; they do not replace model-first interpretation.

Only explicit changes alter existing requirements. Criterion IDs remain stable, versions change only with effective structured changes, and unspecified soft weights default to one. Hard budget limits may coexist with softer preferred rent. Unsupported furnishing, private-use and route requirements remain visible as unknown.

Place IDs come from real catalogues. Planning areas, neighbourhoods, stations, campuses and generic amenities remain distinct. A named neighbourhood is not silently replaced by its MRT station. Nearby can prefer closer homes without inventing a hard radius; missing/ambiguous identities require clarification. Bathroom count never proves private use.

Native `selection.cold_start.active` controls cards. Up to three useful unanswered fields with sufficient coverage are offered. Structured selections enter the same Requirement merge/validation path without another extraction call. `any`/`skip` create no false constraints; keeping the budget preserves its cap and adds no preferred-rent condition. Explicit submission resumes A → B → C. Submitted cards remain read-only; stale/cross-session submissions are rejected and repeated submissions are idempotent. Inactive/exhausted plans stop producing cards.

## B: native recommendation

The default gateway uses Jiangshan's native Recommendation 2.0 implementation with complete requirements, dataset, previous state, feedback and behaviour context. Its native tools load candidates, evaluate hard conditions, score soft/behaviour features, select, gate and validate final replay/read results. Top5 request, display and Explanation scopes agree; raw exchanges retain native provenance.

No native filtering, scoring, weighting, tie sampling, price exploration or cold-start threshold changed for English. Optional baseline/remote adapters remain for their original integration purposes. A historical eligible listing is not a guarantee of current availability.

## C: grounded property guidance

Accepted Recommendation output determines selected IDs, order, observations and condition statuses. Read-only SQL details/photos and nearby graph records enrich only those homes. Evidence records bind permitted numbers/entities, same-metric distances, cohort positions and derived comparisons to source chains.

The English writer explains useful features and trade-offs, including known information the user did not request. Known rent/area are covered; openings and other features can vary. Whole-group relative positions are preferred over repetitive pairwise price scripts. Unit rent requires comparable rental scope and a supported relative judgement. Lower rent or a supermarket does not automatically become a ranking cause.

Strict structure, target order, per-home coverage, evidence, numbers, uncertainty and route-claim checks precede independent review. Straight-line distance cannot establish walking time or commute experience. Detail records do not override unverified native conditions. Missing data stays missing. Writer failure retains diagnostics and uses the existing independently validated evidence recovery; its metadata says model review was not run.

English length limits accommodate translation: 1,000 characters per paragraph and 5,200 overall, retaining the 700-English-word cap. Request/repair/time limits stay unchanged. Official source names and exact quotations may retain their language; unrelated generated non-English prose is not permitted.

## D: follow-up and viewing proposals

Listing references bind to the current source request/operation. Drafts use first-person English and strip internal routing commands. Manual flow is edit/copy → send yourself → confirm sent → paste original reply → review analysis. Contacts are not invented. Optional WhatsApp/Telegram retain address, relationship, recipient permission, template/service-window and approval checks.

Drafting, sending, delivery, replies and confirmation are separate states. Edits invalidate approval by revision/content hash. Persistent execution keys prevent duplicate sending; unknown outcomes are not automatically retried. Viewing times must be valid future Singapore intervals, at most four hours, and pass conflict checks. Both parties must explicitly agree before confirmation. Workspace reminders do not send external messages.

Reply analysis preserves exact quotations, negation, independent clauses, contradictions and unknowns. Minimum lease does not prove an exact requested term; listed furniture does not prove full furnishing. Replies do not rewrite historical facts or automatically change search requirements.

## Attention and stable results

Detail timing retains monotonic-clock, visible-surface, focus, activity, idle, timer-gap and exclusive-lease checks. Hidden tabs, minimised/unfocused windows and unrelated dialogs pause timing. Photo dialogs retain their listing identity. Outbox retries preserve event IDs. Existing thresholds remain 15 seconds per home, two homes and 60 seconds total.

Viewing intention requires explicitly saving a draft with valid times; opening Contact agent or generating a default draft does not qualify. Cancellation/time removal withdraws it. The next explicit recommendation carries cumulative durations and `book_viewing_listings`; intention is not booking confirmation. Reading details, returning and editing forms never silently replace the current ranking.

## Persistence and reliability

| Mechanism | Guarantee |
| --- | --- |
| Shared SQL transactions | State changes, consumed results, next tasks and events commit together. |
| Idempotency | Messages, steps, consumed results, feedback and execution keys are checked separately; reused IDs with different content are rejected. |
| Stale-result protection | Active operation, requirements version and latest request ID must match. |
| Task identity | Session, role, operation, status, references and stable SHA-256 digests are checked. |
| Recovery | Interrupted work and persisted results resume without duplicating context. |
| Event delivery | Ordered database event IDs, cursor-based SSE replay and frontend state reconciliation. |
| Isolation | Owner Bearer credentials scope sessions, references, actions and artifacts. |
| Tool governance | Role permissions precede cache access; cache scope, call/time budgets, circuit breakers and traces remain. |
| Input/output safety | Same-origin CSP, DOM/HTML escaping, body/query limits and pagination duplicate/loop/truncation checks. |

The random Bearer credential in browser storage is a local/classroom ownership mechanism, not production identity/RBAC. Services bind to loopback. Clearing storage may lose that browser's conversation access.

## Data, knowledge and operation

Both editions read the installed 2026-10-09 snapshot: 21,431 listings, 4,657 agents and 418,700 image records. SQL supplies listing facts; Neo4j supplies places/relationships. URA areas/regions, LTA stations/exits and reviewed OSM neighbourhoods complement the original OSM/campus data. Distance is zero inside an area or nearest-boundary outside; station distance uses recorded exits. Metrics remain straight-line and route-unverified unless real route evidence exists.

Eight curated English rental/workspace articles retain their IDs, source URLs and review dates. Local multilingual embeddings, BM25/RRF retrieval, versioned index publication and grounded excerpts remain. English intent vectors have a separate namespace and English knowledge has its own content-hashed generation. Knowledge answers cannot change listing or Recommendation facts.

Application `skills/*/SKILL.md` constrain intake, guidance, follow-up and viewing; their hashes enter traces. Skills cannot grant permissions or relax hard checks.

See [README](README.md), [deployment](LOCAL_DEPLOYMENT.md) and [English migration](docs/English_Edition_Migration.md). English ports are 8020/3011/8099/8101/8102 with independent conversation/native state. Backend edits require build/restart. Logs, indexes and credentials remain ignored. Do not delete Docker volumes for routine shutdown. Model-test spending follows `AGENTS.md`; a language edit does not authorise a paid suite.
