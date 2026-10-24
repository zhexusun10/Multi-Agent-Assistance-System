# Recommendation terminal: usage guide (contract 2.0)

`python -m recommendation.terminal` is the developer terminal of the native recommendation
subagent. It talks to the same `RecommendationService` and SQLite state as the HTTP sidecar. It
plays the orchestrator's role: it stages full requirements and `behavior_context` snapshots (main contract), submits
requests and inspects runs. The debugging commands live in `recommendation/terminal/debug.py`;
`recommendation/terminal/__main__.py` wires them in. Every earlier command still works.

```bash
# fixture data, deterministic scripted controller (no LLM, no databases)
.venv/bin/python -m recommendation.terminal --data-mode fixture --controller scripted
# live databases (read-only) with the scripted controller and a throw-away state file
.venv/bin/python -m recommendation.terminal --data-mode database --controller scripted --state-path /tmp/rec-state.sqlite
# live databases + live model (prints progress events)
.venv/bin/python -m recommendation.terminal --data-mode database --controller live
```

| Option | Meaning |
|---|---|
| `--data-mode database\|fixture` | listing/graph source (default `database`) |
| `--controller live\|scripted` | LLM tool loop or the deterministic pipeline (default `live`) |
| `--conversation-id <id>` | resume a conversation (with the same `--state-path`) |
| `--state-path <file>` | SQLite state (default `RECOMMENDATION_STATE_PATH` or `recommendation/.runtime/state.sqlite`) |
| `--config <file>` | alternative settings file of the same schema (default: `recommendation/settings/recommendation.yaml`) |
| `--script <file>` | run the commands of a file (one per line, `#` comments) and echo each one after the prompt; for transcripts |
| `--brief` | print a response summary instead of the full JSON (the response artifact file still has the full JSON) |
| `--check-connections` | contact both stores and exit (no model call); the `llm` entry reports the RECOMMENDATION_LLM_* configuration (model, non-secret settings, the missing/invalid variable) |

`help` prints the command list. Staged input (requirements, feedback, a loaded request, session
settings, the seed override) is never saved on exit. Committed runs, traces and pool-cache entries
are saved in the state file.

## Commands

### Input and submission

| Command | What it does |
|---|---|
| `requirements [@file]` | Replace the full requirements snapshot. The requirements version advances only when the snapshot changes. |
| `feedback [@file]` | Replace the full `behavior_context` `{version, as_of, listing_view_durations, book_viewing_listings}`, as the orchestrator does (merge with main c7dc536); nothing is merged with earlier input natively. It is validated first (one summary per listing, timezone-aware times, counted viewing signal). |
| `feedback-template [file]` | behavior_context template for the last response: the previous INPUT's cumulative summaries and viewing drafts (never native history) plus, for each newly shown listing, a summary with a `duration_seconds: null` placeholder. Fill in the cumulative seconds and `interest_eligible`. An unfilled template is rejected. |
| `feedback-example [file]` | Filled sample: the previous INPUT's summaries plus 90 s (rank 1) / 30 s (others) added for the shown listings, `interest_eligible: true`. |
| `load-request @file` | Stage a full request body (main contract 1.0 + `cold_start`). It is validated in full, and 2.0 request bodies are rejected. The terminal stages its requirements, behavior_context, cold_start and result_limit (feedback events are validated, not staged; the `cold_start` exit fields are accepted but ignored by the cold-start decision since mpd-v1). The file's `request_id`, `conversation_id`, `previous_state_id` and `requirements_version` are never reused; the terminal manages them and prints them. |
| `show-input` | The request body `recommend` would send (`request_id` shows as "minted by recommend"; a failed pending request shows its real ID), plus the seed override and session settings. |
| `validate-input` | Request schema check plus the run-consistency checks (state reference vs head, requirements version, behavior_context, result_limit). Nothing is submitted. |
| `recommend` | Submit the staged request. Each call mints a **new request_id**. The only exception is retrying a failed pending request, which keeps its ID. Any input change (requirements, feedback, load-request, set-config, set-seed) drops the pending request, so the next recommend gets a new ID. |
| `replay <request-id>` | Re-submit the stored body of a committed request. Prints `REPLAY of <id> (stored response)`, then the stored response (verified by the replay gate) and whether it is unchanged. It never creates a run and never changes the terminal's current response or staged input. Changing settings never alters a replay. |
| `show-state`, `reset`, `exit` | As before: `reset` starts a new conversation, and stored data is kept. |

### Settings (session only)

| Command | What it does |
|---|---|
| `show-config [dotted.key]` | Effective settings, `config_hash`, `selection_hash` and session overrides (or one key or section). |
| `set-config <dotted.key> <json>` | Session override, validated by the same loader as the YAML file. An invalid key, value, range or weight sum, or a secret-like key, is rejected and the settings stay unchanged. **The settings file is never written.** The override applies to **new requests only**: the service freezes settings per request, and stored runs keep their configuration. To keep weights summing to 1, set a whole section in one go: `set-config ranking {"soft_weight":0.5,"behavior_weight":0.5}`. |
| `reset-config` | Drop every session override. |
| `export-config [file.yaml\|file.json]` | Write the effective settings (with a provenance header for YAML). The file loads with `--config` to the same `config_hash`. |
| `set-seed <value>` / `set-seed off` | Test override of the selection **base seed** (R3; the tie / stage A / stage B streams derive from it). It is recorded in the trace (`seed_override.value`, final_selection `params.seed_override`, `selection_input.seeds.override`). Only the terminal sets it; the service API never does. |

### Inspection

The inspection commands read, in this order:

1. **The step session**, when the last command was `step` / `run-until`. Every line is labelled
   `INTERMEDIATE (step session: ...; this is not a recommendation)`.
2. **This terminal's latest run**: the dispatcher of that run is retained in memory (pool rows,
   criterion results, evidence, full-pool score components, selection), so nothing is
   re-queried.
3. **A committed run this terminal did not execute** (after a restart). The pool comes from
   pool_cache by pool_version, and scores from the stored response items plus the trace preview.
   `inspect-listing` re-runs the stored request read-only and labels the output
   **RECOMPUTED**.

| Command | What it shows |
|---|---|
| `show-pool [--page N]` | Pool identity (pool_key, pool_version, data_version, cache built/reused, outcome hit/miss), partition counts and unknown reasons. Rows come in score order once scored; page size = `trace.preview_rows`. |
| `show-resolution` | One line per geographic criterion (RESOLVED / AMBIGUOUS / UNRESOLVED, entity, method, reason, "needs clarification (n candidates)") and the full resolution records with candidates. |
| `show-scores [--top N]` | Effective weights, suppressed / dropped / active similarity features, and the top N of the full-pool order with soft / behaviour / appointment / browsing / combined scores, coverage and selection reason. |
| `show-cold-start` | Cold-start status (detector mpd-v1): evaluated, active, reason, detector / detector_version, candidate_window, evaluated_count, D (`mean_pairwise_difference`) and the threshold, plus the unused request `cold_start`. A stored run from before mpd-v1 shows its legacy C / B / S record as stored (not reinterpreted as D). |
| `show-selection` | Selection summary (mode, seed, leaders, tie group, exploration bins) and the selected items with rank, score_rank, reason and note. |
| `inspect-listing <id>` | One listing: row, criterion results, evidence, score breakdown with contributions, score_rank, and whether and why it was selected. A listing outside the run's pool is read fresh **by ID** and evaluated against the criteria. That output is labelled `recomputed`, because the run never stored data for it. |
| `show-trace [request-id] [--debug] [--latest]` | The stored slim trace (without an ID: the newest trace of the conversation). With a request ID: that run's own trace (found by `response.execution.trace_id`); a newer trace of the same request (a `replay`) is named, and `--latest` shows it. Replay traces (replay gate only) are labelled. A `debug` block is summarised unless `--debug` is given; for a run traced in summary mode, `--debug` explains how to enable debug rows (`set-config trace.mode "debug"`). Legacy v1 traces are labelled. |
| `export [dir]` | `request.json`, `response.json`, `trace.json` and `config.json` (the run's frozen settings and hashes) of the latest committed run (`trace.json` is the run's own trace; after a `replay` of it the replay trace is added as `latest-replay-trace.json`). Default directory: `<state dir>/exports/run-<request_id>`. |

### Step mode (`--controller scripted` only)

| Command | What it does |
|---|---|
| `step` | Run the next stage of the staged request: resolve_entities -> build_pool -> get_geographic_evidence -> rank_candidates -> select -> final_gate. `select` is the code step after the agent loop: cold-start detection and selection on the ranking artifact. |
| `step <stage>` | Run exactly that stage. The dispatcher enforces dependencies, so a stage whose predecessor is not current fails with `STALE_REFERENCE: Call <predecessor> for the current inputs first`. Re-running a stage discards every later stage. |
| `run-until <stage>` | Run stages in order up to `<stage>`. Accepted names: the tool names, the trace stage names (entity_resolution, pool_build_or_load, geographic_evidence, scoring, cold_start_diagnostics, price_sampling, final_selection, final_gate) and short aliases (resolve, pool, geo, rank, select, selection, gate); cold_start_diagnostics, price_sampling, final_selection and selection map to `select`. |
| `step reset` | Discard the step session. |

Rules:

- The step session runs **the request `recommend` will submit** (same request_id, settings and
  seed override). After `run-until final_gate`, `recommend` returns the selection the session
  showed, unless the data changed in between.
- Every stage runs inside a state transaction that is **rolled back**, so pool-cache writes are
  discarded and nothing is committed. `final_gate` is a dry run. Step output never becomes the
  terminal's response and is never reported as a successful recommendation; the selection is
  printed as "candidate selection (intermediate, not a recommendation)".
- An input change invalidates only the affected stages and prints what was invalidated:

| Change | First invalidated stage |
|---|---|
| listing type; set of geographic criteria or their target fields (destination, category, distance type, transport mode); settings `geography.*` / `entity_resolution.*` | resolve_entities |
| any hard criterion (**a hard-criteria change invalidates the pool**); settings `cache.*` | build_pool |
| a soft geographic criterion (e.g. its radius) | get_geographic_evidence |
| feedback, soft non-geographic criteria, weights, result_limit, cold_start, new request_id (seeds), seed override, other settings | rank_candidates |

  Any invalidation also discards a passed final_gate.
- `recommend` closes the step session.
- The controller time budget (`controller.timeout_seconds`) applies per step. Tool calls count
  against `controller.max_tool_calls` for the whole session; use `step reset` if a long session
  exhausts it.

### Live-mode progress events

`recommend` prints structured progress lines in both controller modes:

```text
[progress] model: turn 1 start (messages=2)
[progress] model: turn 1 end 812.4 ms (tool_calls=4)
[progress] tool: build_pool start
[progress] tool: build_pool end ok 41.0 ms (pool=306, ranked=0, artifact=False, evidence=0)
[progress] tool: rank_candidates end ok 18.2 ms (pool=306, ranked=306, artifact=True, evidence=0)
[progress] model: turn 2 end 640.0 ms (tool_calls=0, final answer)
[progress] gate: selecting from ranking artifact <trace>/ranking/<digest> (306 ranked), then final_gate
[progress] gate: final_gate ok (selected=10, rows_read=10) 180.6 ms
```

(Illustrative lines.) A failed gate is reported (`gate: final_gate error STALE_EVIDENCE: ...`) before the agent
receives the structured failure report and repairs. A response summary follows: status, pool `cache=built (MISS)` / `reused (HIT)`, selection
mode, seed, the cold-start status (active / inactive with its reason, D, the threshold and the window)
and the items.

## Typical sessions

```text
# first recommendation with a fixed seed and a smaller result limit
requirements @recommendation/examples/requirements.json
set-config recommendation.default_result_limit 3
set-seed demo
recommend
show-pool
show-scores --top 5
show-selection
inspect-listing fixture-03          # retained data of this run
inspect-listing fixture-11          # outside the pool: labelled recomputed

# feedback re-rank (orchestrator-style full snapshot)
feedback-template /tmp/fb.json      # fill duration_seconds, then
feedback @/tmp/fb.json
recommend                           # pool cache=reused (HIT), behaviour branch now scored

# config comparison with the fixed seed
recommend                           # same settings + seed: same selection under a new request_id
set-config cold_start.enabled false
recommend                           # only the settings differ

# cache miss, entity ambiguity, replay
requirements @/tmp/stricter-budget.json   # hard change -> cache=built (MISS)
recommend
requirements @/tmp/id-name-conflict.json  # destination_id URA:AREA:CLEMENTI + name "Bedok"
recommend                                 # needs_clarification
show-resolution                           # geo: AMBIGUOUS ... 2 candidate(s)
replay <first request id>                 # REPLAY of <id> (stored response)

# step mode
step rank_candidates                # STALE_REFERENCE (dependencies enforced)
run-until build_pool
set-seed other                      # invalidated: rank_candidates ... (pool kept)
run-until final_gate                # INTERMEDIATE, dry-run gate
recommend                           # submits the previewed request
```

## Known effects and limits

- **Cold start follows D every round (mpd-v1, 2026-10-24).** D is the mean pairwise difference of
  the top `cold_start.detector.candidate_window` combined scores; cold start is active while D is
  below `cold_start.detector.min_mean_pairwise_difference`. With no soft criteria and no behaviour
  signal every score is equal, so D = 0 and cold start stays active; a staged `cold_start` exit does
  not change the decision. D shows score spread only, not whether the requirements are sufficient.
  The terminal help and `show-cold-start` say so. `inspect-listing` on a run stored before mpd-v1
  drops the removed C/B/S settings and recomputes under the current detector (it says so).
- `set-seed` replaces the base seed, so two requests with the same settings and seed draw the
  same tie / exploration samples. Without it, every new request_id draws a new seed.
- `inspect-listing` for a listing outside the pool reads that one row by ID and evaluates the
  current criteria. Its geographic evidence is computed for that row only, and the output is
  always labelled `recomputed`.
- Session overrides last for the terminal process (also across `reset`); `export-config` saves
  them as an experiment file. The production source stays the committed YAML.

## Validation artifacts (P6)

- Automated transcript test: `recommendation/tests/test_terminal_debug.py::test_terminal_transcript_covers_the_p6_flows`
  (fixture data, scripted controller, temporary state). It covers the first recommendation,
  feedback re-rank, config comparison with a fixed seed, cache hit/miss, entity ambiguity,
  single-listing inspection, original-request replay and export. Other tests in the same file
  cover each command's rules.
- `p6/terminal_transcript.py` produces both recorded transcripts:
  - `p6/fixture-terminal-transcript.txt`
  - `p6/live-terminal-transcript.txt`: live PostgreSQL + Neo4j, read-only, scripted controller
    (no LLM), temporary state file.
