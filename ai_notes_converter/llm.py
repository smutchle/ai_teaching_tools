"""Single entry point for every Claude call made by the Notes Converter.

Extended thinking is on by default on current models (Sonnet 5 and newer), and
thinking tokens are billed against ``max_tokens``. A request with a small
budget can therefore spend the entire budget thinking and come back with a
ThinkingBlock and no TextBlock at all -- ``stop_reason == "max_tokens"`` with
nothing usable in it. That is exactly what a dense, many-page document triggers
on the short classification/title calls: an easy sample provokes no thinking
and succeeds, a hard one provokes thinking and starves the answer.

``create_text_message`` is the fix: it gives every call room for thinking plus
its answer, and when a response still comes back truncated it retries once with
a much larger budget before giving up loudly.

Every call is made with ``client.messages.stream``. The SDK refuses a
non-streaming request whose ``max_tokens`` implies a generation that could run
past its 10-minute HTTP timeout -- it raises "Streaming is required for
operations that may take longer than 10 minutes" client-side, before any
request is sent, and that error is not an ``APIError`` so callers guarding on
``APIError`` never see it coming. Streaming removes the ceiling entirely, so
the large budgets the repair and extraction calls need are safe.
"""

from __future__ import annotations

import logging
from typing import Any

from anthropic import Anthropic
from anthropic.types import Message, TextBlock

logger = logging.getLogger(__name__)

# Ceiling for the retry budget. Requests stream, so this is not bounded by the
# SDK's non-streaming timeout -- none of these calls need more than this to
# finish an answer.
MAX_RETRY_TOKENS: int = 64000

# Multiplier applied to the caller's budget when a response comes back truncated.
RETRY_BUDGET_FACTOR: int = 4


class TruncatedResponseError(RuntimeError):
    """Raised when a response carries no TextBlock even after a retry.

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
            f"{purpose}: the model returned no text (stop_reason={stop_reason!r}, "
            f"blocks={block_types}). The token budget was consumed before any "
            f"answer was produced -- raise max_tokens for this call."
        )


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
    client: Anthropic,
    model: str,
    content: str | list[dict[str, Any]],
    max_tokens: int,
    purpose: str,
) -> str:
    """Send one streamed message and return its text, retrying once if truncated.

    Both failure shapes are handled: a response with no TextBlock at all
    (thinking consumed the whole budget) and a response whose text was cut off
    mid-answer (``stop_reason == "max_tokens"``). Either triggers one retry
    with a budget RETRY_BUDGET_FACTOR times larger, capped at MAX_RETRY_TOKENS.

    Args:
        client: Configured Anthropic client.
        model: Model identifier.
        content: The user message content -- a plain string, or the block list
            used for multimodal (image + text) requests.
        max_tokens: Token budget for the first attempt.
        purpose: Short description of the call, used in logs and errors
            (e.g. "page 12 extraction").

    Returns:
        The text of the first TextBlock in the response.

    Raises:
        TruncatedResponseError: If no TextBlock is produced even after the retry.
        anthropic.APIError: If the API call itself fails.
    """
    budgets: list[int] = [max_tokens]
    retry_budget = min(max_tokens * RETRY_BUDGET_FACTOR, MAX_RETRY_TOKENS)
    if retry_budget > max_tokens:
        budgets.append(retry_budget)

    last_message: Message | None = None
    for attempt, budget in enumerate(budgets, start=1):
        with client.messages.stream(
            model=model,
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
                f"{purpose}: no text in response (stop_reason="
                f"{last_message.stop_reason!r}, blocks={block_type_names(last_message)}, "
                f"max_tokens={budget})."
                + ("" if is_last_attempt else " Retrying with a larger budget.")
            )
        else:
            logger.warning(
                f"{purpose}: response hit the {budget}-token cap and was truncated."
                + (
                    " Keeping the truncated text."
                    if is_last_attempt
                    else " Retrying with a larger budget."
                )
            )
            if is_last_attempt:
                return text_block.text

    assert last_message is not None  # loop always runs at least once
    raise TruncatedResponseError(
        purpose, last_message.stop_reason, block_type_names(last_message)
    )
