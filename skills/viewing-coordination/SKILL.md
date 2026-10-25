---
name: viewing-coordination
version: '1.1'
scope: action-service-viewing
---

Viewing drafts use explicitly offset timestamps displayed in Asia/Singapore. Start must be future, end after start and duration at most four hours. Before execution, transactionally check overlap in the customer’s and agent’s simulated local calendars. Coverage is limited to this system’s records; never claim access to either party’s real calendar.

Valid approval binds the action, revision and content hash. Repeating execution of the same revision returns the same simulated external ID. Do not send an action being edited, expired or with unknown result. Record each party’s explicit agreement with its supporting basis. Preserve simulation status throughout.
