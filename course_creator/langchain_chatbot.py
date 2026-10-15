"""
LLM chatbot for VT ARC, using LangChain's Anthropic chat model.
Replaces AnthropicChatBot, OllamaChatBot, OpenAIChatBot, GoogleChatBot.
Exposes the same interface: complete(), completeAsJSON(), extract_markdown_content().

ARC serves the Anthropic Messages API. Two details matter:
- The Anthropic SDK appends "/v1/messages" to the base URL itself, so the
  trailing "/v1" of ANTHROPIC_ENDPOINT is stripped.
- ARC rejects non-streaming requests above 8000 output tokens (413), so every
  request streams; streamed responses have no such cap.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from anthropic import AuthenticationError, PermissionDeniedError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

# Output ceiling per request. Streaming lifts ARC's 8000-token buffered cap, so
# this only needs to cover the longest generation (a full notebook) plus any
# reasoning tokens, which count against the same budget.
MAX_OUTPUT_TOKENS: int = 64000


def _build_llm(provider: str, model: str, api_key: Optional[str], endpoint: Optional[str]):
    """Instantiate the LangChain chat model for VT ARC."""
    if provider != "ARC":
        raise ValueError(f"Unknown provider: {provider}")
    if not endpoint:
        raise ValueError("No VT ARC endpoint configured (ANTHROPIC_ENDPOINT).")
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(
        model=model,
        api_key=api_key,
        base_url=endpoint.rstrip("/").removesuffix("/v1"),
        max_tokens=MAX_OUTPUT_TOKENS,
        streaming=True,
    )


def _message_text(message: AIMessage) -> str:
    """Return a response's text, skipping any thinking blocks.

    Reasoning models can return a list of content blocks (thinking + text)
    rather than a plain string.
    """
    if isinstance(message.content, str):
        return message.content
    return "".join(
        block.get("text", "")
        for block in message.content
        if isinstance(block, dict) and block.get("type") == "text"
    )


class LangChainChatBot:
    """
    Drop-in replacement for the custom chatbot classes.
    Supports optional RAG context injection via a system message.
    """

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        num_retries: int = 3,
    ):
        self.provider = provider
        self.model = model
        self.num_retries = num_retries
        self._llm = _build_llm(provider, model, api_key, endpoint)

    # ------------------------------------------------------------------ core

    def _invoke(self, prompt: str, context: Optional[str] = None) -> str:
        messages = []
        if context:
            messages.append(SystemMessage(content=context))
        messages.append(HumanMessage(content=prompt))

        last_exc = None
        for attempt in range(self.num_retries + 1):
            try:
                response = self._llm.invoke(messages)
                return _message_text(response).strip()
            except (AuthenticationError, PermissionDeniedError):
                # A rejected API key won't fix itself; report it immediately.
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < self.num_retries:
                    time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"LLM call failed after {self.num_retries} retries: {last_exc}")

    # ------------------------------------------------------------------ public interface

    def complete(self, prompt: str, context: Optional[str] = None) -> str:
        return self._invoke(prompt, context)

    def completeAsJSON(self, prompt: str, context: Optional[str] = None) -> str:
        response = self._invoke(prompt, context)
        return self.extract_markdown_content(response, "json")

    def extract_markdown_content(self, text: str, type: str = "json") -> str:
        start_marker = f"```{type}"
        start_idx = text.find(start_marker)
        end_idx = text.rfind("```")
        if start_idx >= 0 and end_idx > start_idx:
            start_idx += len(start_marker)
            if text[start_idx] == "\n":
                start_idx += 1
            return text[start_idx:end_idx].strip()
        # Fallback: strip any generic code fences
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", text.strip())
        cleaned = re.sub(r"\n?```$", "", cleaned)
        return cleaned.strip()
