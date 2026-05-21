"""Read events.jsonl and the per-call I/O sidecars from a run directory.

The Streamlit transcript view reads via these helpers rather than touching
the filesystem directly so we can swap in SQLite-backed loaders later without
the UI noticing. Today everything is JSON-on-disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from events import AgentEvent


def load_events(events_path: Path) -> list[AgentEvent]:
    """Read every line of events.jsonl and parse to AgentEvent."""
    if not events_path.exists():
        return []
    events: list[AgentEvent] = []
    with events_path.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            events.append(AgentEvent.model_validate(json.loads(line)))
    return events


def load_sidecar(calls_dir: Path, call_id: str) -> dict[str, Any] | None:
    """Read the full system+user+response sidecar for a call_id, or None."""
    path = calls_dir / f"{call_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(runs_dir: Path) -> list[Path]:
    """List run subdirectories sorted newest-first by name (timestamp-sortable)."""
    if not runs_dir.exists():
        return []
    return sorted(
        (p for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("run_")),
        key=lambda p: p.name,
        reverse=True,
    )


def run_summary(run_dir: Path) -> dict[str, Any]:
    """Summary stats for a run, used in the sidebar run picker label."""
    events_path = run_dir / "events" / "events.jsonl"
    n_events = 0
    if events_path.exists():
        with events_path.open(encoding="utf-8") as f:
            n_events = sum(1 for _ in f)
    bundle = run_dir / "exam_bundle"
    final = run_dir / "phase_3_final_draft.json"
    return {
        "name": run_dir.name,
        "events": n_events,
        "has_bundle": bundle.exists(),
        "has_final": final.exists(),
    }
