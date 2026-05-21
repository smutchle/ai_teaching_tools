"""Base class for all LLM-backed agents in the exam-design system."""

import inspect
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ValidationError

from events import AgentEvent, EventKind, EventLog, summarize
from providers import CallResult, LLMProvider


# Models stringifying nested arrays/objects often emit invalid JSON escapes.
# The JSON spec only allows backslash followed by: " \ / b f n r t or uXXXX.
# Anything else — `\D` for LaTeX `\Delta`, `\'` for an apostrophe, `\c` for
# `\circ`, etc. — is malformed and `json.loads` throws "Invalid \escape".
#
# The regex consumes each `\X` pair as a single match. Valid escapes (the
# named alternatives + uXXXX) match the upper branches with group(1)=None
# and pass through unchanged. Anything else falls to the catch-all `(.)`
# branch with group(1)=the offending char, and we double the backslash so
# `\D` becomes `\\D` — which json.loads correctly decodes back to `\D` in
# the resulting string. Consuming pairs left-to-right also avoids the
# trap where a valid `\\` (two backslashes) gets misread as invalid.
_ESCAPE_PAIR_RE = re.compile(
    r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}|(.))', re.DOTALL,
)


def _repair_json_escapes(s: str) -> str:
    def _fix(m: re.Match[str]) -> str:
        bad = m.group(1)
        if bad is None:
            return m.group(0)  # valid escape — pass through
        return "\\\\" + bad

    return _ESCAPE_PAIR_RE.sub(_fix, s)

T = TypeVar("T", bound=BaseModel)


class AgentResponseError(RuntimeError):
    """Raised when an agent's LLM response does not contain the expected tool call."""


def _try_loads_lenient(s: str) -> Any | None:
    """json.loads with a single fallback: if the strict parse fails due to a
    non-JSON escape that a model commonly emits (e.g. `\\'`), repair and
    retry once. Returns the parsed value, or None if even the repaired form
    won't parse."""
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        repaired = _repair_json_escapes(s)
        if repaired == s:
            return None
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return None


def _recursive_unwrap(v: Any) -> Any:
    """Recursively unwrap stringified-JSON values in a dict/list tree.

    Whenever a string value parses via `json.loads` (with a tolerant
    fallback for known model-emitted escape mistakes) to a dict or list,
    swap it in and recurse. Strings that parse to JSON primitives are kept
    as-is — those are legitimate string fields, not stringified complex
    objects. Strings that don't parse at all (even after repair) are also
    kept as-is.

    Drives Strategy A's per-field unwrap and feeds Strategy B's post-unwrap
    recursion. Single shared traversal so any stringification depth is
    handled uniformly.
    """
    if isinstance(v, str):
        parsed = _try_loads_lenient(v)
        if parsed is None:
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
        max_attempts: int = 2,
    ) -> None:
        if not self.PERSONA_NAME:
            raise ValueError(
                f"{type(self).__name__} must set the PERSONA_NAME class variable"
            )
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        persona_path = persona_dir / f"{self.PERSONA_NAME}.md"
        self._constitution: str = persona_path.read_text(encoding="utf-8")
        self._provider: LLMProvider = provider
        self._max_tokens: int = max_tokens
        # On validation/no-tool-call failure, re-prompt the model with the
        # error and try again, up to this many total attempts. 1 = no retry.
        self._max_attempts: int = max_attempts
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

        On model-output failure (no tool call, or `ValidationError` from the
        Pydantic schema), retry up to `self._max_attempts` times, each retry
        feeding the previous error back to the model in the user prompt:
        *"Your previous response failed validation: <error>. Re-submit
        matching the schema."* This catches the dominant class of failures
        (terse models omitting required fields, stringified nested objects
        the recursive-unwrap can't recover, etc.) without the orchestrator
        needing to anticipate every shape.

        Each attempt writes its own sidecar (`<call_id>.json` for the first
        attempt; `<call_id>_attempt_N.json` for retries) so the audit trail
        shows what the model said on each try. The event stream stays clean:
        one INVOCATION_STARTED, one final INVOCATION_COMPLETED or
        INVOCATION_FAILED — intermediate retries are not separate events.

        Provider exceptions (network, rate limits, etc.) are *not* retried
        here — they propagate immediately after the failed event.
        """
        # Capture verb name from the calling frame (the typed verb that invoked us).
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
        tool_schema = response_model.model_json_schema()
        effective_max_tokens = max_tokens or self._max_tokens

        prompt = user_prompt
        last_exc: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                result: CallResult = self._provider.call_with_tool(
                    system=self._constitution,
                    user_prompt=prompt,
                    tool_name=tool_name,
                    tool_description=tool_description,
                    tool_schema=tool_schema,
                    max_tokens=effective_max_tokens,
                )
            except Exception as exc:
                # Provider-level failures (network, rate limit) don't get
                # retried here — they bubble up immediately.
                self._emit_failed(
                    call_id, verb, target, started_at,
                    f"{type(exc).__name__}: {exc}",
                )
                raise

            # Sidecar per attempt: first attempt uses call_id, retries get
            # a `_attempt_N` suffix so the audit trail shows every try.
            sidecar_id = call_id if attempt == 1 else f"{call_id}_attempt_{attempt}"
            self._write_sidecar_safe(sidecar_id, prompt, result.raw_response)

            attempt_exc: Exception | None
            if result.tool_input is None:
                attempt_exc = AgentResponseError(
                    f"no tool call returned for {tool_name!r}"
                )
            else:
                try:
                    parsed = _validate_tool_input(response_model, result.tool_input)
                except (ValidationError, ValueError) as exc:
                    attempt_exc = exc
                else:
                    self._emit_completed(
                        call_id, verb, target, started_at,
                        parsed, result.tokens_in, result.tokens_out,
                    )
                    return parsed

            # We have a validation failure (or no-tool-call). Either retry
            # or surface depending on attempts remaining.
            last_exc = attempt_exc
            if attempt < self._max_attempts:
                # Build a corrective prompt: the original user prompt plus
                # an addendum that quotes the error and asks for a corrected
                # submission. Keep the addendum short — the model has to
                # parse it, not produce it.
                prompt = (
                    f"{user_prompt}\n\n"
                    f"--- PREVIOUS ATTEMPT {attempt} FAILED VALIDATION ---\n"
                    f"{type(attempt_exc).__name__}: "
                    f"{str(attempt_exc)[:800]}\n\n"
                    "Re-submit your response, matching the schema exactly. "
                    "Common mistakes to avoid: omitting required fields, "
                    "stringifying nested objects or arrays, invalid JSON "
                    "escapes inside strings."
                )
                continue

            # Last attempt — emit failure and propagate.
            self._emit_failed(
                call_id, verb, target, started_at,
                f"{type(attempt_exc).__name__}: {attempt_exc}",
            )
            raise attempt_exc

        # Defensive: should never reach here because the loop returns or raises.
        assert last_exc is not None
        raise last_exc

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
