# NestFinder SG — Multi-Agent Rental Workspace (English edition)

An independent English-language copy of the Singapore rental assistant. Search and revise rental requirements through conversation, compare the recommended homes, inspect full property details, refine preferences through cards, prepare agent enquiries and viewing proposals, analyse replies, and export summaries.

The Chinese project remains in the sibling `Multi-Agent-Assistance-System` directory. This project is `Multi-Agent-Assistance-System-English`. Source files, configuration, dependencies, conversation databases and runtime state are separate. Both editions read the same installed historical property/Neo4j dataset. English intent vectors use a separate Chroma collection; English knowledge articles generate their own versioned collection. Nothing in English startup replaces the Chinese application or imports over its listing graph.

## Start the English workspace

Open Docker Desktop, then run in PowerShell:

```powershell
Set-Location 'D:\Real Estate Agent\Multi-Agent-Assistance-System-English'
.\scripts\start-app.ps1 -SkipDataImport
```

Open **http://127.0.0.1:8020/**. Stop only this edition's recorded application processes with `scripts/stop-app.ps1`. After source changes, build and use `scripts/restart-component.ps1 -Name graph` (or the appropriate component). These commands do not stop the shared data containers or the Chinese application.

| Service | English | Chinese |
| --- | --- | --- |
| Frontend / API proxy | 8020 | 8010 |
| LangGraph service | 3011 | 3001 |
| Listing data API | 8099 | 8089 |
| Retrieval service | 8101 | 8091 |
| Native Recommendation | 8102 | 8092 |

English conversations use `multi_agent_assistance_en`; optional isolated persistence tests use `multi_agent_assistance_en_test`. Native state is in this project's ignored `data/.runtime/recommendation/state.sqlite`. Credentials remain only in the ignored `.env`; do not commit them. This local copy includes its own installed dependencies and data snapshot. For a fresh machine, install dependencies and initialise the shared property data before starting; see [deployment](LOCAL_DEPLOYMENT.md).

## What stays the same

The language changes do not rename API fields, criterion IDs, enums, SQL columns or tool names. They do not change hard/soft semantics, weights, unit conversions, native filtering/scoring/selection, selection gates, display limits, evidence identity checks, viewing thresholds, permissions or booking confirmation rules. Singapore time remains `Asia/Singapore`; user-facing formatting uses English and `en-SG` conventions.

English is used for the interface, generated summaries/enquiries, clarification and error messages, agent prompts/contracts, application skills and curated knowledge articles. User-authored text, exact source quotations, historical fixtures and multilingual parsing vocabulary remain verbatim. Chinese place aliases and number/negation parsing are deliberately retained for compatibility. Historical design/evaluation documents are retained as reference records rather than rewritten as new English test evidence.

## Agent workflow

1. **Requirement Agent:** model-first complete semantic interpretation, two retrieved few-shot examples, strict JSON Schema/Zod validation, verbatim provenance, controlled enums, deterministic unit conversion, real-place resolution, effective-state preview, independent semantic review and bounded repair. Only explicit additions/updates/removals change existing requirements. Missing data never silently removes a real request.
2. **Recommendation Agent:** Jiangshan's native Recommendation 2.0 implementation, with the complete eligible pool, four-stage tools, fused-v2 soft/behaviour scoring, select-v1 ranking/tie sampling/cold-start price exploration, immutable native exchange and final replay/read checks. Request contract remains 1.0 with behaviour/cold-start extensions; response is 2.0. The application requests up to ten homes and initially displays five. A continuation request reveals up to five additional homes without replacing the first batch; raw exchanges retain the native full contract.
3. **Explanation Agent:** an evidence-based property adviser. Read additional SQL details and nearby graph facts only for the selected IDs; introduce real rent, area, cohort positions, layout and useful transport/shopping/food/park features, including facts the user did not request. Vary the narrative rather than imposing a fixed price-area-MRT script. Selection advantages are not invented ranking causes. Structure, per-home coverage, sources, numbers and route claims are validated before independent review. On writing failure, retain diagnostics and use the existing independently validated evidence recovery, honestly marked as not model-reviewed.
4. **Follow-up Agent:** editable first-person enquiry/viewing drafts, manual sending confirmation, original reply capture, claim/negation/contradiction analysis, next enquiries, reminders, calendar and summaries. Drafting, sending and booking are separate states. A model has no permission to send or confirm an appointment on its own.

The orchestrator retains its 70% LLM / 20% template-vector / 10% pattern mixture, with the existing fallback weights. The continuation intent has dedicated English examples and context boundaries; the existing voting weights and retrieval algorithms are retained. Knowledge retrieval retains vector/BM25, RRF, model reranking and sentence-level exact quotation checks.

## Recommended searches

- `Find a rental in D05 for at most SGD 5000 per month, with at least 2 bedrooms.`
- `Change my budget to 4500 and keep everything else.`
- `Prefer a supermarket within 800 metres in a straight line.`
- `Compare the first two homes and help me choose.`
- `Draft a viewing enquiry for listing 1.`
- `Generate a summary document for the current recommendations.`

You can also ask rental-policy questions or ask about existing recommendations directly without repeating a search.

## Refinement and stable recommendations

A card follows the native `selection.cold_start.active` signal, never a mock score or an application-invented threshold. An active valid current signal permits up to three useful unanswered questions supported by the displayed data. Inactive, invalid, stale or exhausted signals do not produce another card. Existing explicit requirements are not asked again.

Cards distinguish exact/minimum/maximum bedroom count, hard versus nice-to-have, area units, property types and preferred rent. **Keep my current budget** retains the cap and adds no preferred-rent condition. Only an explicit submission resumes Requirement → Recommendation → Explanation. Editing forms, reading details or closing them never silently reranks the list. Submitted cards remain read-only in conversation history.

## More recommendations

After the initial five homes, say “recommend more rentals for me” or “these do not work, keep recommending” to add up to five distinct homes. The right-hand list keeps the original five and grows to ten; the introduction describes only the new homes, numbered 6–10. Existing cards and scroll position are retained. Refreshing the page preserves the expanded list. A changed requirement starts a new five-home presentation. Shortages are not padded and this result is capped at ten homes. See [continuation design and offline checks](docs/more-recommendations.md).

## Detail viewing and viewing intentions

Opening details gives a home the main work area while retaining the conversation. The image gallery and known fields are grouped together; missing fields do not occupy rows. Return restores list/scroll position. Numbered navigation follows the server-approved visible list: five initially, up to ten after a continuation request.

Only eligible active detail viewing is recorded. Hidden tabs, minimised/unfocused windows, activity elsewhere and extended inactivity pause timing. The existing quality thresholds remain 15 seconds per home, at least two homes and 60 seconds total. Activity never changes the current list automatically. A later explicit recommendation request carries cumulative viewing and `book_viewing_listings` through `behavior_context` to the native algorithm.

A viewing intention requires valid future start/end times and explicit draft saving, not merely pressing Contact agent. Cancelling/removing times withdraws that signal. An intention is not a confirmed booking: both parties must explicitly agree before confirmation is recorded.

## Manual contact and optional platform integration

Default flow: **edit and copy → send yourself → confirm sent → paste and confirm reply → review analysis and next steps**. No platform credentials are needed for this flow. Replies retain verbatim evidence, identify unresolved terms/conflicts and do not overwrite listing facts or automatically confirm bookings.

WhatsApp Cloud API and Telegram Bot API remain optional advanced integrations. Contact relationship, channel address and permission must be verified; WhatsApp service-window/template restrictions remain enforced. Editing the recipient/content/template/time invalidates approval. Sending uses the persistent outbox and does not retry an unknown result automatically. Reminders create in-app notices, not automatic external messages.

## Installed data and evidence boundaries

The installed 2026-10-09 snapshot contains 21,431 rental listings, 4,657 agents and 418,700 image links. Official geographic catalogues include 55 planning areas, five regions, 188 stations and 613 recorded exits; neighbourhood records distinguish Chinatown/Holland Village from namesake stations.

URA distance is zero inside an area or the nearest boundary distance outside. LTA uses the nearest recorded exit; OSM uses reference geometry. These are straight-line measurements, not verified walking/driving routes or travel minutes. Missing coordinates stay unknown. A bathroom count does not prove exclusive use; property type/photos do not prove amenities. Historical prices/listings do not establish current availability. Unknown hard conditions remain visible and cannot be called verified matches.

## Validation and costs

Use `npm run build`, local Node/Python regression tests and saved offline browser replays first. No paid model suite runs by default. The standing test budget is one representative case and at most three model requests in total, including repairs/reviews, with SDK retries disabled. Never resume old paid suites automatically. A fallback result is not evidence that direct LLM generation passed review.

See [English migration and verification](docs/English_Edition_Migration.md) for exact checks, screenshots, remaining language-data exceptions and the preservation check against the Chinese project. Previous design and Recommendation contracts remain under `docs/` as historical references.
