"""Provider abstraction for forced-tool-use LLM calls.

Two backends today:

- `AnthropicProvider` — claude-opus-4-7 / claude-sonnet-4-6 via the Anthropic
  SDK, with ephemeral system-prompt caching.
- `OpenAIProvider` — any OpenAI-compatible endpoint (e.g., the VT ARC proxy
  at https://llm-api.arc.vt.edu/api/v1 serving gpt-oss-120b) via the
  `openai` SDK.

Both expose the same `call_with_tool(...)` surface returning a `CallResult`.
The differences in system-message shape, tool schema key names, and tool-call
response location are handled inside each provider; the caller (`BaseAgent`)
sees a unified contract.

Per-agent routing happens in `config.MODEL_REGISTRY`: each persona maps to a
`(provider_kind, model_id)` choice, and `config.make_provider(persona)`
hands back the right provider instance.
"""

from __future__ import annotations

import json
import threading
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, ContextManager, Protocol

from anthropic import Anthropic
from openai import OpenAI


@dataclass
class CallResult:
    """Unified result of one forced-tool-use call.

    raw_response is the provider's response object serialized to a dict for
    the sidecar audit log. tool_input is the dict the model returned in the
    tool call (or None if the model failed to invoke the tool).
    """

    raw_response: dict[str, Any]
    tool_input: dict[str, Any] | None
    tokens_in: int | None
    tokens_out: int | None


class LLMProvider(Protocol):
    """One forced-tool-use call to a specific model."""

    @property
    def model(self) -> str: ...

    def call_with_tool(
        self,
        *,
        system: str,
        user_prompt: str,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
        max_tokens: int,
    ) -> CallResult: ...


def _safe_model_dump(obj: Any) -> dict[str, Any]:
    try:
        return obj.model_dump()
    except AttributeError:
        return {"_repr": repr(obj)}


class AnthropicProvider:
    """Anthropic SDK + forced tool use + ephemeral system caching."""

    def __init__(
        self,
        client: Anthropic,
        *,
        model: str,
        concurrency_sem: threading.Semaphore | None = None,
        timeout: float | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._sem = concurrency_sem
        self._timeout = timeout

    def _gate(self) -> ContextManager[Any]:
        return self._sem if self._sem is not None else nullcontext()

    @property
    def model(self) -> str:
        return self._model

    def call_with_tool(
        self,
        *,
        system: str,
        user_prompt: str,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
        max_tokens: int,
    ) -> CallResult:
        # Constitution is identical across an agent's calls — ephemeral cache
        # turns repeated calls into a real cost/latency win once the system
        # prompt exceeds ~1k tokens.
        system_blocks: list[dict[str, Any]] = [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]
        tool: dict[str, Any] = {
            "name": tool_name,
            "description": tool_description,
            "input_schema": tool_schema,
        }
        kwargs: dict[str, Any] = dict(
            model=self._model,
            max_tokens=max_tokens,
            system=system_blocks,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user_prompt}],
        )
        if self._timeout is not None:
            kwargs["timeout"] = self._timeout
        with self._gate():
            response = self._client.messages.create(**kwargs)  # type: ignore[arg-type]
        tool_input: dict[str, Any] | None = None
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                tool_input = block.input  # type: ignore[assignment]
                break
        usage = getattr(response, "usage", None)
        return CallResult(
            raw_response=_safe_model_dump(response),
            tool_input=tool_input,
            tokens_in=getattr(usage, "input_tokens", None) if usage else None,
            tokens_out=getattr(usage, "output_tokens", None) if usage else None,
        )


class OpenAIProvider:
    """OpenAI-compatible endpoint (ARC, OpenAI, LiteLLM, vLLM, Ollama, etc.).

    Uses `response_format=json_schema` (strict) rather than forced tool
    calling. vLLM-served open-weight models like gpt-oss-120b don't reliably
    honor `tool_choice=required` — they may emit free-text in `content`
    instead of invoking the tool. `json_schema` strict mode is enforced at
    decode time by the server and lands clean JSON in `msg.content`.

    `concurrency_sem` is the lever that keeps single-GPU Ollama from blowing
    up under fan-out: 8 parallel HTTP requests against one GPU serialize
    inside Ollama and the SDK times out. Pass a `threading.Semaphore(1)` for
    Ollama; cloud backends can take a larger cap or `None` for unbounded.

    `max_tokens_floor` raises the effective max_tokens to at least this value
    regardless of what the caller asked for. Thinking models like Gemma4 spend
    thousands of tokens in a hidden `reasoning` channel before emitting the
    structured `content`; without a generous floor they hit `finish_reason=
    length` mid-reasoning and produce an empty `content`. 16k is comfortable
    for our nested-schema verbs (EditResult, ItemDraftList).
    """

    def __init__(
        self,
        client: OpenAI,
        *,
        model: str,
        concurrency_sem: threading.Semaphore | None = None,
        timeout: float | None = None,
        max_tokens_floor: int | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._sem = concurrency_sem
        self._timeout = timeout
        self._max_tokens_floor = max_tokens_floor

    def _gate(self) -> ContextManager[Any]:
        return self._sem if self._sem is not None else nullcontext()

    @property
    def model(self) -> str:
        return self._model

    def call_with_tool(
        self,
        *,
        system: str,
        user_prompt: str,
        tool_name: str,
        tool_description: str,  # unused under json_schema path; kept for interface symmetry
        tool_schema: dict[str, Any],
        max_tokens: int,
    ) -> CallResult:
        effective_max_tokens = max_tokens
        if self._max_tokens_floor is not None:
            effective_max_tokens = max(effective_max_tokens, self._max_tokens_floor)
        kwargs: dict[str, Any] = dict(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": (
                        user_prompt
                        + "\n\nReturn ONLY a JSON object matching the "
                          "requested schema. No markdown fences, no "
                          "commentary, no preface."
                    ),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": tool_name,
                    "schema": tool_schema,
                    "strict": True,
                },
            },
            max_tokens=effective_max_tokens,
        )
        if self._timeout is not None:
            kwargs["timeout"] = self._timeout
        with self._gate():
            response = self._client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        tool_input = _parse_json_content(msg.content) if msg.content else None
        usage = getattr(response, "usage", None)
        return CallResult(
            raw_response=_safe_model_dump(response),
            tool_input=tool_input,
            tokens_in=getattr(usage, "prompt_tokens", None) if usage else None,
            tokens_out=getattr(usage, "completion_tokens", None) if usage else None,
        )


def _parse_json_content(content: str) -> dict[str, Any] | None:
    """Parse JSON from model content; strip ``` fences if present.

    Strict json_schema mode normally returns bare JSON, but some
    OpenAI-compatible backends wrap it in a code fence anyway. Be tolerant.
    """
    text = content.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` or ``` ... ``` fences.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Leave the raw content under a sentinel key so the sidecar still
        # shows what the model produced; downstream validation will fail
        # loudly with a useful error.
        return {"_raw_content": content}
