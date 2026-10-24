import json, time
from typing import Any
from pydantic import ValidationError
from pydantic import Field
from recommendation.contracts.requirements import Contract
from recommendation.contracts.errors import RecommendationError
from ..tools.registry import PIPELINE, model_tools
from .prompt import AGENT_PROTOCOL, repair_instruction, system_prompt
from ..tracing import slim
from . import llm


class ModelToolCall(Contract):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    args: dict[str, Any]
    type: str | None = None


def visible_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
    return ""


class Completion(Contract):
    """The agent's finish: the code-managed ranking artifact it built for the current inputs.

    The model never submits listing IDs; code verifies the artifact is the current one, then runs
    cold-start detection, selection and final validation (service.py)."""

    ranking_artifact_id: str = Field(min_length=1)


def parse_completion(content):
    """Completion from exactly ONE top-level JSON object in the visible text (prose or code fences
    around it are tolerated); None when absent, ambiguous or invalid."""
    text = visible_text(content).strip()
    if not text or len(text) > 65536:
        return None
    decoder = json.JSONDecoder()
    objects = []
    cursor = 0
    while (start := text.find("{", cursor)) >= 0:
        try:
            obj, length = decoder.raw_decode(text[start:])
        except ValueError:
            return None
        objects.append(obj)
        cursor = start + length  # Never extract a nested object from an invalid envelope.
        if len(objects) > 1:
            return None
    if len(objects) != 1:
        return None
    try:
        return Completion.model_validate(objects[0])
    except ValueError:
        return None


def current_completion(dispatcher):
    if dispatcher.artifact is None:
        raise RecommendationError("STALE_REFERENCE", "No current ranking artifact")
    return Completion(ranking_artifact_id=dispatcher.artifact.artifact_id)


class ScriptedController:
    """Deterministic path: resolve_entities -> build_pool -> get_geographic_evidence ->
    rank_candidates, never search, bind or read; finishes with the current ranking artifact."""

    mode = "scripted"
    model_identifier = None

    def propose(self, dispatcher, context, trace):
        for name in PIPELINE:
            result = dispatcher.call(name, {"context_ref": dispatcher.ref})
            if result.error:
                raise RecommendationError(
                    result.error.code, result.error.message, result.error.retryable
                )
        return current_completion(dispatcher)


class LiveController:
    """Recommendation-local Python LangChain tool-calling agent (agent/llm.py, RECOMMENDATION_LLM_*).

    Code owns the loop: every model request is one trace step (usage, duration, error), tools run
    sequentially through the dispatcher (typed arguments, stage prerequisites, budgets), and the
    final answer must name the CURRENT ranking artifact. Budgets: controller.max_model_turns
    (shared by repairs), max_tool_calls, timeout_seconds; the SDK retry count is
    RECOMMENDATION_LLM_MAX_RETRIES (default 0)."""

    mode = "live"

    def __init__(self, timeout=None, env=None):
        # None: only controller.timeout_seconds from the run's settings bounds the loop.
        self.timeout = timeout
        self.env = env
        self.model_identifier = llm.configured_identifier(env)

    def settings(self):
        return llm.LlmSettings.from_env(self.env)

    def invoke(self, messages, tools, remaining):
        settings = getattr(self, "_settings", None) or self.settings()
        return llm.invoke(settings, messages, tools, settings.attempt_timeout(remaining))

    def propose(self, dispatcher, context, trace):
        # The real model client is configured before the first request (MODEL_NOT_CONFIGURED, no
        # fallback to the application's model). An offline fake that overrides invoke() needs none.
        self._settings = self.settings() if type(self).invoke is LiveController.invoke else None
        tools = model_tools()
        trace.setdefault("agent", {"protocol": AGENT_PROTOCOL,
                                   "model": self._settings.public() if self._settings else None})
        messages = trace.get("controller_messages")
        if messages is None:
            messages = [
            {"role": "system", "content": system_prompt(dispatcher.config)},
            {"role": "user", "content": json.dumps(context)},
            ]
            trace["controller_messages"] = messages
        elif context.get("repair"):
            # Keep the successful tool history across repairs within this run.
            messages.append({
                "role": "user",
                "content": json.dumps({
                    "repair": context["repair"],
                    "instruction": repair_instruction(context["repair"]),
                }),
            })
        started = trace.setdefault("started_monotonic", time.monotonic())
        for turn in range(
            sum(s["kind"] == "model" for s in trace["steps"]),
            dispatcher.config.controller.max_model_turns,
        ):
            remaining = dispatcher.config.controller.timeout_seconds - (
                time.monotonic() - dispatcher.started
            )
            if self.timeout is not None:
                remaining = min(remaining, self.timeout - (time.monotonic() - started))
            if remaining <= 0:
                break
            # Slim trace (P6): only the messages added since the previous model step; the store
            # reconstructs the full list for this context_ref (tracing/slim.py).
            invocation = slim.model_step(trace, f"{dispatcher.ref}/models/{turn}", messages, tools)
            trace["steps"].append(invocation)
            invoked_at = time.monotonic()
            try:
                output = self.invoke(messages, tools, remaining)
                if not isinstance(output, dict):
                    raise RecommendationError(
                        "MODEL_PROTOCOL_ERROR",
                        "Model returned an invalid response envelope",
                    )
                invocation["output"] = output
            except RecommendationError as exc:
                invocation["error"] = exc.detail.model_dump()
                raise
            finally:
                invocation["duration_ms"] = (time.monotonic() - invoked_at) * 1000
            try:
                if output.get("invalid_tool_calls"):
                    raise ValueError("Tool calls with invalid JSON arguments")
                raw_calls = output.get("tool_calls", [])
                if not isinstance(raw_calls, list):
                    raise ValueError("Tool calls must be a list")
                calls = [ModelToolCall.model_validate(call) for call in raw_calls]
                if len({call.id for call in calls}) != len(calls):
                    raise ValueError("Duplicate tool call IDs")
            except (ValueError, ValidationError):
                messages.append(
                    {
                        "role": "user",
                        "content": "Return valid tool calls with unique IDs, names and object arguments, or the final "
                                   '{"ranking_artifact_id": ...} JSON object.',
                    }
                )
                continue
            content = visible_text(output.get("content"))
            if not calls:
                completion = parse_completion(content)
                current = dispatcher.artifact.artifact_id if dispatcher.artifact else None
                if completion is not None and completion.ranking_artifact_id == current:
                    return completion
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": json.dumps({
                    "completion_rejected": "invalid_json" if completion is None else (
                        "no_current_ranking_artifact" if current is None else "not_the_current_ranking_artifact"),
                    "instruction": "Finish only with {\"ranking_artifact_id\": ...} naming the ranking_artifact_id of "
                                   "the latest successful rank_candidates for the current inputs; run the pipeline "
                                   "tools first if there is none.",
                })})
                continue
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        call.model_dump(exclude_none=True) for call in calls
                    ],
                }
            )
            ranked_empty = False
            for call in calls:
                result = dispatcher.call(call.name, call.args)
                if call.name == "rank_candidates" and result.error is None:
                    ranked_empty = dispatcher.ranked == []
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result.model_dump_json(),
                    }
                )
            if ranked_empty and dispatcher.ranked == [] and dispatcher.artifact is not None:
                # A completed empty ranking is a normal result, still checked by final validation.
                trace["controller_completion"] = "assessed_empty_ranking"
                return current_completion(dispatcher)
        raise RecommendationError(
            "LOOP_EXHAUSTED", "Model turn or time budget exhausted"
        )
