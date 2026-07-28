"""Minimal MCP client for the zimage (Z-Image Turbo) server on ads2.

The server speaks streamable-HTTP MCP, which is JSON-RPC over POST with replies
delivered as Server-Sent Events. That is little enough protocol to implement
directly, so this avoids pulling in an MCP SDK for two calls.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

PROTOCOL_VERSION = "2025-06-18"

NEGATIVE_PROMPT = (
    "text, letters, words, numbers, typography, watermark, signature, logo, "
    "caption, subtitles, people, face, faces, hands, human figure, portrait, "
    "microphone, headphones, podcast studio, waveform, audio equipment"
)


@dataclass(frozen=True)
class GeneratedImage:
    """A rendered thumbnail held in memory as PNG bytes."""

    png_bytes: bytes

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.png_bytes)
        return path


class ZImageClient:
    """Calls generate_image on the zimage MCP server."""

    def __init__(self, url: str, timeout: float = 240.0) -> None:
        self._url = url
        self._timeout = timeout
        self._session = requests.Session()
        self._session_id: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: dict[str, Any]) -> requests.Response:
        response = self._session.post(
            self._url,
            data=json.dumps(payload),
            headers=self._headers(),
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response

    def _connect(self) -> None:
        """Perform the MCP initialize handshake once per client."""
        if self._session_id is not None:
            return

        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "ai-podcast-generator", "version": "1.0"},
                },
            }
        )
        session_id = response.headers.get("Mcp-Session-Id")
        if not session_id:
            raise RuntimeError(
                f"zimage did not return an Mcp-Session-Id header. "
                f"Body was: {response.text[:400]}"
            )
        self._session_id = session_id
        _result_of(response.text)

        # Required by the spec before any tool call; the server ignores the body.
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def server_info(self) -> dict[str, Any]:
        """Report GPU and model status, used for the connection check in the UI."""
        self._connect()
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "server_info", "arguments": {}},
            }
        )
        return _result_of(response.text)

    def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        num_steps: int = 8,
    ) -> GeneratedImage:
        """Render one image and return its PNG bytes."""
        self._connect()
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "generate_image",
                    "arguments": {
                        "prompt": prompt,
                        "negative_prompt": NEGATIVE_PROMPT,
                        "width": width,
                        "height": height,
                        "num_steps": num_steps,
                    },
                },
            }
        )
        return GeneratedImage(png_bytes=_decode_image(_result_of(response.text)))


def _result_of(body: str) -> dict[str, Any]:
    """Extract the JSON-RPC result from an SSE or plain-JSON response body."""
    message = _parse_sse(body)

    if "error" in message:
        error = message["error"]
        raise RuntimeError(
            f"zimage returned JSON-RPC error {error.get('code')}: "
            f"{error.get('message')}"
        )
    if "result" not in message:
        raise RuntimeError(f"zimage reply contained no result: {body[:400]}")

    result = message["result"]
    if isinstance(result, dict) and result.get("isError"):
        raise RuntimeError(f"zimage tool call failed: {_text_content(result)[:400]}")
    return result


def _parse_sse(body: str) -> dict[str, Any]:
    """Decode a streamable-HTTP reply, which may be SSE framed or bare JSON."""
    payloads: list[str] = [
        line[len("data:") :].strip()
        for line in body.splitlines()
        if line.startswith("data:")
    ]
    if not payloads:
        payloads = [body.strip()]

    for payload in reversed(payloads):
        if not payload:
            continue
        parsed = json.loads(payload)
        if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
            return parsed

    raise RuntimeError(f"Could not parse an MCP message from reply: {body[:400]}")


def _text_content(result: dict[str, Any]) -> str:
    """Concatenate the text blocks of an MCP tool result."""
    blocks = result.get("content")
    if not isinstance(blocks, list):
        return ""
    return "".join(
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _decode_image(result: dict[str, Any]) -> bytes:
    """Pull PNG bytes out of the tool result.

    zimage wraps its return value as {"result": "<data URI>"} in
    structuredContent, and mirrors it as a text block. It can also return an MCP
    image block, so all three shapes are handled.
    """
    blocks = result.get("content")
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "image":
                return base64.b64decode(block["data"])

    structured = result.get("structuredContent")
    payload = ""
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        payload = structured["result"]
    if not payload:
        payload = _text_content(result)

    payload = payload.strip()
    if not payload:
        raise RuntimeError(f"zimage returned no image payload: {str(result)[:400]}")

    if payload.startswith("data:"):
        _, _, encoded = payload.partition(",")
        return base64.b64decode(encoded)

    # A bare filesystem path means save_path was honoured server-side; that path
    # is on ads2 and not readable from here.
    if payload.startswith("/"):
        raise RuntimeError(
            f"zimage saved the image server-side at {payload!r} instead of "
            "returning data. Call generate_image without save_path."
        )
    return base64.b64decode(payload)
