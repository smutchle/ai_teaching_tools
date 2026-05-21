"""Base class for all LLM-backed agents in the exam-design system."""

import inspect
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ValidationError

from events import AgentEvent, EventKind, EventLog, summarize
from providers import CallResult, LLMProvider

T = TypeVar("T", bound=BaseModel)


class AgentResponseError(RuntimeError):
    """Raised when an agent's LLM response does not contain the expected tool call."""


def _recursive_unwrap(v: Any) -> Any:
    """Recursively unwrap stringified-JSON values in a dict/list tree.

    Whenever a string value parses via `json.loads` to a dict or list, swap
    it in and recurse into the result. Strings that parse to JSON primitives
    (numbers, bools, plain strings) are kept as-is — those are legitimate
    string fields, not stringified complex objects. Strings that don't parse
    at all are also kept as-is.

    Drives Strategy A's per-field unwrap and feeds Strategy B's post-unwrap
    recursion. Single shared traversal so any Opus stringification depth is
    handled uniformly.
    """
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError:
            return v
        if isinstance(parsed, (dict, list)):
            return _recursive_unwrap(parsed)
        # Parsed to a JSON primitive — keep the original string. A legitimate
        # rationale or claim that happens to be parseable shouldn't be silently
        # demoted to int/bool/null.
        return v
    if isinstance(v, dict):
        return {k: _recursive_unwrap(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_recursive_unwrap(item) for item in v]
    return v


def _validate_tool_input(response_model: type[T], input_data: Any) -> T:
    """Validate tool input, recovering from common model misbehaviors.

    Observed Claude misbehaviors with forced tool use:
    1. Entire response is a JSON string instead of a dict.
    2. Single-key wrapper with whole response stringified under that key:
       `{"themes": "<JSON-of-whole-ThemeList>"}`.
    3. Some nested object/array fields stringified while siblings are correct:
       `{"updated_draft": "<JSON-of-ItemDraft>", "rationale": "ok text"}`.
    4. Multi-level stringification — a stringified value that, when parsed,
       contains *more* stringified children.

    Strategy: validate as-is; on failure, recursively unwrap any string value
    that parses to a dict/list and re-validate (handles cases 3 and 4); if
    that still fails and the input is a single-key wrapper, unwrap that and
    recurse (handles case 2). Raise the original error if nothing recovers.
    """
    if isinstance(input_data, str):
        return response_model.model_validate_json(input_data)
    try:
        return response_model.model_validate(input_data)
    except ValidationError as direct_err:
        if not isinstance(input_data, dict):
            raise

        # Strategy A: recursive per-field unwrap of stringified complex values.
        # Covers both single-level stringification and deeper nestings.
        try:
            return response_model.model_validate(_recursive_unwrap(input_data))
        except ValidationError:
            pass

        # Strategy B: single-key wrapper with whole response under it. Apply
        # the same recursive unwrap to the parsed payload so any inner
        # stringification is also recovered.
        if len(input_data) == 1:
            only_value = next(iter(input_data.values()))
            if isinstance(only_value, str):
                try:
                    parsed = json.loads(only_value)
                except json.JSONDecodeError:
                    raise direct_err
                return response_model.model_validate(_recursive_unwrap(parsed))

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
        provider: LLMProvider,
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
        self._provider: LLMProvider = provider
        self._max_tokens: int = max_tokens
        self._event_log: EventLog | None = event_log
        self._current_epoch: int = 0

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def name(self) -> str:
        return self.PERSONA_NAME

    @property
    def constitution(self) -> str:
        return self._constitution

    def set_epoch(self, epoch: int) -> None:
        """Moderator stamps the current epoch so events are bucketed correctly."""
        self._current_epoch = epoch

    def append_to_constitution(self, text: str) -> None:
        """Append a runtime directive to this agent's constitution.

        Used by the Moderator at startup to inject per-run policies that
        don't belong in the persona .md file — notation rules from the
        ExamSpec, trade-off priority hints, etc. The constitution is the
        cached system prompt, so calls *after* this point pay one cache
        miss to absorb the new text, then re-cache.
        """
        if not text.strip():
            return
        sep = "\n\n" if not self._constitution.endswith("\n") else ""
        self._constitution = self._constitution + sep + text.strip() + "\n"

    def _invoke(
        self,
        user_prompt: str,
        response_model: type[T],
        *,
        max_tokens: int | None = None,
        target: str | None = None,
    ) -> T:
        """Call the agent's LLM via the configured provider and return the
        response parsed as `response_model`.

        Uses forced tool use to constrain the response to a JSON object matching
        the model's JSON schema. Provider differences (Anthropic vs OpenAI tool
        shapes, system-message format, cache controls) are handled inside the
        provider; this method sees a uniform `CallResult`.
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
        tool_description = (
            f"Submit your response as a structured {response_model.__name__}. "
            "Fill in each field according to the schema. Arrays must be passed "
            "as JSON arrays and nested objects as JSON objects — do NOT "
            "stringify nested values."
        )

        try:
            result: CallResult = self._provider.call_with_tool(
                system=self._constitution,
                user_prompt=user_prompt,
                tool_name=tool_name,
                tool_description=tool_description,
                tool_schema=response_model.model_json_schema(),
                max_tokens=max_tokens or self._max_tokens,
            )
        except Exception as exc:
            self._emit_failed(
                call_id, verb, target, started_at,
                f"{type(exc).__name__}: {exc}",
            )
            raise

        # Capture the sidecar BEFORE validation. A validation failure is
        # exactly when we most need the raw response to diagnose.
        self._write_sidecar_safe(call_id, user_prompt, result.raw_response)

        if result.tool_input is None:
            error = f"no tool call returned for {tool_name!r}"
            self._emit_failed(call_id, verb, target, started_at, error)
            raise AgentResponseError(f"{self.name}: {error}")

        try:
            parsed = _validate_tool_input(response_model, result.tool_input)
        except (ValidationError, ValueError) as exc:
            self._emit_failed(
                call_id, verb, target, started_at,
                f"{type(exc).__name__}: {exc}",
            )
            raise

        self._emit_completed(
            call_id, verb, target, started_at,
            parsed, result.tokens_in, result.tokens_out,
        )
        return parsed

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

    def _write_sidecar_safe(
        self,
        call_id: str,
        user_prompt: str,
        raw_response: dict[str, Any],
    ) -> None:
        """Write the full I/O sidecar. Always called before validation so the
        raw response is available even when validation later raises.

        `raw_response` comes from the provider already-serialized to a dict so
        no SDK-specific code lives here."""
        if self._event_log is None:
            return
        self._event_log.write_call_io(
            call_id,
            system=self._constitution,
            user_prompt=user_prompt,
            response=raw_response,
        )

    def _emit_completed(
        self,
        call_id: str,
        verb: str | None,
        target: str | None,
        started_at: float,
        result: BaseModel,
        tokens_in: int | None,
        tokens_out: int | None,
    ) -> None:
        if self._event_log is None:
            return
        duration_ms = int((time.monotonic() - started_at) * 1000)
        # Sidecar already written by _write_sidecar_safe before validation.
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
