"""OpenAI-compatible LLM client for the ARC endpoint.

Two model roles, both configured via .env:
  - OPENAI_MODEL         -> text reasoning model (grading)
  - OPENAI_VISION_MODEL  -> vision model (OCR only)

The client is intentionally resilient: retries on transient errors and a
tolerant JSON extractor so a stray token from the model never crashes a run.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import threading
import time
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

# Load .env from the app directory (next to this package).
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_APP_DIR, ".env"))


# The ARC proxy's "vision" alias currently routes to gpt-oss-120b, a text-only
# model that silently DROPS image content instead of erroring — pages come back
# as "I'm unable to view the image". Kimi-K3 is the multimodal model there.
DEFAULT_VISION_MODEL = "Kimi-K3"

# The ARC proxy caps how many requests one user may have in flight per model and
# rejects the excess with HTTP 400 - the same status it uses for a bad model
# name. Exceeding the cap therefore looks exactly like a misconfiguration, and
# every rejected page is a page that never gets transcribed. Keep in-flight
# requests at or below this and the rejections stop happening at all.
DEFAULT_MAX_INFLIGHT = 3

# Statuses that never succeed on retry. 400 is deliberately absent: the proxy
# overloads it for both permanent and transient conditions, so it is classified
# by message below.
PERMANENT_STATUS = {401, 403, 404, 405, 422}

# A 400 mentioning any of these is the proxy throttling us - retry it.
TRANSIENT_400 = ("concurrent", "session limit", "rate limit", "ratelimit",
                 "too many", "capacity", "busy", "overloaded", "try again",
                 "timeout", "temporarily")

# A 400 mentioning any of these is a configuration error - fail immediately.
PERMANENT_400 = ("model not found", "does not exist", "unknown model",
                 "invalid model", "no such model", "unsupported model",
                 "invalid api key", "incorrect api key")


def _is_permanent(status: int | None, message: str) -> bool:
    """Whether an error is a misconfiguration rather than proxy turbulence.

    When a 400 is ambiguous we treat it as transient: retrying a genuinely
    permanent error costs a few seconds, while misreading a throttle as
    permanent aborts an entire exam mid-run.
    """
    if status in PERMANENT_STATUS:
        return True
    if status == 400:
        low = message.lower()
        if any(m in low for m in TRANSIENT_400):
            return False
        return any(m in low for m in PERMANENT_400)
    return False


# The proxy's concurrency cap is per API key, not per browser session, so the
# gate that enforces it has to be per API key too - process-wide, shared by
# every session using that key. A semaphore owned by each LLMClient would let
# five simultaneous instructors put 5 x max_inflight requests in flight and
# collect 400s that look like a broken model name.
_GATES: dict[str, "_Gate"] = {}
_GATES_LOCK = threading.Lock()


class _Gate:
    """A semaphore plus enough bookkeeping to show the queue in the UI."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._sem = threading.Semaphore(limit)
        self._lock = threading.Lock()
        self.in_use = 0
        self.waiting = 0

    def __enter__(self) -> "_Gate":
        with self._lock:
            self.waiting += 1
        self._sem.acquire()
        with self._lock:
            self.waiting -= 1
            self.in_use += 1
        return self

    def __exit__(self, *exc) -> None:
        with self._lock:
            self.in_use -= 1
        self._sem.release()

    def stats(self) -> tuple[int, int, int]:
        with self._lock:
            return self.in_use, self.waiting, self.limit


def _gate_for(endpoint: str, api_key: str, limit: int) -> _Gate:
    """The one gate shared by every session using this endpoint and key.

    Keyed by a digest rather than the key itself so a stray repr of this dict
    cannot leak credentials.
    """
    ident = hashlib.sha256(f"{endpoint}\n{api_key}".encode()).hexdigest()
    with _GATES_LOCK:
        gate = _GATES.get(ident)
        if gate is None:
            gate = _Gate(limit)
            _GATES[ident] = gate
        return gate


class PermanentLLMError(RuntimeError):
    """A misconfiguration that retrying cannot fix (bad model name or API key)."""


class ConcurrencyLimitError(RuntimeError):
    """The proxy refused because too many of our requests are already in flight."""


class LLMClient:
    """Thin wrapper around the OpenAI SDK pointed at the ARC proxy."""

    def __init__(self, api_key_override: str | None = None):
        endpoint = os.getenv("OPENAI_ENDPOINT")
        api_key = (api_key_override or "").strip() or os.getenv("OPENAI_APIKEY")
        if not endpoint or not api_key:
            raise RuntimeError(
                "Missing OPENAI_ENDPOINT / OPENAI_APIKEY. Set them in .env or the "
                "API key panel in the sidebar."
            )
        self.text_model = os.getenv("OPENAI_MODEL", "thinkinglatest")
        self.vision_model = os.getenv("OPENAI_VISION_MODEL", DEFAULT_VISION_MODEL)
        try:
            self.max_inflight = max(1, int(os.getenv("OPENAI_MAX_INFLIGHT",
                                                     DEFAULT_MAX_INFLIGHT)))
        except ValueError:
            self.max_inflight = DEFAULT_MAX_INFLIGHT
        # Every request goes through this gate, so no amount of caller-side
        # concurrency - from this session or any other one sharing the key -
        # can push us past what the proxy will accept.
        self._gate = _gate_for(endpoint, api_key, self.max_inflight)
        self.client = OpenAI(base_url=endpoint, api_key=api_key, timeout=600.0)

    def gate_stats(self) -> tuple[int, int, int]:
        """(in flight, queued, limit) across every session sharing this key."""
        return self._gate.stats()

    # ------------------------------------------------------------------ core
    def _chat(self, model: str, messages: list[dict], *, max_tokens: int = 8000,
              temperature: float = 0.0, retries: int = 6) -> str:
        # The ARC proxy caps buffered (non-streaming) responses and treats them
        # as all-or-nothing on timeout, so we always stream and accumulate.
        last_err: Exception | None = None
        for attempt in range(retries):
            throttled = False
            try:
                with self._gate:
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
                status = getattr(e, "status_code", None) or getattr(
                    getattr(e, "response", None), "status_code", None)
                if _is_permanent(status, str(e)):
                    # Surfacing this immediately is the difference between one
                    # clear error and a whole exam quietly transcribing to nothing.
                    raise PermanentLLMError(
                        f"model '{model}' rejected the request ({status}): {e}. "
                        "Check OPENAI_MODEL / OPENAI_VISION_MODEL in .env."
                    ) from e
                throttled = status == 400 or status == 429
                last_err = e
            if attempt < retries - 1:
                # Exponential backoff with jitter. Without jitter, workers
                # throttled at the same moment retry in lockstep and collide
                # again. Throttling gets a longer floor than a generic blip.
                base = min(60.0, (4 if throttled else 2) ** (attempt + 1))
                time.sleep(base * (0.5 + random.random()))
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

    def check_vision(self) -> None:
        """Verify the configured vision model exists and accepts an image.

        Called once before a run rather than discovering the problem 100 pages
        and several hundred failed requests later. Raises PermanentLLMError with
        an actionable message; transient trouble is left to the run itself.
        """
        import io as _io
        import struct
        import zlib

        def _tiny_png() -> bytes:
            # 8x8 white PNG, built inline so the check needs no page to render.
            raw = b"".join(b"\x00" + b"\xff" * 24 for _ in range(8))
            def chunk(tag: bytes, data: bytes) -> bytes:
                return (struct.pack(">I", len(data)) + tag + data
                        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
            buf = _io.BytesIO()
            buf.write(b"\x89PNG\r\n\x1a\n")
            buf.write(chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)))
            buf.write(chunk(b"IDAT", zlib.compress(raw)))
            buf.write(chunk(b"IEND", b""))
            return buf.getvalue()

        try:
            self._chat(
                self.vision_model,
                [{"role": "user", "content": [
                    {"type": "text", "text": "Reply with the single word OK."},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/png;base64,"
                               + base64.b64encode(_tiny_png()).decode()}},
                ]}],
                max_tokens=64, retries=1,
            )
        except PermanentLLMError as e:
            raise PermanentLLMError(
                f"The vision model '{self.vision_model}' is not usable: {e} "
                "Set OPENAI_VISION_MODEL in .env to a multimodal model "
                f"(e.g. {DEFAULT_VISION_MODEL})."
            ) from e
        except Exception:  # noqa: BLE001
            # Anything else - a slow proxy, an empty reply to a blank test image -
            # is not evidence of a broken model. Only a hard rejection is, and
            # blocking a good run on a flaky probe would be its own outage.
            return


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
