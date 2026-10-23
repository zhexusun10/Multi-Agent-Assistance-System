"""Terminal debugging commands (spec section 11, R14; P6 part 2).

Wired into ``python -m recommendation.terminal`` (terminal/__main__.py, ``handle``). Everything
here is terminal-only: the service API never sees a session config override or a seed override
except through the run it executes for the terminal, and both are recorded in that run's trace.

Pieces:

- ``Events`` / ``ObservedController``: structured progress events while a request runs (tool
  start/end with counts and duration, model turns, final gate). The controller wrapper also
  applies the terminal seed override (``set-seed``) to the run's dispatcher and retains that
  dispatcher, so show-* / inspect-listing read THIS run's in-memory data (pool rows, criterion
  results, evidence, full-pool score components, selection) without re-querying anything.
- ``StepSession``: scripted step mode (``step`` / ``run-until``). It runs the dispatcher's
  pipeline stages one at a time for the staged request (the request ``recommend`` would submit),
  each inside a rolled-back state transaction, so nothing is committed or cached. The dispatcher
  enforces stage dependencies; an input change invalidates only the affected stages (hard
  criteria -> the pool and later stages). Step output is always labelled INTERMEDIATE and never
  becomes the terminal's response.
- command handlers (``handle``) for show-config, set-config, reset-config, export-config,
  show-input, validate-input, load-request, set-seed, show-pool, show-resolution, show-scores,
  show-cold-start, show-selection, inspect-listing, show-trace, export, replay, step, run-until.
"""

import json
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from recommendation.contracts.errors import RecommendationError
from recommendation.contracts.request import RecommendationRequest
from ..config import RecommendationConfig
from ..settings.loader import lookup
from ..tools.registry import PIPELINE
from ..tracing import slim

STEP_STAGES = PIPELINE + ("final_gate",)
# Stage names accepted by step / run-until: tool names, trace stage names and short aliases.
STAGE_ALIASES = {
    "resolve_entities": "resolve_entities", "entity_resolution": "resolve_entities", "resolve": "resolve_entities",
    "build_pool": "build_pool", "pool_build_or_load": "build_pool", "pool": "build_pool",
    "get_geographic_evidence": "get_geographic_evidence", "geographic_evidence": "get_geographic_evidence",
    "geo": "get_geographic_evidence",
    "rank_candidates": "rank_candidates", "rank": "rank_candidates", "scoring": "rank_candidates",
    "cold_start_diagnostics": "rank_candidates", "price_sampling": "rank_candidates",
    "final_selection": "rank_candidates", "selection": "rank_candidates",
    "final_gate": "final_gate", "gate": "final_gate",
}
# Settings sections whose change invalidates step stages from (index into STEP_STAGES).
CONFIG_INVALIDATES = {"geography": 0, "entity_resolution": 0, "cache": 1}
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
INTERMEDIATE = "INTERMEDIATE (step session: state writes rolled back, nothing committed; this is not a recommendation)"

HELP = """Commands (staged input is sent by `recommend`; nothing staged is saved on exit):
  check-connections                      contact the configured stores (no model call)
  requirements [@file]                   replace the full requirements snapshot
  feedback [@file]                       replace the full feedback snapshot (orchestrator semantics, no merge)
  feedback-example [file]                filled sample snapshot for the last response
  feedback-template [file]               previous-round template: one browsing record per shown ID
                                         (duration null placeholder) + the previous INPUT's appointments
  load-request @file                     stage a full contract-2.0 request (requirements, feedback,
                                         cold_start, result_limit); IDs/version/state stay terminal-managed
  show-input | validate-input            staged request body | schema + run-consistency checks (no run)
  recommend                              submit: always a NEW request_id, except retrying a failed pending one
  replay <request-id>                    re-submit a committed request body: prints the stored response
  show-state | reset | exit
Settings (session only; the settings file is never written; new requests only):
  show-config [dotted.key]               effective settings, hashes and session overrides
  set-config <dotted.key> <json>         validated override, e.g. set-config selection.tie_max_score_span 0.05
                                         or a section merge: set-config ranking {"soft_weight":0.5,"behavior_weight":0.5}
  reset-config                           drop every session override
  export-config [file.yaml|file.json]    effective settings (loadable with --config)
  set-seed <value> | set-seed off        test override of the selection base seed (recorded in the trace)
Inspection (latest run's retained data, or the step session):
  show-pool [--page N]  show-resolution  show-scores [--top N]  show-cold-start  show-selection
  inspect-listing <id>                   one listing; data not retained by this run is labelled 'recomputed'
  show-trace [request-id] [--debug]      stored slim trace (--debug includes the debug rows block); with a
                                         request ID: that run's own trace (--latest: its newest, e.g. a replay)
  export [dir]                           request, response, trace and effective config of the latest run
Step mode (--controller scripted only):
  step [stage]                           run the next stage (or exactly <stage>; dependencies enforced)
  run-until <stage>                      run stages up to <stage>: resolve_entities, build_pool,
                                         get_geographic_evidence, rank_candidates, final_gate
  step reset                             discard the step session
Notes: cold start is exited only when C (soft evidence coverage of the top K), B and S meet their
thresholds; without soft criteria C = 0, so cold start cannot be exited without soft criteria.
Live mode prints progress events (tool start/end with counts and duration, model turns, gate)."""


# --- progress events and the observing controller ---------------------------------------------
class Events:
    """Structured progress events (also kept in ``log`` for tests)."""

    def __init__(self, out=print):
        self.out = out
        self.log = []

    def emit(self, kind, text, **fields):
        self.log.append({"kind": kind, "text": text, **fields})
        self.out(f"[progress] {kind}: {text}", flush=True)


def _counts(result):
    return {"pool": result.candidate_count, "ranked": result.verified_count,
            "selected": len(result.selected_ids), "evidence": result.evidence_count}


class ObservedController:
    """Wraps the scripted or live controller of the terminal's service.

    Emits progress events, applies the session seed override to the run's dispatcher (recorded in
    the trace as ``seed_override``), and retains the dispatcher of the latest run."""

    def __init__(self, inner, events):
        self.inner = inner
        self.events = events
        self.mode = inner.mode
        self.model_identifier = inner.model_identifier
        self.seed_override = None
        self.begin()
        invoke = getattr(inner, "invoke", None)
        if invoke is not None:
            inner.invoke = self._observe_invoke(invoke)

    def begin(self):
        """Reset per-request state before the service runs a request."""
        self.dispatcher = None
        self.turns = 0
        self.reported_gates = 0

    def _observe_invoke(self, invoke):
        def observed(messages, tools, remaining):
            self.turns += 1
            turn = self.turns
            self.events.emit("model", f"turn {turn} start (messages={len(messages)})",
                             event="model_start", turn=turn, messages=len(messages))
            started = time.monotonic()
            try:
                output = invoke(messages, tools, remaining)
            except RecommendationError as e:
                self.events.emit("model", f"turn {turn} error {e.detail.code}", event="model_error", turn=turn,
                                 code=e.detail.code, duration_ms=(time.monotonic() - started) * 1000)
                raise
            ms = (time.monotonic() - started) * 1000
            calls = output.get("tool_calls") if isinstance(output, dict) else None
            count = len(calls) if isinstance(calls, list) else 0
            self.events.emit("model", f"turn {turn} end {ms:.1f} ms (tool_calls={count}"
                             f"{', final answer' if not count else ''})",
                             event="model_end", turn=turn, tool_calls=count, duration_ms=ms)
            return output
        return observed

    def _observe_calls(self, dispatcher):
        call = dispatcher.call

        def observed(name, arguments):
            self.events.emit("tool", f"{name} start", event="tool_start", tool=name)
            started = time.monotonic()
            try:
                result = call(name, arguments)
            except RecommendationError as e:
                self.events.emit("tool", f"{name} aborted {e.detail.code}", event="tool_end", tool=name,
                                 status="aborted", code=e.detail.code)
                raise
            ms = (time.monotonic() - started) * 1000
            counts = _counts(result)
            status = f"error {result.error.code}" if result.error else "ok"
            self.events.emit("tool", f"{name} end {status} {ms:.1f} ms "
                             f"({', '.join(f'{k}={v}' for k, v in counts.items())})",
                             event="tool_end", tool=name, status="error" if result.error else "ok",
                             code=result.error.code if result.error else None, duration_ms=ms, **counts)
            return result

        dispatcher.call = observed

    def report_gates(self, stages):
        gates = [s for s in stages or [] if s.get("stage") == "final_gate" and s.get("status") != "running"]
        for g in gates[self.reported_gates:]:
            outputs = g.get("outputs") or {}
            detail = (f"error {g['error']['code']}: {g['error']['message']}" if g.get("error")
                      else f"ok (selected={outputs.get('selected')}, rows_read={outputs.get('rows_read')})")
            ms = g.get("duration_ms")
            self.events.emit("gate", f"final_gate {detail}" + (f" {ms:.1f} ms" if ms is not None else ""),
                             event="gate_end", status=g.get("status"), duration_ms=ms)
        self.reported_gates = len(gates)

    def propose(self, dispatcher, context, trace):
        if self.dispatcher is not dispatcher:
            self.dispatcher = dispatcher
            dispatcher.seed_override = self.seed_override
            if self.seed_override is not None:
                trace["seed_override"] = {
                    "value": self.seed_override,
                    "source": "terminal set-seed (test override of the R3 base seed; never used by the service API)",
                }
            self._observe_calls(dispatcher)
        self.report_gates(trace.get("stages"))  # a failed gate before a repair attempt
        proposal = self.inner.propose(dispatcher, context, trace)
        self.events.emit("gate", f"checking proposal of {len(proposal.listing_ids)} listing(s)",
                         event="gate_start", proposed=len(proposal.listing_ids))
        return proposal


# --- step mode ----------------------------------------------------------------------------------
class _Rollback(Exception):
    pass


@contextmanager
def rolled_back(store):
    """Run inside a state transaction that is always rolled back (pool cache writes included)."""
    try:
        with store.transaction():
            yield
            raise _Rollback
    except _Rollback:
        pass


def stage_name(text):
    name = STAGE_ALIASES.get((text or "").strip())
    if name is None:
        raise ValueError(f"Unknown stage {text!r}; use one of: {', '.join(STEP_STAGES)}")
    return name


def effective_request(request, config):
    limit = request.result_limit or config.recommendation.default_result_limit
    if limit > config.recommendation.max_result_limit:
        raise RecommendationError("INVALID_REQUEST", f"result_limit {limit} exceeds recommendation.max_result_limit "
                                  f"{config.recommendation.max_result_limit}")
    return request.model_copy(update={"result_limit": limit})


def _dumps(criteria, exclude=()):
    return sorted(json.dumps(c.model_dump(mode="json", exclude=set(exclude)), sort_keys=True) for c in criteria)


def invalidation(old, new, old_config, new_config, old_seed, new_seed):
    """First STEP_STAGES index whose result no longer matches the inputs (None: unchanged).

    0 (resolve_entities): listing type, the set of geographic criteria or any field that resolution
    reads (target, distance type, transport mode; not value / operator / unit / weight / utility /
    strength) or the geography / entity_resolution settings changed. 1 (build_pool):
    any hard criterion or the cache settings changed (a hard-criteria change invalidates the
    pool). 2 (get_geographic_evidence): a soft geographic criterion changed. 3 (rank_candidates):
    anything else that reaches the run (request_id -> seeds, feedback, soft criteria, weights,
    result_limit, cold_start, other settings, the seed override)."""
    from ..tools.pool_builder import GEO_FIELDS

    levels = []
    a, b = old.requirements, new.requirements
    geo = lambda r: [c for c in r.criteria if c.field in GEO_FIELDS]  # noqa: E731
    target = ("weight", "utility", "strength", "value", "operator", "unit")  # not used by resolution
    if a.listing_type != b.listing_type or _dumps(geo(a), target) != _dumps(geo(b), target):
        levels.append(0)
    hard = lambda r: [c for c in r.criteria if c.strength == "hard"]  # noqa: E731
    if _dumps(hard(a)) != _dumps(hard(b)):
        levels.append(1)
    soft_geo = lambda r: [c for c in geo(r) if c.strength == "soft"]  # noqa: E731
    if _dumps(soft_geo(a), ("weight",)) != _dumps(soft_geo(b), ("weight",)):
        levels.append(2)
    ignore = {"requirements_version", "previous_state_id", "conversation_id"}
    if old.model_dump(mode="json", exclude=ignore) != new.model_dump(mode="json", exclude=ignore):
        levels.append(3)
    if old_seed != new_seed:
        levels.append(3)
    old_data, new_data = old_config.to_data(), new_config.to_data()
    for section in old_data:
        if old_data[section] != new_data.get(section):
            levels.append(CONFIG_INVALIDATES.get(section, 3))
    return min(levels) if levels else None


class StepSession:
    """Scripted step mode over the staged request (see module docstring)."""

    def __init__(self, terminal, request, config, seed, validate=True, label="step session"):
        from ..context.manager import prepare
        from ..tools.dispatcher import Dispatcher
        from ..state.pool_cache import PoolCache
        from ..state.store import TRACE_FORMAT
        from ..tools.pool_builder import parse_rows

        service = terminal.ensure_service()
        self.store = service.store
        self.label = label
        self.request_id = request.request_id
        effective = effective_request(request, config)
        ctx = (prepare(request, self.store.current(request.conversation_id), self.store, config) if validate
               else {"profile": {}, "changes": {}, "sequence": 0})
        self.trace = {"format": TRACE_FORMAT, "trace_id": f"step-{uuid.uuid4()}", "layout": slim.LAYOUT,
                      "trace_mode": config.trace.mode, "step_session": True, "stages": [], "steps": [],
                      **config.identity()}
        self.dispatcher = Dispatcher(effective, ctx, service.listing, service.geographic, self.trace, None, config,
                                     pools=PoolCache(self.store, config, parse=parse_rows))
        self.dispatcher.seed_override = seed
        self.validate = validate
        self.gate_passed = False

    def completed(self):
        done = list(self.dispatcher.completed_stages())
        return done + (["final_gate"] if self.gate_passed and len(done) == len(PIPELINE) else [])

    def next_stage(self):
        return next((s for s in STEP_STAGES if s not in self.completed()), None)

    def update(self, request, config, seed):
        """Apply new staged input; returns (reason level or None, invalidated stage names)."""
        from ..context.manager import prepare

        d = self.dispatcher
        new = effective_request(request, config)
        level = invalidation(d.request, new, d.config, config, d.seed_override, seed)
        if level is None:
            return None, []
        if self.validate:
            prepare(request, self.store.current(request.conversation_id), self.store, config)
        before = self.completed()
        d.request, d.config, d.seed_override = new, config, seed
        if d.pools is not None:
            from ..state.pool_cache import PoolCache
            from ..tools.pool_builder import parse_rows

            d.pools = PoolCache(self.store, config, parse=parse_rows)
        self.request_id = request.request_id
        if level <= 0:
            d.resolutions, d.resolved = None, {}
            d.tools.reset_catalog()
            d.discard_stages()
        elif level == 1:
            d.discard_stages()
        elif level == 2:
            d.geo_records, d.geo_done = [], False
            d.evidence = d.pool_result.hard_evidence(new, d.resolved) if d.pool_result else []
            d.reset_results()
        else:
            d.reset_results()
        self.gate_passed = False
        after = self.completed()
        return level, [s for s in before if s not in after]

    def step(self, stage=None):
        """Run ``stage`` (default: the next one); returns (stage, new stage records)."""
        from ..agent.controller import Proposal
        from ..validation.gate import final_gate

        d = self.dispatcher
        stage = stage or self.next_stage()
        if stage is None:
            return None, []
        d.started = time.monotonic()  # step mode: the controller time budget applies per step
        first = len(self.trace["stages"])
        if stage == "final_gate":
            if d.selected is None:
                raise RecommendationError(
                    "STALE_REFERENCE",
                    f"Call rank_candidates for the current inputs first (pipeline order: {' -> '.join(STEP_STAGES)})")
            gate = slim.Running(self.trace, "final_gate", d.config, inputs={"proposed": len(d.selected)},
                                params={"mode": "step dry run (rolled back)"})
            try:
                with rolled_back(self.store):
                    final_gate(Proposal(listing_ids=list(d.selected)), d, self.trace)
            except RecommendationError as e:
                gate.failed(e.detail)
                raise
            gate.done(pool=d.pool_result, outputs={"selected": len(d.selected),
                                                   "rows_read": self.trace["steps"][-1].get("rows_read")})
            self.gate_passed = True
        else:
            pool_before = d.pool_result
            with rolled_back(self.store):
                result = d.call(stage, {"context_ref": d.ref})
            self.gate_passed = False
            pool = d.pool_result
            if pool is not None and pool is not pool_before and pool.persisted and pool.outcome != "hit":
                # The pool content written by this step was rolled back (a hit was stored before).
                pool.persisted = False
                for record in self.trace["stages"][first:]:
                    if record["stage"] == "pool_build_or_load":
                        record["outputs"]["persisted"] = False
                        record["note"] = "step mode: the pool_cache write was rolled back (not persisted)"
            if result.error:
                raise RecommendationError(result.error.code, result.error.message, result.error.retryable)
        return stage, self.trace["stages"][first:]

    def run_until(self, target):
        ran = []
        if target in self.completed():
            return ran
        while target not in self.completed():
            stage, records = self.step()
            if stage is None:
                break
            ran.append((stage, records))
        return ran


# --- helpers ------------------------------------------------------------------------------------
def _print(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _options(arg, *flags):
    """Split ``arg`` into positional tokens and {--flag: value|True}."""
    tokens, options = arg.split(), {}
    positional = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in flags:
            if token in ("--debug", "--latest"):  # boolean flags
                options[token] = True
            else:
                if i + 1 >= len(tokens):
                    raise ValueError(f"{token} needs a value")
                options[token] = tokens[i + 1]
                i += 1
        elif token.startswith("--"):
            raise ValueError(f"Unknown option {token}")
        else:
            positional.append(token)
        i += 1
    return positional, options


def _positive(value, name):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a positive integer") from None
    if number < 1:
        raise ValueError(f"{name} must be a positive integer")
    return number


def view(t):
    """(label, dispatcher or None, response or None) for the inspection commands."""
    if t.step is not None and t.view_source == "step":
        d = t.step.dispatcher
        return (f"{INTERMEDIATE}; request {t.step.request_id}; completed: "
                f"{', '.join(t.step.completed()) or 'none'}", d, None)
    if t.run_dispatcher is not None and t.response is not None:
        return f"run {t.response.request_id} (data retained in memory from this run)", t.run_dispatcher, t.response
    if t.response is not None:
        return (f"run {t.response.request_id} (committed response; this terminal did not execute it, so "
                "per-listing data is read from the stored response/trace or labelled 'recomputed')"), None, t.response
    raise ValueError("No run yet: recommend (or step) first")


def _row(row):
    return {k: getattr(row, k) for k in ("listing_id", "price", "bedrooms", "floor_area_sqft", "property_type",
                                          "district_code", "latitude", "longitude")}


def _store_trace(t, request_id=None):
    """Newest stored trace of the conversation (or of ``request_id``), e.g. a replay's trace."""
    return t.store.trace(t.conversation, request_id)


def is_replay_trace(trace):
    return any(s.get("kind") == "final_gate" and s.get("mode") == "immutable_replay" for s in trace.get("steps", []))


def _run_trace(t, request_id):
    """The trace of the run that produced ``request_id``'s committed response, found by
    ``response.execution.trace_id`` (a later replay of the same request saves a newer trace that
    holds only the replay gate). Requests without a committed v2 run (failed requests, legacy v1
    runs) fall back to the newest trace of that request."""
    cached = t.store.cached(t.conversation, request_id)
    trace_id = ((cached[1].get("execution") or {}).get("trace_id")) if cached else None
    if trace_id:
        try:
            return t.store.resolve_reference(t.conversation, trace_id)
        except RecommendationError:
            pass
    return _store_trace(t, request_id)


# --- settings commands --------------------------------------------------------------------------
def show_config(t, arg):
    key = arg.strip()
    if key:
        try:
            value = lookup(t.config, key)
        except AttributeError:
            raise ValueError(f"Unknown settings key {key!r}") from None
        _print({key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value})
        return
    _print({"source": t.config.source, "config_version": t.config.config_version,
            "config_hash": t.config.config_hash, "selection_hash": t.config.selection_hash,
            "session_overrides": t.overrides, "settings": t.config.to_data()})


def set_config(t, arg):
    key, _, raw = arg.strip().partition(" ")
    if not key or not raw.strip():
        raise ValueError("Usage: set-config <dotted.key> <json-value>")
    try:
        value = json.loads(raw)
    except ValueError:
        raise ValueError("set-config value must be JSON (strings in double quotes)") from None
    base = t.base_config.source or "<settings>"
    overrides = {**t.overrides, key: value}
    new = t.base_config.with_overrides(overrides, source=f"{base} + session overrides")  # validated
    old = t.config
    t.overrides = overrides
    t.apply_config(new)
    print(f"Session override {key} = {json.dumps(value)} (validated; the settings file is not written; "
          f"applies to new requests only). config_hash {old.config_hash[:12]} -> {new.config_hash[:12]}, "
          f"selection_hash {old.selection_hash[:12]} -> {new.selection_hash[:12]}")


def reset_config(t, arg):
    t.overrides = {}
    t.apply_config(t.base_config)
    print(f"Session overrides cleared; settings {t.config.source} (config_hash {t.config.config_hash[:12]}).")


def export_config(t, arg):
    data = t.config.to_data()
    if not arg.strip():
        _print(data)
        return
    path = Path(arg.strip())
    suffix = path.suffix.lower()
    header = (f"# Effective recommendation settings exported by the terminal (config_hash {t.config.config_hash}).\n"
              f"# Base: {t.base_config.source}; session overrides: {json.dumps(t.overrides, sort_keys=True)}.\n"
              "# Experiment file of the settings schema (load with --config); not the production source.\n")
    if suffix in (".yaml", ".yml"):
        import yaml

        path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    elif suffix == ".json":
        path.write_text(json.dumps(data, indent=2) + "\n")
    else:
        raise ValueError("export-config writes .yaml, .yml or .json")
    print(f"Exported effective settings to {path} (config_hash {t.config.config_hash[:12]}).")


def set_seed(t, arg):
    value = arg.strip()
    if not value:
        raise ValueError("Usage: set-seed <value> | set-seed off")
    if value in ("off", "none", "clear"):
        t.seed = None
        print("Seed override cleared: selection seeds derive from the request again (R3).")
    else:
        if len(value) > 128:
            raise ValueError("Seed override is limited to 128 characters")
        t.seed = value
        print(f"Seed override {value!r} for new requests (replaces the R3 base seed; recorded in the trace; "
              "terminal test override only, never used by the service API).")
    t.input_changed()


# --- input commands -----------------------------------------------------------------------------
def show_input(t, arg):
    pending = t.pending_request
    request = pending or t.request()
    body = request.model_dump(mode="json")
    if pending is None:
        body["request_id"] = "<new request_id minted by recommend>"
    print("Pending retry of a failed request (same request_id)." if pending is not None
          else "Staged input (not submitted):")
    _print(body)
    print(f"seed override: {t.seed!r}; settings: {t.config.source} (config_hash {t.config.config_hash[:12]}); "
          f"session overrides: {json.dumps(t.overrides, sort_keys=True)}")


def validate_input(t, arg):
    from ..context.manager import check_state, check_feedback

    request = t.pending_request or t.request()
    RecommendationRequest.model_validate(request.model_dump())  # contract 2.0 schema
    try:
        effective_request(request, t.config)
        check_state(request, t.store.current(t.conversation))
        check_feedback(request, t.store, t.config)
    except RecommendationError as e:
        print(f"Input invalid: {e.detail.code}: {e.detail.message}")
        return False
    print("Input valid: contract 2.0 schema and run-consistency checks (state reference, requirements version, "
          "feedback snapshot, result_limit) pass; nothing was submitted.")
    return True


def load_request(t, arg):
    from .__main__ import read_payload
    from ..context.manager import check_feedback
    from recommendation.contracts.requirements import Requirements

    payload = read_payload(arg)
    loaded = RecommendationRequest.model_validate(payload)  # full contract 2.0 body (1.0 rejected)
    requirements = Requirements.model_validate(loaded.requirements.model_dump())
    version = t.version + (1 if requirements != t.requirements else 0)
    staged = RecommendationRequest(
        request_id=str(uuid.uuid4()), conversation_id=t.conversation, requirements_version=max(version, 1),
        requirements=requirements, feedback=loaded.feedback, cold_start=loaded.cold_start,
        result_limit=loaded.result_limit, previous_state_id=t.current["state_id"] if t.current else None)
    check_feedback(staged, t.store, t.config)
    t.requirements, t.version = requirements, max(version, 1)
    t.feedback, t.cold_start, t.result_limit = loaded.feedback, loaded.cold_start, loaded.result_limit
    t.input_changed()
    managed = {k: getattr(loaded, k) for k in ("request_id", "conversation_id", "previous_state_id",
                                               "requirements_version")}
    print(f"Loaded request staged: requirements v{t.version}, feedback "
          f"{'with' if loaded.feedback.previous_round else 'without'} previous round, "
          f"{len(loaded.feedback.appointments)} appointment listings, cold_start {loaded.cold_start.model_dump()}, "
          f"result_limit {loaded.result_limit}. Terminal-managed (file values not reused): {json.dumps(managed)}; "
          "recommend mints a new request_id.")


# --- inspection commands ------------------------------------------------------------------------
def show_pool(t, arg):
    _, options = _options(arg, "--page")
    page = _positive(options.get("--page", 1), "--page")
    label, d, response = view(t)
    size = t.config.trace.preview_rows
    print(f"source: {label}")
    if d is not None and d.pool_result is not None:
        pool = d.pool_result
        header = {**pool.reference(), **{k: pool.partition[k] for k in (
            "size", "source_count", "excluded_hard_fail", "excluded_hard_unknown", "unknown_reasons")}}
        rows = list(d.pool)
        scored = d.scored
        if scored is not None:
            order = {k: n for n, k in enumerate(scored.ranked)}
            rows.sort(key=lambda r: order.get(r.listing_id, len(order)))
        out = []
        for r in rows[(page - 1) * size: page * size]:
            entry = _row(r)
            if scored is not None and r.listing_id in scored.index:
                entry["score_rank"] = scored.score_rank[r.listing_id]
                entry["combined_score"] = float(scored.fused.combined[scored.index[r.listing_id]])
            out.append(entry)
    elif response is not None and response.pool is not None:
        header = response.pool.model_dump(mode="json")
        content = t.store.resolve_reference(t.conversation, f"{response.execution.trace_id}/pool")
        print(f"rows: pool_cache content of pool_version {header['pool_version'][:12]} (stored)")
        rows = content.get("rows", [])
        out = [{k: r.get(k) for k in ("listing_id", "price", "bedrooms", "floor_area_sqft", "property_type",
                                       "district_code")} for r in rows[(page - 1) * size: page * size]]
    else:
        raise ValueError("No exploration pool in this view (run build_pool / recommend first)")
    pages = max(1, -(-len(rows) // size))
    _print({"pool": header, "page": page, "pages": pages, "page_size": size, "rows": out})


def show_resolution(t, arg):
    label, d, response = view(t)
    print(f"source: {label}")
    if d is not None and d.resolutions is not None:
        items = d.resolution_view()
    elif response is not None:
        items = [r.model_dump(mode="json") for r in response.resolution]
    else:
        raise ValueError("Entity resolution has not run in this view (step resolve_entities first)")
    if not items:
        print("No geographic criteria: nothing to resolve.")
        return
    for r in items:
        problem = r["status"] in ("ambiguous", "unresolved")
        print(f"{r['criterion_id']}: {r['status'].upper()} entity={r.get('entity_id')} kind={r.get('entity_kind')} "
              f"method={r.get('method')} reason={r.get('reason')}"
              + (f" -> needs clarification ({len(r.get('candidates') or [])} candidate(s))" if problem else ""))
    _print(items)


def _breakdown_row(listing_id, breakdown, score_rank, selected):
    b = breakdown.model_dump(mode="json")
    return {"listing_id": listing_id, "score_rank": score_rank,
            **{k: b[k] for k in ("combined_score", "soft_score", "behavior_score", "appointment_score",
                                 "browsing_score", "soft_evidence_coverage", "comparison_coverage")},
            "selected": selected}


def show_scores(t, arg):
    _, options = _options(arg, "--top")
    top = _positive(options.get("--top", t.config.trace.preview_rows), "--top")
    label, d, response = view(t)
    print(f"source: {label}")
    if d is not None and d.scored is not None:
        s = d.scored
        selection = d.selection
        rows = [_breakdown_row(k, s.breakdown(k), s.score_rank[k],
                               selection.reasons.get(k) if selection and k in selection.reasons else None)
                for k in s.ranked[:top]]
        _print({"scored": len(s.ids), "weights": s.weights.as_dict(s.criterion_ids, s.features), "suppressed_features": s.suppressed,
                "dropped_features": s.dropped, "active_features": s.features, "top": rows})
        return
    if d is not None:
        raise ValueError("Scores do not exist in this view yet (run rank_candidates first)")
    rows = [_breakdown_row(i.listing.listing_id, i.scores, i.score_rank, i.selection_reason)
            for i in response.recommendations]
    try:
        stage = next(s for s in reversed(_run_trace(t, response.request_id)["stages"]) if s["stage"] == "scoring")
        preview = stage["preview"][:top]
    except (RecommendationError, StopIteration):
        preview = []
    print("Retained full-pool scores are unavailable: returned items (full breakdown) + trace scoring preview.")
    _print({"returned_items": rows, "trace_scoring_preview": preview})


def show_cold_start(t, arg):
    label, d, response = view(t)
    print(f"source: {label}")
    if d is not None:
        if d.selection is None:
            raise ValueError("Cold-start diagnostics do not exist in this view yet (run rank_candidates first)")
        cold = d.selection.summary.cold_start.model_dump(mode="json")
        record = {k: v for k, v in d.selection.record["cold_start"].items() if k not in cold}
    elif response.selection is not None:
        cold, record = response.selection.cold_start.model_dump(mode="json"), {}
    else:
        raise ValueError("This response has no selection summary")
    _print({"cold_start": cold, "details": record})
    print("Cold start is active when enabled, the pool exceeds result_limit and the exit conditions "
          "(C >= c, B <= b, S >= s) are not met; without soft criteria C = 0, so cold start cannot be exited.")


def show_selection(t, arg):
    label, d, response = view(t)
    print(f"source: {label}")
    if d is not None:
        if d.selection is None:
            raise ValueError("No selection in this view yet (run rank_candidates first)")
        summary = d.selection.summary.model_dump(mode="json")
        items = [{"rank": n, "listing_id": k, "score_rank": d.scored.score_rank[k],
                  "selection_reason": d.selection.reasons[k], "selection_note": d.selection.notes.get(k)}
                 for n, k in enumerate(d.selected, 1)]
        override = d.selection_input["seeds"]["override"]
    else:
        summary = response.selection.model_dump(mode="json") if response.selection else None
        items = [{"rank": i.rank, "listing_id": i.listing.listing_id, "score_rank": i.score_rank,
                  "selection_reason": i.selection_reason, "selection_note": i.selection_note}
                 for i in response.recommendations]
        override = None
    if d is not None and response is None:
        print("Candidate selection of the step session: " + INTERMEDIATE)
    _print({"selection": summary, "seed_override": override, "items": items})


def _criterion_results(results):
    return [r.model_dump(mode="json") for r in results]


def _recompute_listing(t, d, listing_id, request, config):
    """Fresh read of one listing BY ID + criterion evaluation (labelled 'recomputed')."""
    from ..tools.pool_builder import GEO_FIELDS, by_listing, normalize_evidence
    from ..validation.rules import evaluate, eligible

    service = t.ensure_service()
    rows = service.listing.lookup([listing_id])
    if not rows:
        return {"listing_id": listing_id, "found": False, "note": "not found in the listing source (by-id read)"}
    row = rows[0]
    criteria = request.requirements.criteria
    geo = [c for c in criteria if c.field in GEO_FIELDS]
    resolutions = d.resolutions if d is not None and d.resolutions is not None else (
        service.geographic.resolve_criteria(geo, config) if geo else {})
    from ..geography.resolver import key_map

    resolved = key_map(resolutions)
    evidence = normalize_evidence(
        service.geographic.evidence([row], geo, resolutions=resolutions, settings=config) if geo else [],
        geo, {row.listing_id: row}, resolved)
    results = evaluate(row, criteria, by_listing(evidence).get(row.listing_id, []))
    hard = [r for r in results if any(c.criterion_id == r.criterion_id and c.strength == "hard" for c in criteria)]
    return {"listing_id": listing_id, "found": True, "row": _row(row),
            "hard_criteria_pass": eligible(results, criteria),
            "criterion_results": _criterion_results(results),
            "hard_failures": [r.criterion_id for r in hard if r.outcome != "pass"],
            "evidence": [e.model_dump(mode="json") for e in evidence]}


def stored_configuration(data, current):
    """(RecommendationConfig, filled keys) for a stored run configuration.

    A trace-only key added after the run was stored (P7: trace.max_string_chars) is absent from
    older snapshots. Trace keys never affect results (they are outside selection_hash), so the
    missing ones are taken from the current settings and named in the output; every other key
    must be present and valid exactly as stored."""
    data = json.loads(json.dumps(data))
    filled = []
    if isinstance(data.get("trace"), dict):
        for key, value in current.to_data()["trace"].items():
            if key not in data["trace"]:
                data["trace"][key] = value
                filled.append(f"trace.{key}")
    source = "stored run configuration" + (f" + current {', '.join(filled)}" if filled else "")
    return RecommendationConfig.from_data(data, source), filled


def inspect_listing(t, arg):
    listing_id = arg.strip()
    if not listing_id:
        raise ValueError("Usage: inspect-listing <listing-id>")
    label, d, response = view(t)
    recomputed = False
    if d is None:
        # This terminal did not execute the run: recompute it (read-only, rolled back).
        cached = t.store.cached(t.conversation, response.request_id)
        request = RecommendationRequest.model_validate(cached[2]["request"])
        config, filled = stored_configuration(cached[2]["configuration"], t.config)
        if filled:
            print(f"Note: the stored configuration predates {', '.join(filled)} (trace-only, never result-"
                  "affecting); the current value is used for this recompute.")
        session = StepSession(t, request, config, None, validate=False, label="recompute")
        session.run_until("rank_candidates")
        d, recomputed = session.dispatcher, True
        label = (f"RECOMPUTED from the current data for run {response.request_id} (its stored request and "
                 "configuration; not data retained from the original run)")
    print(f"source: {label}")
    pool = {r.listing_id: r for r in d.pool}
    if listing_id not in pool:
        info = _recompute_listing(t, d, listing_id, d.request, d.config)
        print(f"Listing {listing_id} is not in this run's exploration pool: recomputed (fresh by-id read and "
              "criterion evaluation; the run itself never stored data for it).")
        _print({"recomputed": True, **info})
        return
    out = {"recomputed": recomputed, "in_pool": True, "row": _row(pool[listing_id]),
           "criterion_results": _criterion_results(d.results.get(listing_id, [])) if d.results else None,
           "evidence": [e.model_dump(mode="json") for e in d.evidence if e.listing_id == listing_id]}
    if d.scored is not None and listing_id in d.scored.index:
        out["score_rank"] = d.scored.score_rank[listing_id]
        out["scores"] = d.scored.breakdown(listing_id).model_dump(mode="json")
    if d.selection is not None:
        selected = d.selected or []
        out["selected"] = listing_id in selected
        out["rank"] = selected.index(listing_id) + 1 if listing_id in selected else None
        out["selection_reason"] = d.selection.reasons.get(listing_id)
        out["selection_note"] = d.selection.notes.get(listing_id)
    _print(out)


def show_trace(t, arg):
    from ..state.store import is_legacy_trace

    positional, options = _options(arg, "--debug", "--latest")
    if positional and not options.get("--latest"):
        trace = _run_trace(t, positional[0])
        latest = _store_trace(t, positional[0])
        if latest.get("trace_id") != trace.get("trace_id"):
            print(f"Note: request {positional[0]} has a newer trace {latest.get('trace_id')}"
                  + (" (immutable replay)" if is_replay_trace(latest) else "")
                  + f"; this is the run's own trace. show-trace {positional[0]} --latest shows the newer one.")
    else:
        trace = _store_trace(t, positional[0] if positional else None)
    if is_legacy_trace(trace):
        print("Note: legacy v1 trace (contract 1.0 run, read-only).")
    if is_replay_trace(trace):
        original = next(s.get("original_trace_ref") for s in trace["steps"] if s.get("mode") == "immutable_replay")
        replayed = (trace.get("request") or {}).get("request_id")
        print(f"Note: immutable replay trace of request {replayed} (replay gate only); the run's own "
              f"trace is {original}: show-trace {replayed}.")
    debug = trace.pop("debug", None)
    if options.get("--debug"):
        if debug is None and not is_legacy_trace(trace):
            print(f"Note: this run was traced with trace.mode={trace.get('trace_mode')!r}: no debug block. "
                  "Use set-config trace.mode \"debug\" and recommend again.")
        else:
            trace["debug"] = debug
    elif debug is not None:
        trace["debug"] = f"[{debug.get('rows')} debug rows omitted: show-trace --debug]"
    _print(trace)


def export_run(t, arg):
    if t.response is None:
        raise ValueError("No committed run to export (recommend first)")
    request_id = t.response.request_id
    cached = t.store.cached(t.conversation, request_id)
    name = f"run-{request_id}" if SAFE_NAME.match(request_id) else f"run-{uuid.uuid4()}"
    directory = Path(arg.strip()) if arg.strip() else Path(t.args.state_path).resolve().parent / "exports" / name
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = cached[2]
    files = {
        "request.json": snapshot["request"],
        "response.json": cached[1],
        "trace.json": _run_trace(t, request_id),
        "config.json": {"note": "effective settings of this run (frozen at request start)",
                        "config_version": snapshot.get("config_version"), "config_hash": snapshot.get("config_hash"),
                        "selection_hash": snapshot.get("selection_hash"), "settings": snapshot.get("configuration")},
    }
    latest = _store_trace(t, request_id)
    if latest.get("trace_id") != files["trace.json"].get("trace_id"):
        files["latest-replay-trace.json"] = latest  # a later replay of this request (not the run's trace)
    for filename, content in files.items():
        (directory / filename).write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
    print(f"Exported run {request_id} to {directory}: {', '.join(files)}")


def replay(t, arg):
    from ..models.candidate import Response
    from ..schemas import validate_response

    request_id = arg.strip()
    if not request_id:
        raise ValueError("Usage: replay <request-id>")
    cached = t.store.cached(t.conversation, request_id)
    if not cached:
        raise ValueError(f"No committed run {request_id!r} in this conversation (replay re-submits a stored "
                         "request body; failed requests are retried with recommend)")
    print(f"REPLAY of {request_id} (stored response)")
    service = t.ensure_service()
    result = service.recommend(RecommendationRequest.model_validate(cached[2]["request"]))
    validate_response(result)
    identical = result.model_dump(mode="json") == Response.model_validate(cached[1]).model_dump(mode="json")
    t.print_response(result)
    print("(stored response: its pool/cache, selection and execution fields describe the original run)")
    print(f"Replay returned the stored response unchanged: {identical} (no new run; terminal state unchanged)")
    return result


def step(t, arg):
    if getattr(t.args, "controller", None) != "scripted":
        raise ValueError("step / run-until are available in scripted mode only (--controller scripted)")
    if arg.strip() == "reset":
        t.step = None
        t.view_source = "run"
        print("Step session discarded.")
        return
    session = t.step_session()
    stage = stage_name(arg) if arg.strip() else None
    ran, records = session.step(stage)
    if ran is None:
        print(f"{INTERMEDIATE}: every stage is current; use recommend to submit request {session.request_id}.")
        return
    t.view_source = "step"
    _report_step(session, [(ran, records)])


def run_until(t, arg):
    if getattr(t.args, "controller", None) != "scripted":
        raise ValueError("step / run-until are available in scripted mode only (--controller scripted)")
    if not arg.strip():
        raise ValueError(f"Usage: run-until <stage> ({', '.join(STEP_STAGES)})")
    target = stage_name(arg)
    session = t.step_session()
    t.view_source = "step"
    ran = session.run_until(target)
    if not ran:
        print(f"{INTERMEDIATE}: {target} is already current for the staged input.")
        return
    _report_step(session, ran)


def _report_step(session, ran):
    print(INTERMEDIATE)
    for stage, records in ran:
        print(f"stage {stage}: ok")
        for r in records:
            ms = r.get("duration_ms")
            print(f"  {r['stage']}: {r['status']}" + (f" {ms:.1f} ms" if ms is not None else "")
                  + f" outputs={json.dumps(r.get('outputs'), default=str)}")
    d = session.dispatcher
    if d.selected is not None:
        print(f"candidate selection (intermediate, not a recommendation): {d.selected}")
    if session.gate_passed:
        print(f"final_gate dry run passed (rolled back). Use recommend to submit request {session.request_id}.")
    nxt = session.next_stage()
    print(f"completed: {', '.join(session.completed())} | next: {nxt or 'none (all stages current)'}")


COMMANDS = {
    "show-config": show_config,
    "set-config": set_config,
    "reset-config": reset_config,
    "export-config": export_config,
    "set-seed": set_seed,
    "show-input": show_input,
    "validate-input": validate_input,
    "load-request": load_request,
    "show-pool": show_pool,
    "show-resolution": show_resolution,
    "show-scores": show_scores,
    "show-cold-start": show_cold_start,
    "show-selection": show_selection,
    "inspect-listing": inspect_listing,
    "show-trace": show_trace,
    "export": export_run,
    "replay": replay,
    "step": step,
    "run-until": run_until,
}


def handle(t, command, arg):
    """Run a debug command; returns False when ``command`` is not one of them."""
    handler = COMMANDS.get(command)
    if handler is None:
        return False
    handler(t, arg)
    return True
