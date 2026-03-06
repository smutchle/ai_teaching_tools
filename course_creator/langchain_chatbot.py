"""
Unified LLM chatbot using LangChain.
Replaces AnthropicChatBot, OllamaChatBot, OpenAIChatBot, GoogleChatBot.
Exposes the same interface: complete(), completeAsJSON(), extract_markdown_content().
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage


def _build_llm(provider: str, model: str, api_key: Optional[str], endpoint: Optional[str]):
    """Instantiate the appropriate LangChain chat model."""
    if provider == "Anthropic":
        from langchain_anthropic import ChatAnthropic
        kwargs = {"model": model, "max_tokens": 16000, "temperature": 0.7}
        if api_key:
            kwargs["api_key"] = api_key
        return ChatAnthropic(**kwargs)

    if provider == "OpenAI":
        from langchain_openai import ChatOpenAI
        kwargs = {"model": model, "temperature": 0.7}
        if api_key:
            kwargs["api_key"] = api_key
        return ChatOpenAI(**kwargs)

    if provider == "Google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        kwargs = {"model": model, "temperature": 0.7}
        if api_key:
            kwargs["google_api_key"] = api_key
        return ChatGoogleGenerativeAI(**kwargs)

    if provider == "Ollama":
        from langchain_ollama import ChatOllama
        kwargs = {"model": model, "temperature": 0.0}
        if endpoint:
            base = endpoint.rstrip("/")
            kwargs["base_url"] = base
        return ChatOllama(**kwargs)

    raise ValueError(f"Unknown provider: {provider}")


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
                return response.content.strip()
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
