"""OpenAI-compatible LLM client for the ARC endpoint.

Two model roles, both configured via .env:
  - OPENAI_MODEL         -> text reasoning model (grading)
  - OPENAI_VISION_MODEL  -> vision model (OCR only)

The client is intentionally resilient: retries on transient errors and a
tolerant JSON extractor so a stray token from the model never crashes a run.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

# Load .env from the app directory (next to this package).
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_APP_DIR, ".env"))


class LLMClient:
    """Thin wrapper around the OpenAI SDK pointed at the ARC proxy."""

    def __init__(self, api_key_override: str | None = None):
        endpoint = os.getenv("OPENAI_ENDPOINT")
        api_key = (api_key_override or "").strip() or os.getenv("OPENAI_APIKEY")
        if not endpoint or not api_key:
            raise RuntimeError(
                "Missing OPENAI_ENDPOINT / OPENAI_APIKEY. Set them in .env or the "
                "API key override in the Config tab."
            )
        self.text_model = os.getenv("OPENAI_MODEL", "thinkinglatest")
        self.vision_model = os.getenv("OPENAI_VISION_MODEL", "vision")
        self.client = OpenAI(base_url=endpoint, api_key=api_key, timeout=600.0)

    # ------------------------------------------------------------------ core
    def _chat(self, model: str, messages: list[dict], *, max_tokens: int = 8000,
              temperature: float = 0.0, retries: int = 4) -> str:
        # The ARC proxy caps buffered (non-streaming) responses and treats them
        # as all-or-nothing on timeout, so we always stream and accumulate.
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                stream = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                )
                parts: list[str] = []
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        parts.append(delta.content)
                content = "".join(parts).strip()
                if content:
                    return content
                last_err = RuntimeError("empty response")
            except Exception as e:  # noqa: BLE001 - deliberately broad; we retry
                last_err = e
            # exponential backoff: 2s, 4s, 8s, 16s
            time.sleep(2 ** (attempt + 1))
        raise RuntimeError(f"LLM call failed after {retries} attempts: {last_err}")

    # ------------------------------------------------------------------ text
    def complete_text(self, system: str, user: str, *, max_tokens: int = 8000,
                      temperature: float = 0.0) -> str:
        return self._chat(
            self.text_model,
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            max_tokens=max_tokens, temperature=temperature,
        )

    def complete_json(self, system: str, user: str, *, max_tokens: int = 8000) -> Any:
        """Text completion whose result is parsed as JSON (tolerantly)."""
        raw = self.complete_text(system, user, max_tokens=max_tokens)
        return extract_json(raw)

    # ---------------------------------------------------------------- vision
    def vision(self, prompt: str, images_png: list[bytes], *,
               max_tokens: int = 8000, temperature: float = 0.0) -> str:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for png in images_png:
            b64 = base64.b64encode(png).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
        return self._chat(
            self.vision_model,
            [{"role": "user", "content": content}],
            max_tokens=max_tokens, temperature=temperature,
        )

    def vision_json(self, prompt: str, images_png: list[bytes], *,
                    max_tokens: int = 8000) -> Any:
        raw = self.vision(prompt, images_png, max_tokens=max_tokens)
        return extract_json(raw)


def extract_json(raw: str) -> Any:
    """Best-effort extraction of a JSON object/array from model output.

    Handles ```json fences, leading prose, and trailing commentary.
    Returns None if nothing parseable is found.
    """
    if not raw:
        return None
    text = raw.strip()

    # Strip code fences.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # Fast path.
    try:
        return json.loads(text)
    except Exception:
        pass

    # Find the first balanced { } or [ ] block.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break
    return None
