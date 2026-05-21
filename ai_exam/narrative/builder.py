"""Hybrid narrative builder: template + LLM polish + Quarto render.

Pipeline:
  1. `templater.build_structured_draft(run_dir)` walks events + snapshots
     and emits a deterministic markdown timeline.
  2. `NarratorAgent.polish()` rewrites the timeline as flowing prose
     (Haiku via the standard provider stack).
  3. Quarto renders the polished markdown to HTML for in-tab viewing.

Output paths under `<run_dir>/exam_bundle/`:
  narrative.md       — polished markdown
  narrative.qmd      — Quarto source with frontmatter
  narrative.html     — rendered HTML
  narrative_draft.md — the structured pre-polish draft (kept for audit)

The bundle directory is created if missing — useful when called on a run
whose Phase 4 didn't fire (e.g. a run that crashed mid-Phase-3).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import config
from agents.narrator import NarratorAgent
from events import EventLog
from narrative.templater import build_structured_draft


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PERSONA_DIR = _PROJECT_ROOT / "persona"


_QMD_FRONTMATTER = """\
---
title: "{title}"
subtitle: "Multi-agent run narrative"
date: "{date}"
lang: en
format:
  html:
    embed-resources: true
    toc: true
    toc-depth: 2
---

"""


@dataclass
class NarrativeResult:
    draft_path: Path
    md_path: Path
    qmd_path: Path
    html_path: Path | None = None
    render_error: str | None = None
    failures: dict[str, str] = field(default_factory=dict)


def build_narrative(run_dir: Path) -> NarrativeResult:
    """End-to-end: template → polish → render. Writes everything into
    `<run_dir>/exam_bundle/`."""
    bundle_dir = run_dir / "exam_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # 1. Structured draft (deterministic).
    structured = build_structured_draft(run_dir)
    draft_path = bundle_dir / "narrative_draft.md"
    draft_path.write_text(structured, encoding="utf-8")

    # 2. LLM polish via NarratorAgent. We construct it directly here —
    # no Moderator involved — using the standard config.make_provider
    # registry so per-tier overrides apply. Event log lives under the
    # run's events/ dir so the narrative call shows up in the Job Monitor.
    event_log = EventLog(run_dir / "events")
    narrator = NarratorAgent(
        persona_dir=_PERSONA_DIR,
        provider=config.make_provider("narrator"),
        event_log=event_log,
        max_tokens=8192,  # narratives can be long
    )
    polished = narrator.polish(structured)
    md_path = bundle_dir / "narrative.md"
    md_path.write_text(polished, encoding="utf-8")

    # 3. Wrap in a Quarto .qmd and render to HTML.
    qmd_path = bundle_dir / "narrative.qmd"
    qmd_path.write_text(
        _QMD_FRONTMATTER.format(
            title=f"Narrative — {run_dir.name}",
            date=datetime.now().strftime("%Y-%m-%d"),
        ) + polished,
        encoding="utf-8",
    )

    result = NarrativeResult(
        draft_path=draft_path, md_path=md_path, qmd_path=qmd_path,
    )
    try:
        subprocess.run(
            ["quarto", "render", str(qmd_path), "--to", "html"],
            check=True, capture_output=True, text=True,
            cwd=str(qmd_path.parent),
        )
        html_path = qmd_path.with_suffix(".html")
        if html_path.exists():
            result.html_path = html_path
    except subprocess.CalledProcessError as exc:
        result.render_error = (exc.stderr or exc.stdout or "").strip()
    except FileNotFoundError:
        result.render_error = "quarto not on PATH — install or fix PATH"
    return result
