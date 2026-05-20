"""Append-only event log for agent communications.

Each LLM call by an agent produces two events: invocation_started before the
Claude API call and invocation_completed (or invocation_failed) after. Events
carry truncated input/output summaries suitable for a chat-style UI; the full
prompts and responses go to a sidecar file keyed by call_id, so the events
file stays small and skimmable while the audit trail is preserved.

The Moderator (when built) writes its own routing/policy events into the same
log so the chat UI shows agent invocations and orchestration decisions
interleaved.

Concurrency: write path uses filelock so the parallel critic phase can append
safely from multiple agents. Read path also takes the lock to avoid partial
reads of a line being appended.
"""

import json
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from filelock import FileLock
from pydantic import BaseModel, Field


class EventKind(str, Enum):
    INVOCATION_STARTED = "invocation_started"
    INVOCATION_COMPLETED = "invocation_completed"
    INVOCATION_FAILED = "invocation_failed"
    ROUTING_DECISION = "routing_decision"
    CHECKPOINT_REACHED = "checkpoint_reached"
    POLICY_APPLIED = "policy_applied"
    PROVENANCE_APPENDED = "provenance_appended"


class AgentEvent(BaseModel):
    timestamp: datetime
    epoch: int = 0
    agent: str
    kind: EventKind
    call_id: str | None = None
    verb: str | None = None
    target: str | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    duration_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)


def summarize(s: str, max_len: int = 240) -> str:
    """Collapse whitespace and truncate for chat-UI display."""
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


class EventLog:
    """Append-only JSONL event log with sidecar full-content storage.

    Layout under base_dir:
      events.jsonl           — one AgentEvent per line, append-only
      calls/<call_id>.json   — full system prompt, user prompt, raw response
      .events.lock           — filelock for concurrent writers
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        base_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = base_dir / "events.jsonl"
        self._calls_dir = base_dir / "calls"
        self._calls_dir.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(base_dir / ".events.lock"))

    @property
    def events_path(self) -> Path:
        return self._events_path

    @property
    def calls_dir(self) -> Path:
        return self._calls_dir

    def append(self, event: AgentEvent) -> None:
        line = event.model_dump_json() + "\n"
        with self._lock:
            with self._events_path.open("a", encoding="utf-8") as f:
                f.write(line)

    def write_call_io(
        self,
        call_id: str,
        *,
        system: str,
        user_prompt: str,
        response: dict[str, Any],
    ) -> None:
        """Persist the full I/O for one Claude call, keyed by call_id.

        Not atomic — a crash mid-write produces a partial sidecar. Acceptable
        for v0; production should write-to-tmp-then-rename.
        """
        path = self._calls_dir / f"{call_id}.json"
        payload = {
            "call_id": call_id,
            "system": system,
            "user_prompt": user_prompt,
            "response": response,
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def read_all(self) -> list[AgentEvent]:
        if not self._events_path.exists():
            return []
        with self._lock:
            text = self._events_path.read_text(encoding="utf-8")
        return [
            AgentEvent.model_validate_json(line)
            for line in text.splitlines()
            if line.strip()
        ]

    def read_call_io(self, call_id: str) -> dict[str, Any]:
        path = self._calls_dir / f"{call_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def to_markdown(self) -> str:
        """Render the event log as a chat-style transcript."""
        events = self.read_all()
        if not events:
            return "# Agent transcript\n\n_(no events recorded)_\n"

        lines: list[str] = ["# Agent transcript", ""]
        current_epoch: int | None = None

        for ev in events:
            if ev.epoch != current_epoch:
                lines.append(f"\n## Epoch {ev.epoch}\n")
                current_epoch = ev.epoch

            ts = ev.timestamp.strftime("%H:%M:%S")
            head = f"**[{ts}] {ev.agent}**"
            if ev.verb:
                head += f" `{ev.verb}`"
            if ev.target:
                head += f" → `{ev.target}`"
            head += f" — *{ev.kind.value}*"
            lines.append(head)

            if ev.kind is EventKind.INVOCATION_STARTED and ev.input_summary:
                lines.append("")
                lines.append(f"> {ev.input_summary}")

            if ev.kind is EventKind.INVOCATION_COMPLETED:
                if ev.output_summary:
                    lines.append("")
                    lines.append(f"> {ev.output_summary}")
                meta_parts: list[str] = []
                if ev.duration_ms is not None:
                    meta_parts.append(f"{ev.duration_ms / 1000:.1f}s")
                if ev.tokens_in is not None:
                    meta_parts.append(f"in={ev.tokens_in} tok")
                if ev.tokens_out is not None:
                    meta_parts.append(f"out={ev.tokens_out} tok")
                if ev.call_id:
                    meta_parts.append(f"call_id=`{ev.call_id}`")
                if meta_parts:
                    lines.append("")
                    lines.append(f"_{' · '.join(meta_parts)}_")

            if ev.kind is EventKind.INVOCATION_FAILED:
                lines.append("")
                lines.append(f"**FAILED**: {ev.error or '(no detail)'}")

            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)
