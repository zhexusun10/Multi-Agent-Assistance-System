import json, subprocess, time, os
from typing import Any
from pydantic import ValidationError
from pydantic import Field
from recommendation.contracts.requirements import Contract
from recommendation.contracts.errors import RecommendationError
from ..repositories.connections import ROOT
from ..tools.registry import PIPELINE, model_tools
from .prompt import repair_instruction, system_prompt
from recommendation.contracts.limits import MAX_RESULT_LIMIT
from ..tracing import slim


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


class Proposal(Contract):
    listing_ids: list[str] = Field(max_length=MAX_RESULT_LIMIT)


class ScriptedController:
    """Deterministic path: resolve_entities -> build_pool -> get_geographic_evidence ->
    rank_candidates, never search or bind; proposes the recorded selection."""

    mode = "scripted"
    model_identifier = None

    def propose(self, dispatcher, context, trace):
        for name in PIPELINE:
            result = dispatcher.call(name, {"context_ref": dispatcher.ref})
            if result.error:
                raise RecommendationError(
                    result.error.code, result.error.message, result.error.retryable
                )
        return Proposal(listing_ids=list(dispatcher.selected or []))


class LiveController:
    mode = "live"

    def __init__(self, timeout=None):
        # None: only controller.timeout_seconds from the run's settings bounds the loop.
        self.timeout = timeout
        self.model_identifier = (
            os.getenv("CHAT_MODEL", "").strip() or "openai:gpt-4.1-mini"
        )

    def invoke(self, messages, tools, remaining):
        try:
            process = subprocess.run(
                [
                    "node",
                    "--import",
                    "tsx",
                    str(ROOT / "recommendation/agent/model_bridge.ts"),
                ],
                input=json.dumps({"messages": messages, "tools": tools}),
                text=True,
                capture_output=True,
                cwd=ROOT,
                timeout=remaining,
                check=True,
            )
            return json.loads(process.stdout)
        except Exception:
            raise RecommendationError(
                "MODEL_UNAVAILABLE",
                "Configured model call failed or timed out; check provider configuration",
                True,
            ) from None

    def propose(self, dispatcher, context, trace):
        tools = model_tools()
        messages = trace.get("controller_messages")
        if messages is None:
            messages = [
            {"role": "system", "content": system_prompt(dispatcher.config)},
            {"role": "user", "content": json.dumps(context)},
            ]
            trace["controller_messages"] = messages
        elif context.get("repair"):
            # Keep the successful tool history across final-gate repairs within this run.
            messages.append({
                "role": "user",
                "content": json.dumps({
                    "repair": context["repair"],
                    "recorded_selection": list(dispatcher.selected or []),
                    "instruction": repair_instruction(dispatcher),
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
                        "content": "Return valid tool calls with unique IDs, names and object arguments, or a final listing_ids JSON object.",
                    }
                )
                continue
            content = visible_text(output.get("content"))
            if not calls:
                try:
                    return Proposal.model_validate_json(content)
                except (ValueError, KeyError):
                    messages.append({"role": "assistant", "content": content})
                    messages.append(
                        {
                            "role": "user",
                            "content": "Return valid JSON with listing_ids only: the selected_ids of the latest rank_candidates.",
                        }
                    )
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
            if ranked_empty and dispatcher.ranked == []:
                # A completed empty ranking is a normal result, still checked by final_gate.
                trace["controller_completion"] = "assessed_empty_ranking"
                return Proposal(listing_ids=[])
        raise RecommendationError(
            "LOOP_EXHAUSTED", "Model turn or time budget exhausted"
        )
