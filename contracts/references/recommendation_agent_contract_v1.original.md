# Recommendation Agent Integration Contract

**Status:** Proposed v1  
**Contract version:** `1.0`  
**Date:** 13 October 2026  
**Scope:** Singapore residential leasing

The recommendation agent receives the client’s current requirements and relevant listing feedback. It retrieves candidate properties, checks mandatory constraints, ranks eligible listings, and returns evidence explaining the results.

The orchestrator calls this agent when a client first requests recommendations, updates their preferences, or provides feedback that should affect the shortlist. This document is a proposed integration agreement; it does not claim that the interface has already been implemented.

## 1. Responsibilities of each component

| Component | Responsibility |
|---|---|
| Requirements agent | Interpret conversation and produce structured requirements; identify explicit positive/negative feedback |
| Frontend/event collection | Record interactions such as active viewing time, saving, and shortlisting |
| Orchestrator | Assemble the request, invoke the recommendation agent, and route its response |
| Recommendation agent | Retrieve, validate, rank, and return evidence; maintain recommendation state |
| Explanation agent | Turn the returned evidence into a client-facing explanation |

The recommendation agent can return clarification requests, but the orchestrator coordinates asking the client and receiving the updated requirements.

### Internal recommendation components

| Component | Responsibility |
|---|---|
| LLM controller | Choose retrieval, candidate reuse, clarification, or reranking; call tools |
| Profile manager | Accept current stated requirements and maintain a separate inferred-interest profile; deduplicate and age feedback signals |
| Retrieval tool | Apply knowledge-based filters and content/geographic matching |
| Evidence tool | Obtain listing attributes, distances, and KG relationships |
| Constraint validator | Evaluate hard criteria as pass, fail, or unknown |
| Ranker | Combine explicit preference fit and behavioural similarity |
| State store | Retain the full candidate pool, profiles, versions, and cached evidence |
| Trace recorder | Record actual tool execution, results, errors, and timing |
| Response validator | Enforce the response schema and consistency rules |

These are components within one recommendation subagent; each does not need its own LLM. Tools calculate distances, evaluate constraints, and produce scores. The LLM selects the strategy.

## 2. Input contract — RecommendationRequest

| Field | Required? | Meaning and caller responsibility |
|---|---|---|
| `contract_version` | Yes | Version of the interface schema, initially `"1.0"` |
| `request_id` | Yes | Unique ID for this logical recommendation request; reuse it for a retry of the same request |
| `conversation_id` | Yes | Identifies the client conversation; keeps different clients’ recommendation states separate |
| `requirements_version` | Yes | Increasing integer; increment whenever structured requirements change |
| `requirements` | Yes | Complete current requirements, including unchanged criteria |
| `feedback` | No | New interaction events and interpreted listing feedback; default is an empty list |
| `previous_state_id` | No | State reference returned by the previous recommendation call |
| `result_limit` | Yes | Maximum number of verified recommendations to return, such as five |

**Send the complete requirements snapshot every time.** If only the budget changes, still send the bedroom and location requirements. The recommendation agent compares snapshots to decide what work to repeat.

The two versions serve different purposes: `contract_version` describes the message format; `requirements_version` describes the client’s preferences. A feedback-only update can use the same requirements version with a new request ID.

## 3. How to represent requirements

The `requirements` object contains `listing_type: "RENT"` and a `criteria` array. Each criterion describes one condition:

```json
{
  "criterion_id": "monthly-rent",
  "field": "price",
  "operator": "lte",
  "value": 3000,
  "unit": "SGD/month",
  "strength": "hard"
}
```

This means monthly rent must be no more than SGD 3,000.

| Criterion field | Meaning |
|---|---|
| `criterion_id` | Stable identifier; keep it unchanged when editing the same requirement |
| `field` | Attribute being assessed, such as price, bedrooms, district, or place proximity |
| `operator` | Comparison: `eq`, `in`, `gte`, `lte`, or `between` |
| `value` | Desired value, allowed values, or range |
| `unit` | Required where needed to distinguish money, distance, area, or time |
| `strength` | `hard` means mandatory; `soft` means preferred |
| `weight` | Positive relative importance for a soft preference |
| `target` | Resolved place ID or category for geographic criteria |
| `distance_type` / `transport_mode` | How proximity or travel time should be assessed |

A soft preference might be:

```json
{
  "criterion_id": "near-supermarket",
  "field": "place_proximity",
  "operator": "lte",
  "value": 800,
  "unit": "m",
  "strength": "soft",
  "weight": 3,
  "target": {
    "category": "Supermarket"
  },
  "distance_type": "straight_line"
}
```

Weights express relative importance; they do not need to sum to one. A weight of three has three times the influence of a weight of one in a normalized weighted score.

Hard constraints cannot be compensated for by high scores on other preferences. Geographic criteria must distinguish straight-line distance from walking distance and commute time.

The team should agree on a typed field/operator registry during implementation. Unsupported capabilities must be distinguished from malformed requests. Missing data must never be silently treated as proof that a criterion passes.

## 4. Feedback input

Feedback supports inferred interests without silently changing stated requirements.

| Feedback field | Meaning |
|---|---|
| `event_id` | Unique event identifier, used to prevent duplicate processing |
| `listing_id` | Property the event concerns |
| `occurred_at` | Event timestamp |
| `event_type` | For example, active view, save, shortlist, or explicit attribute feedback |
| `payload` | Typed details appropriate to the event |

An active-view event carries active duration. Explicit feedback can identify an attribute and sentiment—for example, “floor area: too small.” Use timezone-aware timestamps.

Browsing time and question count are weak interest signals. A question is not automatically positive feedback. Explicit requirements take precedence over inferred interests.

## 5. Output contract — RecommendationResponse

| Part | Contents | How the teammate uses it |
|---|---|---|
| Identity | `contract_version`, `request_id`, `conversation_id`, `requirements_version` | Match the response to the request and reject obsolete results |
| `status` | Success, clarification, no match, or error | Decide the next workflow action |
| `recommendations` | Verified ranked shortlist | Display results and pass them to the explanation agent |
| `unverified_candidates` | Candidates with no confirmed hard failure but at least one unknown mandatory criterion | Present separately if useful; never describe them as verified matches |
| `evidence` | Source facts supporting criterion results and explanations | Ground client-facing statements |
| `clarifications` | Questions and affected criterion IDs | Route back through the requirements workflow |
| `relaxation_suggestions` | Proposed requirement changes and reasons | Ask the client whether they want to change requirements |
| `warnings` | Limitations such as incomplete retrieval coverage | Avoid overstating result completeness |
| `state` | State ID, candidate-pool reference, coverage, and counts | Reuse on the next request |
| `execution` | Strategy, trace reference, tool calls, timing, and scoring version | Debug and evaluate the system |
| `error` | Structured code and message when execution fails | Handle failure appropriately |

This document uses these as top-level response fields. If the team chooses a nested result envelope instead, agree on that layout before generating the executable schema.

## 6. What each recommendation contains

- **Listing identity and summary:** ID, title, rent, bedrooms, district, and source URL where available.
- **Rank and score:** final position and preference-fit score; the score is not a probability of renting.
- **Criterion results:** pass/fail/unknown, observed values, units, and evidence references.
- **Score breakdown:** preference scores, weights, and contributions; inferred-interest contributions remain identifiable.
- **Explanation evidence:** strengths, compromises, and unknowns.

For example, the explanation agent should receive facts equivalent to:

> Listing A costs $2,800, has two bedrooms, and is 650 metres from a recorded supermarket in straight-line distance. It meets the mandatory criteria. Supermarket proximity contributed strongly because the client assigned it high priority. Current availability is unverified.

This is an illustrative example, not a claim about an actual listing. If confirmed availability were a hard requirement, this listing would be unverified rather than eligible.

The explanation agent uses the facts to generate wording. It should preserve the returned ranking and distinguish stated preferences from inferred interests.

## 7. Status and routing

| Status | Meaning | Orchestrator action |
|---|---|---|
| `success` | At least one verified eligible recommendation | Send shortlist and evidence to the explanation agent |
| `needs_clarification` | Ambiguity blocks reliable assessment | Obtain clarification and send updated requirements |
| `no_match` | Assessment completed with no verified matches in the assessed pool | Explain limitations and offer proposed relaxations |
| `error` | Execution could not complete | Handle or retry according to the error code |

A response may contain fewer than `result_limit` recommendations. Never add unsuitable listings merely to fill the requested count.

“No match” does not establish that no suitable property exists in Singapore. The response must state whether retrieval covered the full relevant dataset or only a limited candidate pool.

Do not report zero assessed candidates when no assessment occurred; omit unavailable metrics or represent them as null according to the final schema.

## 8. State and execution trace

The state store retains the full candidate pool, requirements, inferred-interest profile, evidence, and dataset/scoring versions. The orchestrator passes `state_id` back as `previous_state_id` without needing to understand the internal storage format.

State should include retrieval scope and whether coverage is complete or truncated. This lets the agent decide whether existing candidates are sufficient after a preference change.

The execution trace records observable actions:

- Tool name, call ID, sequence/dependencies, and structured arguments.
- Success/failure, duration, and result or result reference.
- Brief decision summaries, such as “retrieved additional candidates because the budget increased.”

Application code records actual tool execution. Large candidate sets and raw tool responses can be stored behind references accessible to authorized components. Internal LLM chain-of-thought is not part of this contract.

## 9. Integration rules

- All hard criteria must pass before a listing enters `recommendations`.
- Missing values produce `unknown`; they never silently become zero or a pass.
- Suggested relaxations require a new explicit requirements update.
- Evidence and candidate references must be resolvable.
- Counts refer to the deduplicated assessed candidate pool: eligible + unverified + excluded.
- A confirmed hard failure takes precedence over unknown criteria when classifying a candidate.
- Expired or unavailable previous state allows a fresh search.
- The orchestrator tracks the latest relevant request ID as well as requirements version, because feedback-only requests may share a version.
- Every response must pass schema and consistency validation before being returned.

## 10. Items to finalize before implementation

This is an explanatory contract draft. Agree on the following when creating the authoritative models and JSON Schema:

- Supported criterion fields, allowed operators, value types, and units.
- Feedback event names and typed payloads.
- Ranking utility functions, missing soft-data policy, and scoring version.
- State retention, reference retrieval, and freshness rules.
- Structured clarification, warning, and error codes.
- Result limits, unverified-candidate limits, and trace size limits.

