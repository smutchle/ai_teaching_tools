"""Base class for all LLM-backed agents in the exam-design system."""

import inspect
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from anthropic import Anthropic
from anthropic.types import MessageParam
from pydantic import BaseModel, ValidationError

from events import AgentEvent, EventKind, EventLog, summarize

T = TypeVar("T", bound=BaseModel)


class AgentResponseError(RuntimeError):
    """Raised when an agent's LLM response does not contain the expected tool call."""


def _validate_tool_input(response_model: type[T], input_data: Any) -> T:
    """Validate tool input, recovering from common model misbehaviors.

    Some models occasionally stringify the entire response and stuff it under
    a single key (e.g., `{"themes": "<json-of-whole-ThemeList>"}`) instead of
    filling in the schema. Detect that and unwrap before failing.
    """
    if isinstance(input_data, str):
        return response_model.model_validate_json(input_data)
    try:
        return response_model.model_validate(input_data)
    except ValidationError as direct_err:
        if isinstance(input_data, dict) and len(input_data) == 1:
            only_value = next(iter(input_data.values()))
            if isinstance(only_value, str):
                try:
                    unwrapped = json.loads(only_value)
                except json.JSONDecodeError:
                    raise direct_err
                return response_model.model_validate(unwrapped)
        raise


class BaseAgent:
    """LLM-backed agent base.

    Subclasses set PERSONA_NAME (the stem of the .md file in persona_dir) and
    expose typed verbs that internally call self._invoke with a Pydantic response
    model. The persona .md is loaded once at construction and used verbatim as
    the Claude system prompt — it is the agent's constitution.

    Event logging: if an EventLog is passed at construction, every _invoke call
    emits invocation_started and invocation_completed (or invocation_failed)
    events. The verb name is captured from the call stack — derived classes do
    not need to pass it explicitly so long as their typed verbs call _invoke
    directly. The Moderator stamps the current epoch via set_epoch().
    """

    PERSONA_NAME: ClassVar[str] = ""

    def __init__(
        self,
        persona_dir: Path,
        model: str,
        client: Anthropic,
        *,
        event_log: EventLog | None = None,
        max_tokens: int = 4096,
    ) -> None:
        if not self.PERSONA_NAME:
            raise ValueError(
                f"{type(self).__name__} must set the PERSONA_NAME class variable"
            )
        persona_path = persona_dir / f"{self.PERSONA_NAME}.md"
        self._constitution: str = persona_path.read_text(encoding="utf-8")
        self._model: str = model
        self._client: Anthropic = client
        self._max_tokens: int = max_tokens
        self._event_log: EventLog | None = event_log
        self._current_epoch: int = 0

    @property
    def name(self) -> str:
        return self.PERSONA_NAME

    @property
    def constitution(self) -> str:
        return self._constitution

    def set_epoch(self, epoch: int) -> None:
        """Moderator stamps the current epoch so events are bucketed correctly."""
        self._current_epoch = epoch

    def _invoke(
        self,
        user_prompt: str,
        response_model: type[T],
        *,
        context: list[MessageParam] | None = None,
        max_tokens: int | None = None,
        target: str | None = None,
    ) -> T:
        """Call Claude and return its response parsed as `response_model`.

        Uses forced tool use to constrain the response to a JSON object matching
        the model's JSON schema. Malformed responses raise ValidationError with
        the original location intact rather than being wrapped in a generic
        error. API failures propagate unmodified after a failure event is
        recorded — the log is instrumentation, never a wrapper.
        """
        # Capture verb name from the calling frame (the typed verb that invoked us).
        # Falls back to None if introspection fails — log shape stays valid either way.
        verb: str | None
        try:
            verb = inspect.stack()[1].function
        except (IndexError, RuntimeError):
            verb = None

        call_id = uuid.uuid4().hex[:12]
        started_at = time.monotonic()
        self._emit_started(call_id, verb, target, user_prompt)

        tool_name = "submit_response"
        tool: dict[str, Any] = {
            "name": tool_name,
            "description": (
                f"Submit your response as a structured {response_model.__name__}. "
                "Fill in each field according to the input_schema. Arrays must be "
                "passed as JSON arrays and nested objects as JSON objects — do NOT "
                "stringify nested values."
            ),
            "input_schema": response_model.model_json_schema(),
        }
        messages: list[MessageParam] = list(context or [])
        messages.append({"role": "user", "content": user_prompt})

        # Cache the constitution: it is identical across invocations for the life
        # of the agent, so ephemeral cache_control turns repeated calls into a
        # significant cost/latency win once the system prompt exceeds ~1k tokens.
        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": self._constitution,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens or self._max_tokens,
                system=system_blocks,  # type: ignore[arg-type]
                tools=[tool],  # type: ignore[list-item]
                tool_choice={"type": "tool", "name": tool_name},
                messages=messages,
            )
        except Exception as exc:
            self._emit_failed(call_id, verb, target, started_at, f"{type(exc).__name__}: {exc}")
            raise

        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                result = _validate_tool_input(response_model, block.input)
                self._emit_completed(
                    call_id,
                    verb,
                    target,
                    started_at,
                    user_prompt,
                    result,
                    response,
                )
                return result

        error = (
            f"expected tool call {tool_name!r}, got "
            f"stop_reason={response.stop_reason!r}, "
            f"content_types={[b.type for b in response.content]!r}"
        )
        self._emit_failed(call_id, verb, target, started_at, error)
        raise AgentResponseError(f"{self.name}: {error}")

    # -- event emission helpers --------------------------------------------------

    def _emit_started(
        self,
        call_id: str,
        verb: str | None,
        target: str | None,
        user_prompt: str,
    ) -> None:
        if self._event_log is None:
            return
        self._event_log.append(
            AgentEvent(
                timestamp=datetime.now(timezone.utc),
                epoch=self._current_epoch,
                agent=self.name,
                kind=EventKind.INVOCATION_STARTED,
                call_id=call_id,
                verb=verb,
                target=target,
                input_summary=summarize(user_prompt),
            )
        )

    def _emit_completed(
        self,
        call_id: str,
        verb: str | None,
        target: str | None,
        started_at: float,
        user_prompt: str,
        result: BaseModel,
        response: Any,
    ) -> None:
        if self._event_log is None:
            return
        duration_ms = int((time.monotonic() - started_at) * 1000)
        tokens_in: int | None = None
        tokens_out: int | None = None
        usage = getattr(response, "usage", None)
        if usage is not None:
            tokens_in = getattr(usage, "input_tokens", None)
            tokens_out = getattr(usage, "output_tokens", None)
        try:
            raw_response = response.model_dump()
        except AttributeError:
            raw_response = {"_repr": repr(response)}
        self._event_log.write_call_io(
            call_id,
            system=self._constitution,
            user_prompt=user_prompt,
            response=raw_response,
        )
        self._event_log.append(
            AgentEvent(
                timestamp=datetime.now(timezone.utc),
                epoch=self._current_epoch,
                agent=self.name,
                kind=EventKind.INVOCATION_COMPLETED,
                call_id=call_id,
                verb=verb,
                target=target,
                output_summary=summarize(result.model_dump_json()),
                duration_ms=duration_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        )

    def _emit_failed(
        self,
        call_id: str,
        verb: str | None,
        target: str | None,
        started_at: float,
        error: str,
    ) -> None:
        if self._event_log is None:
            return
        duration_ms = int((time.monotonic() - started_at) * 1000)
        self._event_log.append(
            AgentEvent(
                timestamp=datetime.now(timezone.utc),
                epoch=self._current_epoch,
                agent=self.name,
                kind=EventKind.INVOCATION_FAILED,
                call_id=call_id,
                verb=verb,
                target=target,
                duration_ms=duration_ms,
                error=error,
            )
        )
