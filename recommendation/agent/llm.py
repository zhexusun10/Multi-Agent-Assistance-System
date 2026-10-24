"""Recommendation-local LangChain chat model (Python), configured only by RECOMMENDATION_LLM_*.

The recommendation agent never uses the outer application's model factory (src/model.ts), its
CHAT_MODEL / OPENAI_* variables or a Node.js subprocess. There is no fallback: a missing or invalid
key is MODEL_NOT_CONFIGURED naming the variable (never its value).

Environment (documented in .env.example):
- RECOMMENDATION_LLM_PROVIDER      ``openai`` (OpenAI-compatible chat completions; the only one)
- RECOMMENDATION_LLM_MODEL         model name, e.g. ``gpt-4.1-mini`` or ``deepseek-chat`` (required)
- RECOMMENDATION_LLM_API_KEY       credential (required; never logged, traced or prompted)
- RECOMMENDATION_LLM_BASE_URL      optional OpenAI-compatible endpoint (default https://api.openai.com/v1)
- RECOMMENDATION_LLM_TEMPERATURE   default 0
- RECOMMENDATION_LLM_MAX_TOKENS    optional output-token limit (provider default when unset)
- RECOMMENDATION_LLM_TIMEOUT_SECONDS  per-request timeout, default 60; always capped by the time
  left in the run (controller.timeout_seconds), split across the SDK attempts
- RECOMMENDATION_LLM_MAX_RETRIES   SDK retry count, default 0 (tests keep it 0)

The client is built with every OPENAI_* variable of the process hidden (OPENAI_BASE_URL,
OPENAI_API_BASE, OPENAI_ORG_ID, OPENAI_PROJECT_ID, OPENAI_PROXY, OPENAI_API_KEY ...): the
OpenAI SDK and langchain-openai would otherwise read them for every argument left unset, which
would silently send the recommendation key to the application's endpoint.

An endpoint on api.deepseek.com gets ``thinking`` disabled, as the former TypeScript bridge did.
LangSmith tracing is disabled for every call: the run's own trace is the record.
"""

import math
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlparse

from recommendation.contracts.errors import RecommendationError

PREFIX = "RECOMMENDATION_LLM_"
PROVIDERS = ("openai",)
DEFAULT_BASE_URL = "https://api.openai.com/v1"
OUTER_ENV_PREFIXES = ("OPENAI_",)  # read implicitly by the SDKs; never by the recommendation agent
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 0


def _not_configured(name, problem):
    return RecommendationError("MODEL_NOT_CONFIGURED", f"{PREFIX}{name} {problem}")


def _number(env, name, kind, default, minimum):
    raw = (env.get(PREFIX + name) or "").strip()
    if not raw:
        return default
    try:
        value = kind(raw)
    except ValueError:
        raise _not_configured(name, "is not a valid number") from None
    if not math.isfinite(value) or value < minimum:
        raise _not_configured(name, f"must be a finite number >= {minimum}")
    return value


@dataclass(frozen=True)
class LlmSettings:
    provider: str
    model: str
    api_key: str = field(repr=False)  # never printed by repr / tracebacks
    base_url: str | None
    temperature: float
    max_tokens: int | None
    timeout_seconds: float
    max_retries: int

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        provider = (env.get(PREFIX + "PROVIDER") or "openai").strip().lower()
        if provider not in PROVIDERS:
            raise _not_configured("PROVIDER", f"must be one of {', '.join(PROVIDERS)}")
        model = (env.get(PREFIX + "MODEL") or "").strip()
        if not model:
            raise _not_configured("MODEL", "is required for the live recommendation agent")
        api_key = (env.get(PREFIX + "API_KEY") or "").strip()
        if not api_key:
            raise _not_configured("API_KEY", "is required for the live recommendation agent")
        base_url = (env.get(PREFIX + "BASE_URL") or "").strip() or None
        if base_url is not None and urlparse(base_url).scheme not in ("http", "https"):
            raise _not_configured("BASE_URL", "must be an http(s) URL")
        return cls(
            provider=provider, model=model, api_key=api_key, base_url=base_url,
            temperature=_number(env, "TEMPERATURE", float, DEFAULT_TEMPERATURE, 0),
            max_tokens=_number(env, "MAX_TOKENS", int, None, 1),
            timeout_seconds=_number(env, "TIMEOUT_SECONDS", float, DEFAULT_TIMEOUT_SECONDS, 1),
            max_retries=_number(env, "MAX_RETRIES", int, DEFAULT_MAX_RETRIES, 0),
        )

    @property
    def identifier(self):
        return f"{self.provider}:{self.model}"

    @property
    def endpoint(self):
        return self.base_url or DEFAULT_BASE_URL

    def attempt_timeout(self, remaining):
        """Per-attempt timeout: the configured one, capped so that every SDK attempt fits in the
        time left in the run."""
        return min(self.timeout_seconds, remaining / (self.max_retries + 1))

    def public(self):
        """Non-secret configuration for traces (the endpoint by host only, never the key)."""
        return {"provider": self.provider, "model": self.model,
                "base_url_host": urlparse(self.endpoint).hostname,
                "temperature": self.temperature, "max_tokens": self.max_tokens,
                "timeout_seconds": self.timeout_seconds, "max_retries": self.max_retries}


def configured_identifier(env=None):
    """provider:model when the live agent is configured, else None (never raises)."""
    try:
        return LlmSettings.from_env(env).identifier
    except RecommendationError:
        return None


@contextmanager
def outer_environment_hidden():
    """Hide the application's OPENAI_* variables while the client is constructed (the SDKs read them
    only then). Requests are serialised by the service, so the brief process-wide change is safe."""
    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith(OUTER_ENV_PREFIXES)}
    try:
        yield
    finally:
        os.environ.update(saved)


def chat_model(settings, timeout):
    from langchain_openai import ChatOpenAI

    extra = {}
    if urlparse(settings.endpoint).hostname == "api.deepseek.com":
        extra["extra_body"] = {"thinking": {"type": "disabled"}}
    with outer_environment_hidden():
        return ChatOpenAI(model=settings.model, api_key=settings.api_key, base_url=settings.endpoint,
                          temperature=settings.temperature, max_tokens=settings.max_tokens,
                          timeout=timeout, max_retries=settings.max_retries, **extra)


def to_langchain(messages):
    """Role-dict transcript (the trace format) -> LangChain messages."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    out = []
    for m in messages:
        role = m["role"]
        if role == "system":
            out.append(SystemMessage(content=m["content"]))
        elif role == "tool":
            out.append(ToolMessage(content=m["content"], tool_call_id=m["tool_call_id"]))
        elif role == "assistant":
            calls = [{"name": c["name"], "args": c["args"], "id": c["id"], "type": "tool_call"}
                     for c in m.get("tool_calls") or []]
            out.append(AIMessage(content=m.get("content") or "", tool_calls=calls))
        else:
            out.append(HumanMessage(content=m["content"]))
    return out


def invoke(settings, messages, tools, timeout):
    """One tool-calling model request: {content, tool_calls, usage} (the controller's envelope).

    Provider exceptions can contain endpoint credentials, so none is forwarded."""
    from langsmith import tracing_context

    try:
        model = chat_model(settings, timeout).bind_tools(
            [{"type": "function", "function": tool} for tool in tools])
        with tracing_context(enabled=False):
            result = model.invoke(to_langchain(messages))
    except Exception:
        raise RecommendationError(
            "MODEL_UNAVAILABLE", "Configured model call failed or timed out; check provider configuration", True
        ) from None
    usage = dict(result.usage_metadata) if getattr(result, "usage_metadata", None) else None
    calls = [{"id": c.get("id"), "name": c.get("name"), "args": c.get("args"), "type": c.get("type")}
             for c in result.tool_calls or []]
    # Tool calls whose arguments the provider sent as invalid JSON: reported, never executed.
    invalid = [{"id": c.get("id"), "name": c.get("name"), "error": (c.get("error") or "invalid arguments")[:200]}
               for c in getattr(result, "invalid_tool_calls", None) or []]
    out = {"content": result.content, "tool_calls": calls, "usage": usage}
    if invalid:
        out["invalid_tool_calls"] = invalid
    return out
