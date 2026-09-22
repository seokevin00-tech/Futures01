"""Anthropic client wrapper for the reasoning agents.

Design decisions that matter for this system:

* **The deterministic core never needs this.** Features, backtests, risk sizing
  and the journal all run with no API key and no network. The LLM adds
  judgement on top of measured evidence; it is never the source of the
  evidence. If :class:`LLMClient` is unavailable, every agent falls back to its
  deterministic path and says so.

* **Structured output, not prose.** Agents return JSON validated against an
  explicit schema, because the decision layer compares predictions field by
  field. Free-form analysis cannot be compared objectively.

* **Stable prompt prefixes.** The system prompt and the evidence block are
  ordered stable-first so the cache prefix survives between calls; volatile
  content (the timestamp, the current bar) goes last. A timestamp near the top
  of a system prompt silently invalidates the cache on every single call.

* **Refusal handling and server-side fallbacks** are on by default, so a
  classifier decline on one call does not take down a research cycle.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..timeutil import et_stamp

__all__ = ["LLMClient", "LLMResponse", "LLMUnavailable", "build_client",
           "WEB_SEARCH_TOOL"]

DEFAULT_MODEL = "claude-opus-5"

#: Server tool the news agent uses for live research.
WEB_SEARCH_TOOL: Dict[str, Any] = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 8,
}


class LLMUnavailable(RuntimeError):
    """Raised when the SDK or credentials are absent and no fallback is wanted."""


@dataclass
class LLMResponse:
    """One model response, plus the accounting the journal records."""

    text: str = ""
    parsed: Optional[Any] = None
    stop_reason: str = ""
    refused: bool = False
    refusal_category: Optional[str] = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_s: float = 0.0
    citations: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.refused and not self.error

    def to_dict(self) -> dict:
        return {
            "model": self.model, "stop_reason": self.stop_reason,
            "refused": self.refused, "refusal_category": self.refusal_category,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "latency_s": round(self.latency_s, 2),
            "citations": self.citations[:20], "error": self.error,
        }


class LLMClient:
    """Thin, retrying wrapper over the Anthropic Messages API."""

    def __init__(self, *, model: str = DEFAULT_MODEL, effort: str = "high",
                 max_tokens: int = 16_000, timeout: float = 600.0,
                 max_retries: int = 3, enable_fallbacks: bool = True,
                 api_key: Optional[str] = None):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.enable_fallbacks = enable_fallbacks
        self._client = None
        self._anthropic = None
        self.available = False
        self.unavailable_reason = ""
        self._init_client(api_key)
        # Running totals so a research cycle can report what it spent.
        self.calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read = 0

    def _init_client(self, api_key: Optional[str]) -> None:
        try:
            import anthropic
        except ImportError:
            self.unavailable_reason = (
                "the 'anthropic' package is not installed "
                "(pip install anthropic) - agents will use their deterministic paths")
            return
        self._anthropic = anthropic
        try:
            # A bare constructor resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN
            # or an `ant auth login` profile, so an unset env var does not mean
            # there are no credentials.
            self._client = (anthropic.Anthropic(api_key=api_key, timeout=self.timeout,
                                                max_retries=0)
                            if api_key else
                            anthropic.Anthropic(timeout=self.timeout, max_retries=0))
            self.available = True
        except Exception as exc:                        # noqa: BLE001
            self.unavailable_reason = (
                f"could not construct the Anthropic client ({type(exc).__name__}: "
                f"{exc}) - agents will use their deterministic paths")

    # ------------------------------------------------------------------
    def complete(
        self, *,
        system: str,
        user: str,
        schema: Optional[Dict[str, Any]] = None,
        tools: Optional[Sequence[Dict[str, Any]]] = None,
        effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
        cache_system: bool = True,
        stream: bool = False,
    ) -> LLMResponse:
        """One call. Returns an ``LLMResponse`` rather than raising, so a single
        failed call degrades one agent instead of aborting a cycle."""
        if not self.available or self._client is None:
            return LLMResponse(error=self.unavailable_reason or "LLM unavailable")

        anthropic = self._anthropic
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            # Adaptive thinking: the model decides how much reasoning each
            # question needs. budget_tokens is removed on Opus 5.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort or self.effort},
            # The stable instruction block is cached; the volatile evidence
            # block lives in the user message, after the breakpoint.
            "system": ([{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}]
                       if cache_system else system),
            "messages": [{"role": "user", "content": user}],
        }
        if schema is not None:
            kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}
        if tools:
            kwargs["tools"] = list(tools)

        use_beta = self.enable_fallbacks
        if use_beta:
            # Route a policy decline to Anthropic's recommended substitute
            # instead of returning an empty turn mid-cycle.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"

        last_error = ""
        for attempt in range(self.max_retries + 1):
            started = time.time()
            try:
                api = self._client.beta.messages if use_beta else self._client.messages
                if stream:
                    with api.stream(**kwargs) as s:
                        message = s.get_final_message()
                else:
                    message = api.create(**kwargs)
                return self._to_response(message, time.time() - started, schema)
            except Exception as exc:                    # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                if not self._is_retryable(exc, anthropic):
                    break
                if attempt < self.max_retries:
                    delay = self._retry_delay(exc, anthropic, attempt)
                    time.sleep(delay)
        return LLMResponse(error=last_error, latency_s=0.0)

    # ------------------------------------------------------------------
    @staticmethod
    def _is_retryable(exc: Exception, anthropic: Any) -> bool:
        """Retry transient failures only. A 400 will fail identically forever."""
        if anthropic is None:
            return False
        if isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError,
                            anthropic.APITimeoutError)):
            return True
        if isinstance(exc, anthropic.APIStatusError):
            return exc.status_code >= 500
        return False

    @staticmethod
    def _retry_delay(exc: Exception, anthropic: Any, attempt: int) -> float:
        if anthropic is not None and isinstance(exc, anthropic.RateLimitError):
            try:
                return min(60.0, float(exc.response.headers.get("retry-after", "5")))
            except (AttributeError, ValueError, TypeError):
                pass
        return min(30.0, 2.0 ** attempt)

    def _to_response(self, message: Any, latency: float,
                     schema: Optional[Dict[str, Any]]) -> LLMResponse:
        resp = LLMResponse(model=getattr(message, "model", self.model),
                           latency_s=latency)
        resp.stop_reason = getattr(message, "stop_reason", "") or ""

        # Check the stop reason BEFORE reading content: a refusal can return an
        # empty content array, and indexing into it would raise.
        if resp.stop_reason == "refusal":
            resp.refused = True
            details = getattr(message, "stop_details", None)
            resp.refusal_category = getattr(details, "category", None)
            resp.error = f"request declined ({resp.refusal_category or 'unspecified'})"
            return resp

        usage = getattr(message, "usage", None)
        if usage is not None:
            resp.input_tokens = getattr(usage, "input_tokens", 0) or 0
            resp.output_tokens = getattr(usage, "output_tokens", 0) or 0
            resp.cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
            resp.cache_write_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0

        texts: List[str] = []
        for block in getattr(message, "content", []) or []:
            btype = getattr(block, "type", "")
            if btype == "text":
                texts.append(getattr(block, "text", ""))
            elif btype == "web_search_tool_result":
                # A server-tool error returns an object here, not a list - so
                # branch on the shape before iterating.
                content = getattr(block, "content", None)
                if isinstance(content, list):
                    for item in content:
                        url = getattr(item, "url", None)
                        if url:
                            resp.citations.append(url)
        resp.text = "\n".join(t for t in texts if t).strip()

        if schema is not None and resp.text:
            try:
                resp.parsed = json.loads(resp.text)
            except json.JSONDecodeError as exc:
                resp.error = f"model returned invalid JSON: {exc}"

        self.calls += 1
        self.total_input_tokens += resp.input_tokens
        self.total_output_tokens += resp.output_tokens
        self.total_cache_read += resp.cache_read_tokens
        return resp

    # ------------------------------------------------------------------
    def usage_summary(self) -> Dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "cache_read_tokens": self.total_cache_read,
            "cache_hit_rate": (self.total_cache_read /
                               max(1, self.total_input_tokens + self.total_cache_read)),
            "model": self.model,
        }

    def __repr__(self) -> str:
        state = "available" if self.available else f"unavailable ({self.unavailable_reason})"
        return f"<LLMClient {self.model} {state}>"


def build_client(config: Any = None) -> Optional[LLMClient]:
    """Construct a client from a :class:`~futures_agents.config.SystemConfig`.

    Returns ``None`` when the LLM is switched off, so callers can treat "no
    client" as a first-class, fully supported mode rather than an error.
    """
    if config is not None and not getattr(config, "enable_llm", True):
        return None
    client = LLMClient(
        model=getattr(config, "model", DEFAULT_MODEL),
        effort=getattr(config, "effort", "high"),
        max_tokens=getattr(config, "max_tokens", 16_000),
        timeout=getattr(config, "llm_timeout_seconds", 600.0),
        max_retries=getattr(config, "llm_max_retries", 3),
    )
    return client
