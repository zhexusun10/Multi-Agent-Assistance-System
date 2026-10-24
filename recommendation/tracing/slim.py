"""Slim run trace (spec section 9, R13, R14; P6).

A v2 trace (``"format": 2``, ``"layout": "slim-v1"``) holds:

- ``stages``: one record per executed stage, in execution order. Stage names: input_validation,
  entity_resolution, pool_build_or_load, geographic_evidence, scoring, cold_start_diagnostics,
  price_sampling, final_selection and final_gate. Each record has ``status`` (ok | skipped |
  error), ``duration_ms``, ``inputs`` / ``outputs`` counts, ``params`` (key settings),
  ``versions`` (config_hash, selection_hash and, once a pool exists, pool_key / pool_version /
  data_version), a ``preview`` of at most ``trace.preview_rows`` rows, ``unknown`` summaries and
  ``error`` ({code, message}) when it failed. Pipeline stages carry ``call`` = the tool step's
  ``snapshot_ref``. A stage that runs again (live controller, bind_entity) gets a new record.
- ``steps``: model / tool / final_gate events. A tool step stores its arguments and a compact
  result (counts, at most ``trace.preview_rows`` IDs and previews, tool data with every list cut
  to ``trace.preview_rows`` entries and the original lengths in ``data_truncated``). It never
  stores the dispatcher state, the pool rows, the full-pool evidence / validation or per-listing
  provenance such as the nearest exit of every pool listing. ``snapshot_ref`` resolves to the
  step record itself. A model step stores only the messages added since the previous model step
  (``messages_added``, ``message_offset``, ``message_count``); the tool schemas are stored once
  (``tool_schemas``) and ``resolve_reference(<context_ref>)`` reconstructs the full message list
  the model received. Messages appended after the last model step are kept in ``messages_tail``
  (``<trace_id>/messages`` returns the whole transcript).
- ``error`` ({stage, code, message}) when the run failed: the stage that raised, or the stage
  whose tool error ended the run, or ``model`` / ``controller``.
- ``debug`` only when ``trace.mode`` is ``debug``: at most ``trace.debug_max_rows`` rows in total
  for the latest rank stage, shared by three kinds of row (``debug_rows``): the top of the score
  order with every contribution, the tie group members and the exploration candidates with
  their price bin and stage A/B membership; the drawn listings come first, then
  ``trace.preview_rows`` top-score rows, then the other tie / exploration rows.

Pool content is never copied into the trace: ``pool_ref`` names its pool_version and
``<trace_id>/pool`` resolves it from pool_cache while it is retained (STALE_REFERENCE "pool
evicted" afterwards). Size: every stage record and tool step is bounded by the settings
(preview_rows, criteria count, model_result_candidates for the model-facing messages), never by
the pool size, so the trace grows with the number of steps only.

Credentials: the persisted trace passes ``tracing.trace.redact`` (secret-named keys) and
``scrub`` below (more secret-like key names, and string values that carry a URL password or a
scheme-less ``user:pass@host:port``). JSON-encoded strings (model-facing message content) are
scrubbed value by value and kept byte for byte when they hold no credential, so listing text
such as 'Suites@Changi' never redacts a whole message.

String length: one string in compacted tool data / arguments keeps at most
``trace.max_string_chars`` characters (the former code constant TEXT_LIMIT, moved to
recommendation.yaml in P7); the original length is recorded in ``data_truncated``/``_truncated``.
"""

import re
import time
from urllib.parse import urlsplit

from .trace import digest, redact

LAYOUT = "slim-v1"
STAGES = (
    "input_validation",
    "entity_resolution",
    "pool_build_or_load",
    "geographic_evidence",
    "scoring",
    "cold_start_diagnostics",
    "price_sampling",
    "final_selection",
    "final_gate",
    "response_assembly",  # response, run snapshot and commit (locates errors after the gate)
)
# Pipeline tool -> the stages it runs (rank_candidates runs four, in this order).
TOOL_STAGES = {
    "resolve_entities": ("entity_resolution",),
    "build_pool": ("pool_build_or_load",),
    "get_geographic_evidence": ("geographic_evidence",),
    "rank_candidates": ("scoring", "cold_start_diagnostics", "price_sampling", "final_selection"),
}
LEGACY_NOTE = (
    "legacy v1 trace (contract 1.0 run, read-only): recorded before contract 2.0 and the slim "
    "trace; shapes differ from v2 traces"
)
REDACTED = "[REDACTED]"
# Key names treated as credentials (in addition to tracing.trace.redact). Matched on the
# lower-cased key with separators removed, so "total_tokens" / "tokens" are NOT matched.
SECRET_KEY = re.compile(
    r"(password|passwd|pwd|secret|apikey|accesskey|privatekey|authorization|credential|dsn|"
    r"accesstoken|refreshtoken|bearer|servicetoken|sessiontoken|authtoken)"
)


def _now():
    return time.perf_counter()


def _ms(started):
    return (_now() - started) * 1000


# --- versions and stage records ----------------------------------------------------------------
def versions(config, pool=None):
    """config_hash / selection_hash (+ pool identity once a pool exists)."""
    out = {"config_version": config.config_version, "config_hash": config.config_hash,
           "selection_hash": config.selection_hash}
    if pool is not None:
        out.update(pool_key=pool.key, pool_version=pool.version, data_version=pool.data_version)
    return out


def error_of(detail):
    return {"code": detail.code, "message": detail.message, "retryable": detail.retryable}


def record(trace, stage, status, duration_ms, config, *, pool=None, inputs=None, outputs=None,
           params=None, preview=None, unknown=None, error=None, call=None, note=None, preview_total=None):
    """Append one stage record to ``trace["stages"]`` and return it.

    ``preview`` is cut to trace.preview_rows rows; ``preview_total`` is the number of rows the
    preview was taken from (default: the length of ``preview`` as passed, so a builder that
    pre-cuts its rows passes the uncut count explicitly)."""
    if stage not in STAGES:
        raise ValueError(f"unknown trace stage {stage!r}")
    rows = config.trace.preview_rows
    entry = {
        "stage": stage,
        "status": status,
        "duration_ms": duration_ms,
        "inputs": inputs or {},
        "outputs": outputs or {},
        "params": params or {},
        "versions": versions(config, pool),
        "preview": list(preview or [])[:rows],
        "preview_total": len(preview or []) if preview_total is None else preview_total,
        "unknown": unknown or {},
    }
    if call is not None:
        entry["call"] = call
    if note:
        entry["note"] = note
    if error is not None:
        entry["error"] = error
    trace.setdefault("stages", []).append(entry)
    return entry


class Running:
    """A service-side stage that is open while code runs (input_validation, final_gate).

    ``fail_running`` marks every still-open record as failed when an exception escapes, so an
    error is always located at the stage that raised."""

    def __init__(self, trace, stage, config, **fields):
        self.started = _now()
        self.config = config
        self.entry = record(trace, stage, "running", None, config, **fields)
        trace.setdefault("_open", []).append(self)  # run memory only; finalize() drops it

    def done(self, status="ok", pool=None, **fields):
        if pool is not None:
            self.entry["versions"] = versions(self.config, pool)
        self.entry.update({k: v for k, v in fields.items() if v is not None})
        self.entry["status"] = status
        self.entry["duration_ms"] = _ms(self.started)
        return self.entry

    def failed(self, detail):
        return self.done("error", error=error_of(detail))


def fail_running(trace, detail):
    for stage in trace.get("_open", []):
        if stage.entry["status"] == "running":
            stage.failed(detail)


def locate_error(trace, detail):
    """Stage (or event kind) that caused the run error ``detail``."""
    for entry in reversed(trace.get("stages", [])):
        if entry["status"] == "error" and entry.get("error", {}).get("code") == detail.code:
            return entry["stage"]
    for step in reversed(trace.get("steps", [])):
        if step.get("kind") == "model" and (step.get("error") or {}).get("code") == detail.code:
            return "model"
    if detail.code == "INVALID_PROPOSAL" or detail.code == "STALE_EVIDENCE":
        return "final_gate"
    return "controller"


# --- compaction --------------------------------------------------------------------------------
def compact(value, rows, chars, path="", truncated=None):
    """``value`` with every list cut to ``rows`` entries and every string to ``chars``
    characters (original lengths recorded by path). Returns (compacted, truncated)."""
    truncated = {} if truncated is None else truncated
    if isinstance(value, dict):
        return {k: compact(v, rows, chars, f"{path}.{k}" if path else str(k), truncated)[0]
                for k, v in value.items()}, truncated
    if isinstance(value, (list, tuple)):
        if len(value) > rows:
            truncated[path or "."] = len(value)
        return [compact(v, rows, chars, f"{path}[{i}]", truncated)[0]
                for i, v in enumerate(value[:rows])], truncated
    if isinstance(value, str) and len(value) > chars:
        truncated[path or "."] = len(value)
        return value[:chars], truncated
    return value, truncated


def compact_result(result, config, pipeline):
    """Trace form of a ToolResult: counts, at most preview_rows IDs/previews, compacted data.

    Pipeline tool data is already compact by construction (per criterion / per stage, never per
    pool listing); query tool data (per requested listing) is cut to preview_rows entries."""
    rows = config.trace.preview_rows
    full = result.model_dump(mode="json")
    out = {k: full[k] for k in ("call_id", "tool", "status", "input_version", "output_version",
                                "result_ref", "candidate_count", "selected_ids", "completed_stages",
                                "previews_truncated", "verified_count", "evidence_count", "warnings",
                                "error")}
    out["candidate_ids"] = full["candidate_ids"][:rows]
    out["candidate_ids_truncated"] = full["candidate_count"] > len(out["candidate_ids"])
    out["ranked_ids"] = full["ranked_ids"][:rows]
    out["ranked_ids_truncated"] = full["verified_count"] > len(out["ranked_ids"])
    out["previews"] = full["previews"][:rows]
    data = full["data"]
    if data is not None and not pipeline:
        data, truncated = compact(data, rows, config.trace.max_string_chars)
        if truncated:
            out["data_truncated"] = truncated
    out["data"] = data
    return out


def compact_arguments(arguments, config):
    if not isinstance(arguments, dict):
        return {"type": type(arguments).__name__}
    value, truncated = compact(arguments, config.trace.preview_rows, config.trace.max_string_chars)
    if truncated:
        value = {**value, "_truncated": truncated}
    return value


def tool_step(ref, name, arguments, result, duration_ms, config, pipeline, stages=()):
    return {
        "kind": "tool",
        "tool": name,
        "snapshot_ref": ref,
        "stages": list(stages),
        "arguments": compact_arguments(arguments, config),
        "result": compact_result(result, config, pipeline),
        "duration_ms": duration_ms,
    }


# --- model steps -------------------------------------------------------------------------------
def model_step(trace, ref, messages, tools):
    """Model step with only the messages added since the previous model step of this run."""
    import json

    offset = trace.get("model_messages_recorded", 0)
    trace["model_messages_recorded"] = len(messages)
    schemas = digest(tools)
    if trace.get("tool_schemas_digest") != schemas:
        trace["tool_schemas"] = tools
        trace["tool_schemas_digest"] = schemas
    return {
        "kind": "model",
        "context_ref": ref,
        "message_offset": offset,
        "message_count": len(messages),
        "messages_added": json.loads(json.dumps(messages[offset:])),
        "tool_schemas_digest": schemas,
    }


def reconstruct(trace, context_ref=None):
    """Full message list up to model step ``context_ref`` (None: whole transcript incl. tail)."""
    messages = []
    for step in trace.get("steps", []):
        if step.get("kind") != "model":
            continue
        if step["message_offset"] != len(messages):
            raise ValueError("model steps do not form a contiguous transcript")
        messages.extend(step["messages_added"])
        if step["context_ref"] == context_ref:
            return {"messages": messages, "tool_schemas": trace.get("tool_schemas")}
    if context_ref is not None:
        return None
    return {"messages": messages + list(trace.get("messages_tail", [])),
            "tool_schemas": trace.get("tool_schemas")}


# --- debug rows --------------------------------------------------------------------------------
def _share(budget, *lists):
    """Split ``budget`` fairly over ``lists`` (round robin; a list's unused share goes to the
    others). Returns the number of entries taken from each list."""
    taken = [0] * len(lists)
    while budget > 0:
        progressed = False
        for n, items in enumerate(lists):
            if budget > 0 and taken[n] < len(items):
                taken[n] += 1
                budget -= 1
                progressed = True
        if not progressed:
            break
    return taken


def debug_rows(scored, selection, config, ref):
    """At most trace.debug_max_rows rows in total for the latest rank stage (trace.mode = debug).

    Three kinds of row share the budget: ``top_scores`` (the head of the score order with every
    contribution), ``tie_group`` members and, in cold start, ``exploration`` candidates (price
    bin, stage A / drawn flags). Priority: (1) the drawn listings (exploration candidates in
    cold start, else tie members), (2) the first ``trace.preview_rows`` top-score rows, (3) the
    remaining tie members and exploration candidates, shared round robin (drawn ones first),
    (4) whatever is left extends the top-score rows. Every list keeps score order and reports
    ``total`` and ``included``, so a truncated list is visible."""
    from ..ranking import exploration

    budget = config.trace.debug_max_rows
    si = selection.selection_input
    head = si["head"]
    outcome = selection.outcome
    leaders = outcome.leaders if outcome is not None else 0
    chosen = set(outcome.ids) if outcome is not None else set(selection.ids)
    drawn = set(outcome.ids[leaders:]) if outcome is not None else set()
    tie = []
    if outcome is not None and outcome.tie is not None:
        start, end = outcome.tie
        tie = [{"listing_id": h[0], "score": h[1], "score_rank": start + i + 1, "drawn": h[0] in drawn}
               for i, h in enumerate(head[start:end])]
    explored, edges = None, None
    if outcome is not None and outcome.mode == "cold_start_exploration":
        edges = si["bin_edges"]
        in_a = set(outcome.sample)
        explored = [{"listing_id": h[0], "score": h[1], "score_rank": leaders + i + 1, "price": h[2],
                     "bin": exploration.bin_index(h[2], edges), "stage_a": h[0] in in_a, "drawn": h[0] in drawn}
                    for i, h in enumerate(head[leaders:])]
    ranked = scored.ranked
    # Candidate order of each list: drawn entries first, then score order.
    queues = {"tie": sorted(range(len(tie)), key=lambda i: (not tie[i]["drawn"], i)),
              "exploration": sorted(range(len(explored or [])), key=lambda i: (not explored[i]["drawn"], i))}
    take = {"tie": 0, "exploration": 0}
    primary = "exploration" if explored is not None else "tie"
    entries = explored if explored is not None else tie
    first = min(budget, sum(1 for e in entries if e["drawn"]))
    take[primary] = first
    budget -= first
    top_n = min(budget, config.trace.preview_rows, len(ranked))
    budget -= top_n
    more = _share(budget, queues["tie"][take["tie"]:], queues["exploration"][take["exploration"]:])
    take["tie"] += more[0]
    take["exploration"] += more[1]
    budget -= sum(more)
    extra = min(budget, len(ranked) - top_n)
    top_n += extra
    top = []
    for listing_id in ranked[:top_n]:
        breakdown = scored.breakdown(listing_id)
        top.append({"listing_id": listing_id, "score_rank": scored.score_rank[listing_id],
                    "selected": listing_id in chosen, **breakdown.model_dump(mode="json")})
    out = {"rank_call": ref, "max_rows": config.trace.debug_max_rows,
           "budget": "drawn listings, then trace.preview_rows top scores, then tie group and exploration "
                     "candidates shared round robin, then more top scores"}
    members = [tie[i] for i in sorted(queues["tie"][:take["tie"]])]
    out["tie_group"] = {"total": len(tie), "included": len(members), "members": members}
    if explored is not None:
        candidates = [explored[i] for i in sorted(queues["exploration"][:take["exploration"]])]
        out["exploration"] = {"bin_edges": edges, "total": len(explored), "included": len(candidates),
                              "candidates": candidates}
    else:
        out["exploration"] = None
    out["top_scores"] = {"total": len(ranked), "included": len(top), "rows": top}
    out["rows"] = len(members) + (len(out["exploration"]["candidates"]) if explored is not None else 0) + len(top)
    return out


# --- persistence and references ----------------------------------------------------------------
def _secret_key(key):
    return bool(SECRET_KEY.search(re.sub(r"[^a-z]", "", str(key).lower())))


# A URL token ends at whitespace, a quote, a backslash or an angle bracket, so a URL inside a
# JSON-encoded string never absorbs the JSON that follows it.
URL_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\"'<>\\]+")
# Scheme-less ``user:pass@host:port`` (e.g. a driver error message). Its parts exclude quotes and
# JSON / list delimiters, so compact JSON such as '"title":"Suites@Changi","price":4100' is
# never read as one.
_PART = r"[^\s:/@\"'<>\\,;{}\[\]()]+"
USERINFO = re.compile(rf"(?<![^\s(=\"'])({_PART}):{_PART}@{_PART}:\d+\b")


def _secret_url(text):
    if "://" not in text and "@" not in text:
        return False
    for token in URL_TOKEN.findall(text):
        try:
            if urlsplit(token).password:
                return True
        except ValueError:
            return True
    return bool(USERINFO.search(text))


def _scrub_text(text):
    """A string, or REDACTED when it carries a credential. A JSON-encoded string (the content of
    model-facing messages, e.g. ``ToolResult.model_dump_json()``) is scrubbed structurally: it is
    kept byte for byte unless one of its values is a credential, and then only that value is
    redacted (re-serialised), so ``resolve_reference`` still reconstructs what the model received
    whenever the content holds no credential."""
    if "://" not in text and "@" not in text:
        return text
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        import json

        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            cleaned = scrub(parsed)
            return text if cleaned == parsed else json.dumps(cleaned, ensure_ascii=False)
    return REDACTED if _secret_url(text) else text


def scrub(value):
    if isinstance(value, dict):
        return {k: (REDACTED if _secret_key(k) and v not in (None, "", [], {}) else scrub(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def finalize(trace):
    """Persisted form: drops controller scratch state, keeps the post-model message tail, redacts."""
    out = dict(trace)
    messages = out.pop("controller_messages", None)
    recorded = out.pop("model_messages_recorded", 0)
    out.pop("started_monotonic", None)
    out.pop("_open", None)
    out.pop("tool_schemas_digest", None)
    if messages and len(messages) > recorded:
        import json

        out["messages_tail"] = json.loads(json.dumps(messages[recorded:]))
    return scrub(redact(out))


def resolve_step(trace, reference):
    """Immutable step content for ``reference`` within one stored trace, or None."""
    legacy = trace.get("format") is None
    if reference == f"{trace.get('trace_id')}/messages" and not legacy:
        if trace.get("layout") == LAYOUT:
            return reconstruct(trace)
        # P2-P5 v2 traces: every model step stored its full message list; the last one is the
        # whole transcript the model saw (those traces kept no post-model tail).
        full = [step for step in trace.get("steps", []) if step.get("kind") == "model" and "messages" in step]
        if not full:
            return {"messages": [], "tool_schemas": None}
        return {"messages": full[-1]["messages"], "tool_schemas": full[-1].get("tool_schemas")}
    for step in trace.get("steps", []):
        if step.get("snapshot_ref") == reference:
            # P2-P5 v2 traces and legacy traces stored the dispatcher snapshot in the step.
            return step["snapshot"] if "snapshot" in step else step
        if step.get("context_ref") == reference:
            if "messages" in step:
                return {"messages": step["messages"], "tool_schemas": step["tool_schemas"]}
            return reconstruct(trace, reference)
    return None
