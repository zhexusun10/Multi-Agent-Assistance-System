SDK guidance: https://docs.langchain.com/mcp
Architecture guidance: README.md

## Model-test cost limits (explicit user requirement, 2026-10-22)

- Control API spending. A local change must not automatically expand into a full paid-model regression suite. Do not resume the earlier 19 outstanding paid cases automatically.
- Prefer local unit tests, saved-result offline checks and code review; these checks must not call paid models.
- Only when necessary, choose one representative case for the current issue, with at most three model requests in total, serially. Count generation, semantic review, evaluation and repair. Disable SDK automatic retries.
- Do not append a separate LLM evaluator by default or repeat previously passed live/browser cases. State the scope before invoking a paid model. Stop at the limit, and stop immediately on insufficient balance without probing retries.
- Additional cases or spending require a new explicit user instruction. Retain production agent logic; do not remove production validation to reduce test cost.

## English-edition workspace boundary (explicit user requirement, 2026-10-24)

Work only in this English project. The sibling Multi-Agent-Assistance-System is the preserved Chinese edition; do not overwrite its source or configuration. Keep English ports, session databases, runtime process records and retrieval collections separate. Listing data may be read from the existing shared snapshot. Do not push this branch over GitHub main unless the user explicitly requests that publication.
