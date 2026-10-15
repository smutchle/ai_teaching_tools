"""Single entry point for every LLM call made by the Notes Converter.

Providers
---------
Every call goes through the Anthropic Messages API, against one of two
providers:

* **ARC** (default) -- Virginia Tech's hosted LLM proxy, configured by
  ``ARC_ENDPOINT`` / ``ARC_API_KEY`` / ``ARC_MODEL``. A user may supply their
  own ARC key in the UI, which replaces ``ARC_API_KEY`` for their session. The
  proxy speaks both the OpenAI and Anthropic APIs; the Anthropic route lives at
  the endpoint with its trailing ``/v1`` removed (the SDK appends
  ``/v1/messages`` itself). The ``vt-arc-llm`` alias always points at a
  frontier model with vision. Buffered (non-streaming) requests are capped at
  8000 output tokens, but streamed ones are not -- and every call here streams.
* **Claude** (fallback) -- Anthropic directly, configured by
  ``CLAUDE_API_KEY`` / ``CLAUDE_MODEL``.

``FailoverLLM`` holds one session's view of the two: it calls ARC until ARC is
unavailable, then switches to Claude for the rest of that session and retries
the failed call there. When the ARC key is the user's own, a rejected key is
reported rather than failed over, so the user knows their key isn't working.

Truncation
----------
Extended thinking is on by default on current models, and thinking tokens are
billed against ``max_tokens``. A request with a small budget can therefore
spend the entire budget thinking and come back with a ThinkingBlock and no
TextBlock at all -- ``stop_reason == "max_tokens"`` with nothing usable in it.
``create_text_message`` gives every call room for thinking plus its answer, and
when a response still comes back truncated it retries once with a larger budget
(never above the provider's output cap) before giving up loudly.

Every call is made with ``client.messages.stream``. The SDK refuses a
non-streaming request whose ``max_tokens`` implies a generation that could run
past its 10-minute HTTP timeout, and the ARC proxy refuses buffered requests
above 8000 tokens; streaming sidesteps both.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from anthropic import (
    Anthropic,
    APIConnectionError,
    APIError,
    APIStatusError,
)
from anthropic.types import Message, TextBlock

logger = logging.getLogger(__name__)

# Output ceilings. Requests stream, so neither is bounded by the SDK's
# non-streaming timeout or ARC's 8000-token buffered-mode cap.
CLAUDE_MAX_OUTPUT_TOKENS: int = 64000
ARC_MAX_OUTPUT_TOKENS: int = 64000

DEFAULT_CLAUDE_MODEL: str = "claude-sonnet-5"
DEFAULT_ARC_ENDPOINT: str = "https://llm-api.arc.vt.edu/api/v1"
DEFAULT_ARC_MODEL: str = "vt-arc-llm"

# HTTP statuses meaning the API key was rejected.
AUTH_STATUS_CODES: frozenset[int] = frozenset({401, 403})

# Multiplier applied to the caller's budget when a response comes back truncated.
RETRY_BUDGET_FACTOR: int = 4

# Fail fast when a provider is unreachable so failover happens in seconds, not
# minutes; the read timeout stays generous for long streamed generations.
CLIENT_TIMEOUT: httpx.Timeout = httpx.Timeout(600.0, connect=10.0)

# HTTP statuses that mean "this provider can't serve us right now" rather than
# "this request is malformed".
UNAVAILABLE_STATUS_CODES: frozenset[int] = frozenset({401, 403, 404, 408, 429})


class TruncatedResponseError(RuntimeError):
    """Raised when a response carries no usable text even after a retry.

    Attributes:
        purpose: Human-readable name of the call that failed.
        stop_reason: The API's stop_reason for the final attempt.
        block_types: Class names of the content blocks that were returned.
    """

    def __init__(self, purpose: str, stop_reason: str | None, block_types: list[str]) -> None:
        self.purpose = purpose
        self.stop_reason = stop_reason
        self.block_types = block_types
        super().__init__(
            f"{purpose}: the model returned no complete text (stop_reason={stop_reason!r}, "
            f"blocks={block_types}). The token budget was consumed before a full "
            f"answer was produced."
        )


@dataclass(frozen=True)
class LLMProvider:
    """One Anthropic-API endpoint plus the model and output cap to use with it.

    Attributes:
        name: Display name ("ARC" or "Claude").
        client: Configured Anthropic client for this endpoint.
        model: Model identifier sent with every request.
        max_output_tokens: Hard ceiling on ``max_tokens`` for this provider.
        user_key: True if the client authenticates with a key the user
            entered, False if with the key from .env.
    """

    name: str
    client: Anthropic
    model: str
    max_output_tokens: int
    user_key: bool = False

    @property
    def label(self) -> str:
        """Human-readable "model (provider)" string for the UI and logs."""
        key_note = ", your key" if self.user_key else ""
        return f"{self.model} ({self.name}{key_note})"


def arc_provider_from_env(user_api_key: str | None = None) -> LLMProvider | None:
    """Build the ARC provider, or None if no ARC key is available.

    Args:
        user_api_key: A key the user entered. When non-blank it is used
            INSTEAD of ARC_API_KEY -- the .env key is never consulted.

    ARC_ENDPOINT and ARC_MODEL fall back to the standard VT values, so a user
    key works even when .env has no ARC settings at all.
    """
    user_key = (user_api_key or "").strip()
    api_key = user_key or os.getenv("ARC_API_KEY", "").strip()
    if not api_key:
        return None
    endpoint = os.getenv("ARC_ENDPOINT", "").strip() or DEFAULT_ARC_ENDPOINT
    model = os.getenv("ARC_MODEL", "").strip() or DEFAULT_ARC_MODEL
    # ARC_ENDPOINT is the OpenAI-style base (".../api/v1"); the Anthropic SDK
    # appends "/v1/messages" itself, so strip the trailing "/v1".
    base_url = endpoint.rstrip("/").removesuffix("/v1")
    return LLMProvider(
        name="ARC",
        client=Anthropic(api_key=api_key, base_url=base_url, timeout=CLIENT_TIMEOUT),
        model=model,
        max_output_tokens=ARC_MAX_OUTPUT_TOKENS,
        user_key=bool(user_key),
    )


def verify_provider_key(provider: LLMProvider) -> None:
    """Check that the provider accepts its API key, with a cheap model listing.

    Raises:
        anthropic.APIError: If the key is rejected or the provider is unreachable.
    """
    provider.client.models.list(limit=1)


def is_auth_error(error: APIError) -> bool:
    """Return True if the error means the API key itself was rejected."""
    return isinstance(error, APIStatusError) and error.status_code in AUTH_STATUS_CODES


def claude_provider_from_env() -> LLMProvider | None:
    """Build the Claude provider from CLAUDE_API_KEY/CLAUDE_MODEL, or None if unset."""
    api_key = os.getenv("CLAUDE_API_KEY", "").strip()
    if not api_key:
        return None
    return LLMProvider(
        name="Claude",
        client=Anthropic(api_key=api_key, timeout=CLIENT_TIMEOUT),
        model=os.getenv("CLAUDE_MODEL", "").strip() or DEFAULT_CLAUDE_MODEL,
        max_output_tokens=CLAUDE_MAX_OUTPUT_TOKENS,
    )


def is_provider_unavailable(error: APIError) -> bool:
    """Return True if an API error means the provider itself can't serve requests.

    Connection failures and timeouts, 5xx/overloaded, rate limiting, auth
    failures, and unknown models all count. ARC's proxy reports an unknown or
    unroutable model -- and an exceeded per-key concurrency cap -- as a 400
    "Model not found", so that specific 400 counts too. Any other 4xx means the
    request itself is bad and would fail on the fallback as well.
    """
    if isinstance(error, APIConnectionError):
        return True
    if isinstance(error, APIStatusError):
        status = error.status_code
        if status >= 500 or status in UNAVAILABLE_STATUS_CODES:
            return True
        return status == 400 and "model not found" in str(error).lower()
    return False


def find_text_block(message: Message) -> TextBlock | None:
    """Return the first TextBlock in a response, or None if there is none.

    Models with extended thinking enabled prepend a ThinkingBlock (and may
    interleave other block types), so the text is never guaranteed to be at
    index 0 -- and when thinking exhausts max_tokens there is no TextBlock at
    all. Callers must handle the None case rather than assume a position.
    """
    for block in message.content:
        if isinstance(block, TextBlock):
            return block
    return None


def block_type_names(message: Message) -> list[str]:
    """Return the class names of a response's content blocks, for diagnostics."""
    return [type(block).__name__ for block in message.content]


def create_text_message(
    provider: LLMProvider,
    content: str | list[dict[str, Any]],
    max_tokens: int,
    purpose: str,
    allow_truncated: bool = True,
) -> str:
    """Send one streamed message and return its text, retrying once if truncated.

    Both failure shapes are handled: a response with no TextBlock at all
    (thinking consumed the whole budget) and a response whose text was cut off
    mid-answer (``stop_reason == "max_tokens"``). Either triggers one retry
    with a budget RETRY_BUDGET_FACTOR times larger, provided the provider's
    output cap leaves room to grow.

    Args:
        provider: Endpoint, model and output cap to use.
        content: The user message content -- a plain string, or the block list
            used for multimodal (image + text) requests.
        max_tokens: Token budget for the first attempt (clamped to the
            provider's cap).
        purpose: Short description of the call, used in logs and errors
            (e.g. "page 12 extraction").
        allow_truncated: When True, text cut off on the final attempt is
            returned as-is. When False, it raises instead -- for callers such
            as document repair, where a partial answer is worse than none.

    Returns:
        The text of the first TextBlock in the response.

    Raises:
        TruncatedResponseError: If no TextBlock is produced even after the
            retry, or the text is truncated and ``allow_truncated`` is False.
        anthropic.APIError: If the API call itself fails.
    """
    first_budget = min(max_tokens, provider.max_output_tokens)
    budgets: list[int] = [first_budget]
    retry_budget = min(first_budget * RETRY_BUDGET_FACTOR, provider.max_output_tokens)
    if retry_budget > first_budget:
        budgets.append(retry_budget)

    last_message: Message | None = None
    for attempt, budget in enumerate(budgets, start=1):
        with provider.client.messages.stream(
            model=provider.model,
            max_tokens=budget,
            messages=[{"role": "user", "content": content}],
        ) as stream:
            last_message = stream.get_final_message()
        text_block = find_text_block(last_message)

        if text_block is not None and last_message.stop_reason != "max_tokens":
            return text_block.text

        is_last_attempt = attempt == len(budgets)
        if text_block is None:
            logger.warning(
                f"{purpose} [{provider.label}]: no text in response (stop_reason="
                f"{last_message.stop_reason!r}, blocks={block_type_names(last_message)}, "
                f"max_tokens={budget})."
                + ("" if is_last_attempt else " Retrying with a larger budget.")
            )
        else:
            logger.warning(
                f"{purpose} [{provider.label}]: response hit the {budget}-token cap "
                "and was truncated."
                + ("" if is_last_attempt else " Retrying with a larger budget.")
            )
            if is_last_attempt and allow_truncated:
                logger.warning(f"{purpose}: keeping the truncated text.")
                return text_block.text

    assert last_message is not None  # loop always runs at least once
    raise TruncatedResponseError(
        purpose, last_message.stop_reason, block_type_names(last_message)
    )


FailoverCallback = Callable[[LLMProvider, LLMProvider, APIError], None]


class FailoverLLM:
    """One session's LLM: the primary provider until it fails, then the fallback.

    The switch is sticky -- once the primary is judged unavailable, every later
    call in this session goes straight to the fallback. Create a new instance
    (i.e. a new session) to try the primary again.
    """

    def __init__(
        self,
        primary: LLMProvider | None,
        fallback: LLMProvider | None,
        on_failover: FailoverCallback | None = None,
        fail_over_on_auth_error: bool = True,
    ) -> None:
        """Create a session LLM.

        Args:
            primary: Preferred provider (ARC), or None if not configured.
            fallback: Provider used once the primary is unavailable (Claude),
                or None if not configured.
            on_failover: Called once, with (primary, fallback, error), at the
                moment the session switches providers.
            fail_over_on_auth_error: When False, a rejected primary key is
                raised instead of failing over. Use this when the primary key
                is the user's own, so a bad key is never masked by the fallback.

        Raises:
            ValueError: If neither provider is configured.
        """
        if primary is None and fallback is None:
            raise ValueError(
                "No LLM provider is configured. Set ARC_ENDPOINT, ARC_API_KEY and "
                "ARC_MODEL (and/or CLAUDE_API_KEY and CLAUDE_MODEL) in .env."
            )
        self._primary = primary
        self._fallback = fallback
        self._on_failover = on_failover
        self._fail_over_on_auth_error = fail_over_on_auth_error
        self._failed_over = primary is None

    @property
    def active(self) -> LLMProvider:
        """The provider the next call will use."""
        provider = self._fallback if self._failed_over else self._primary
        assert provider is not None  # guaranteed by __init__
        return provider

    @property
    def failed_over(self) -> bool:
        """True once this session has switched from the primary to the fallback."""
        return self._failed_over and self._primary is not None

    def text(
        self,
        content: str | list[dict[str, Any]],
        max_tokens: int,
        purpose: str,
        allow_truncated: bool = True,
    ) -> str:
        """Send one message via the active provider, failing over if it is unavailable.

        Arguments and return value are as for ``create_text_message``.

        Raises:
            anthropic.APIError: If the call fails for a reason other than the
                primary being unavailable, if no fallback is configured, or if
                the fallback fails too.
            TruncatedResponseError: As for ``create_text_message``.
        """
        provider = self.active
        try:
            return create_text_message(provider, content, max_tokens, purpose, allow_truncated)
        except APIError as error:
            if self._failed_over or self._fallback is None or not is_provider_unavailable(error):
                raise
            if is_auth_error(error) and not self._fail_over_on_auth_error:
                raise
            logger.warning(
                f"{purpose}: {provider.label} is unavailable ({error}); switching this "
                f"session to {self._fallback.label}."
            )
            self._failed_over = True
            if self._on_failover is not None:
                self._on_failover(provider, self._fallback, error)
        return create_text_message(self._fallback, content, max_tokens, purpose, allow_truncated)
