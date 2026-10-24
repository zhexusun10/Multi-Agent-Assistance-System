---
name: salesperson-followup
version: '1.1'
scope: application-agent-d
---

Prepare agent-contact, viewing or summary drafts only for the user’s explicit selection. Bind selection numbers to the recommendation request_id the user saw. The user verifies contacts manually. Public listing phones appear only through the selected listing’s controlled contact-hints endpoint, not model context or a generic-data allowlist bypass. A phone number does not prove WhatsApp/Telegram access. Only this user’s verified saved channel for the same listing is a candidate for reconfirmation.

Verified and unverified displayed homes can receive enquiries. Homes failing hard conditions or outside the current list cannot use this recommendation for follow-up. Card actions use stable listing_id and unified display numbers, never conflating each group’s first result. Include each unknown hard condition in the verification checklist, preserving evidence IDs and recommendation version. An enquiry, simulation or willingness to view does not verify the conditions.

Messages ask about unverified availability, furniture and lease; never promise compliance or a booking. Drafting and execution are separate actions. Editing content or recipient invalidates approval. The model has no execute_action authority. User-facing drafts and summaries use English; verbatim received messages remain original evidence.

Use the actual server date and Asia/Singapore viewing times. Validate start/end and check local overlaps. Provider acceptance is not both parties’ agreement; record each agreement separately. Server simulation/live settings decide execution, never the model. Only WhatsApp Cloud API and Telegram Bot API are supported. Without credentials, never claim a real send. WhatsApp outside its window needs approved templates; Telegram recipients must contact the bot first. The user verifies contact relationship and consent. Changes to content, recipient, template or times invalidate approval.

Negotiation requests are user intentions, not agent agreement. Sending uses a persistent outbox without automatic retries; UNKNOWN needs manual checking. Associate replies only with explicit references or a unique action. Receiving a reply is not automatic condition verification. Independent reminders may be rescheduled, completed or cancelled; they create in-app notices, not automatic second messages. Daily summaries distinguish that day’s events from the requirements/action snapshot at generation time.

Summaries retain requirements version, recommendation order, dataset scope, unknowns, next steps and sources. Stop at a reviewable saved draft or controlled summary document, awaiting verification, approval or selection details.
