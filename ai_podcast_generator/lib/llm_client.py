"""Thin typed client for the Anthropic API.

Deliberately not a framework: one class, two methods, no agent abstractions. API
errors from the SDK propagate untouched so the UI can show the real status code.

Three things about Claude Opus 5 shape this module:

- `temperature` is rejected outright (400). Variation is a prompting concern now,
  not a sampling parameter - see the note on `complete`.
- Thinking is on by default and its tokens count against `max_tokens`, so every
  budget here has to cover the reasoning as well as the answer.
- Safety classifiers can decline a request, which arrives as a normal 200 with
  `stop_reason="refusal"` rather than an exception. `fallbacks` re-runs those on
  a second model inside the same call.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic

from .config import Settings

# (visible characters so far, reasoning characters so far)
ProgressCallback = Callable[[int, int], None]

# Claude Opus 5's actual output ceiling, confirmed against the Models API
# (max_tokens=128000, max_input_tokens=1000000). This is the model's limit, not
# a policy of ours: max_tokens is a ceiling rather than a reservation, unused
# budget is not billed, and a request that asks for the full ceiling and answers
# in forty tokens is charged for forty. So every call asks for all of it and
# lets the prompt decide the length. Nothing here should cap output below this.
MAX_OUTPUT_TOKENS = 128_000

# Effort controls how much Claude thinks and how thoroughly it works. Script
# writing is quality-sensitive rather than latency-sensitive, so this sits above
# the API default of "high" only where it earns its keep; "low" is for the
# connection test, which just needs a pulse.
DEFAULT_EFFORT = "high"

# A declined request would otherwise surface as an empty script. Source PDFs are
# arbitrary - a paper on malware analysis or a pathogen is entirely plausible in
# a teaching context - so let the API re-run a refusal on its recommended
# fallback model instead of failing the episode.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_JSON_FENCE_LANGUAGES = ("```json", "```")


class LLMClient:
    """Messages against the configured Claude model."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = anthropic.Anthropic(
            api_key=settings.api_key,
            timeout=900.0,
            max_retries=2,
        )

    @property
    def model(self) -> str:
        return self._settings.model

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = MAX_OUTPUT_TOKENS,
        effort: str = DEFAULT_EFFORT,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        """Completion returning the assistant's text.

        Always streams: `max_tokens` defaults to the model's full ceiling, and
        the SDK refuses non-streaming requests it estimates will outlive the
        HTTP timeout.

        There is no `temperature`: Claude Opus 5 rejects it, along with `top_p`
        and `top_k`. Where the old code varied temperature to get variety - the
        titles and cover-art prompts - the prompts themselves now ask for it,
        which is the supported lever and a more direct one.

        `on_progress` receives (visible characters, thinking characters) as they
        arrive. Thinking is summarised rather than omitted purely so that counter
        moves: on a long script Claude can think for a while before writing
        anything, and a frozen zero reads as a hung app.
        """
        text_parts: list[str] = []
        thinking_chars = 0

        with self._client.beta.messages.stream(
            model=self._settings.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": effort},
            betas=[_FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            for event in stream:
                if event.type != "content_block_delta":
                    continue
                delta = event.delta
                if delta.type == "text_delta":
                    text_parts.append(delta.text)
                    if on_progress is not None:
                        on_progress(
                            sum(len(p) for p in text_parts), thinking_chars
                        )
                elif delta.type == "thinking_delta":
                    thinking_chars += len(delta.thinking)
                    if on_progress is not None:
                        on_progress(
                            sum(len(p) for p in text_parts), thinking_chars
                        )

            message = stream.get_final_message()

        return self._text_or_raise(message, "".join(text_parts), thinking_chars)

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_tokens: int = MAX_OUTPUT_TOKENS,
        effort: str = DEFAULT_EFFORT,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, object]:
        """Completion constrained to a JSON object matching `schema`.

        The schema is enforced by the API, so the reply is valid JSON of the
        right shape or the request fails - no fenced-block scraping, no "the
        model wrote a sentence before the object" recovery path.
        """
        text_parts: list[str] = []
        thinking_chars = 0

        with self._client.beta.messages.stream(
            model=self._settings.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            betas=[_FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            for event in stream:
                if event.type != "content_block_delta":
                    continue
                delta = event.delta
                if delta.type == "text_delta":
                    text_parts.append(delta.text)
                    if on_progress is not None:
                        on_progress(
                            sum(len(p) for p in text_parts), thinking_chars
                        )
                elif delta.type == "thinking_delta":
                    thinking_chars += len(delta.thinking)

            message = stream.get_final_message()

        raw = self._text_or_raise(message, "".join(text_parts), thinking_chars)
        return _parse_json_object(raw)

    def _text_or_raise(
        self, message: Any, text: str, thinking_chars: int
    ) -> str:
        """Validate the terminal state before trusting the accumulated text.

        `stop_reason` has to be checked first: a refusal is a successful HTTP
        response whose content is empty or half-written, so reading the text
        without looking would silently produce a truncated script.
        """
        stop_reason = getattr(message, "stop_reason", None)

        if stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            raise RuntimeError(
                f"{self._settings.model} declined this request "
                f"(category: {category}). The fallback model declined it too. "
                "This is usually the source material rather than the podcast "
                "settings - try a different document."
            )

        cleaned = text.strip()
        if not cleaned:
            raise RuntimeError(
                f"Model {self._settings.model} returned no text "
                f"(stop_reason={stop_reason!r}, {thinking_chars} thinking "
                "characters). If stop_reason is 'max_tokens', thinking consumed "
                "the whole budget - raise max_tokens or lower the effort level."
            )

        if stop_reason == "max_tokens":
            # Not fatal on its own - the caller may still have a usable script -
            # but a script cut mid-sentence is worth naming rather than passing
            # quietly to the speech synthesiser.
            raise RuntimeError(
                f"Model {self._settings.model} hit the {len(cleaned)}-character "
                "output cap mid-answer. Raise max_tokens, shorten the target "
                "length, or lower the effort level."
            )

        return cleaned


def _parse_json_object(raw: str) -> dict[str, object]:
    """Parse a schema-constrained reply.

    Structured outputs guarantee a bare JSON object, so this is a straight parse
    with one tolerance for a fenced block, and an error that quotes the offending
    text rather than a bare JSONDecodeError.
    """
    candidate = raw.strip()
    for fence in _JSON_FENCE_LANGUAGES:
        if candidate.startswith(fence):
            candidate = candidate[len(fence) :]
            if candidate.endswith("```"):
                candidate = candidate[:-3]
            candidate = candidate.strip()
            break

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Expected a JSON object in the model reply but could not parse one "
            f"({error}). Reply was:\n{raw[:1500]}"
        ) from error

    if not isinstance(parsed, dict):
        raise ValueError(
            f"Expected a JSON object, got {type(parsed).__name__}. "
            f"Reply was:\n{raw[:1500]}"
        )
    return parsed
