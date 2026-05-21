"""Walk a run's events + snapshots and emit a structured markdown draft.

This is the deterministic half of the hybrid narrative pipeline. The output
is a phase-by-phase markdown timeline — factually complete but mechanical.
The NarratorAgent then polishes it into flowing prose.

Reads:
  events/events.jsonl  — for timings, agent activity, failures
  phase_1_themes.json
  phase_1_blueprint.json
  phase_2_items.json
  phase_3_epoch_*.json
  phase_3_final_draft.json
  phase_4_audit.json
  phase_4_variants.json   (optional)

The templater is forgiving: if a snapshot is missing (e.g. Phase 3 was
skipped, or Phase 4 didn't run), that phase's section is short or omitted.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_events(events_path: Path) -> list[dict[str, Any]]:
    if not events_path.exists():
        return []
    out: list[dict[str, Any]] = []
    with events_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _parse_ts(s: str) -> datetime:
    # events.jsonl uses ISO 8601 with trailing 'Z' for UTC.
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def _phase_window(events: list[dict], phase: str) -> tuple[datetime, datetime] | None:
    """Return the (start, end) timestamps for a named phase."""
    start = None
    end = None
    phases = ["phase_0", "phase_1", "phase_2", "phase_3", "phase_4"]
    for ev in events:
        if ev.get("kind") != "routing_decision":
            continue
        ex = ev.get("extras", {}) or {}
        cur = ex.get("phase")
        if cur is None:
            continue
        ts = _parse_ts(ev["timestamp"])
        if cur == phase and start is None:
            start = ts
        if start is not None and cur != phase and end is None:
            try:
                if phases.index(cur) > phases.index(phase):
                    end = ts
                    break
            except ValueError:
                continue
    if start is None:
        return None
    if end is None and events:
        end = _parse_ts(events[-1]["timestamp"])
    return (start, end) if end else None


def build_structured_draft(run_dir: Path) -> str:
    """Top-level: produce the structured markdown timeline for `run_dir`."""
    events = _load_events(run_dir / "events" / "events.jsonl")
    themes = _load(run_dir / "phase_1_themes.json")
    blueprint = _load(run_dir / "phase_1_blueprint.json")
    p2_items = _load(run_dir / "phase_2_items.json")
    final_draft = _load(run_dir / "phase_3_final_draft.json")
    audit = _load(run_dir / "phase_4_audit.json")
    variants = _load(run_dir / "phase_4_variants.json")
    epoch_snapshots = sorted(run_dir.glob("phase_3_epoch_*.json"))

    parts: list[str] = []
    parts.append(f"# Run timeline — {run_dir.name}")
    parts.append("")
    if events:
        first = _parse_ts(events[0]["timestamp"])
        last = _parse_ts(events[-1]["timestamp"])
        total = (last - first).total_seconds()
        parts.append(
            f"_Total wall-clock: {_format_duration(total)} · "
            f"{len(events)} events · "
            f"{sum(1 for e in events if e.get('kind') == 'invocation_completed')} agent calls completed · "
            f"{sum(1 for e in events if e.get('kind') == 'invocation_failed')} failed_"
        )
        parts.append("")

    parts.extend(_phase_0_section(events))
    parts.extend(_phase_1_section(events, themes, blueprint))
    parts.extend(_phase_2_section(events, p2_items))
    parts.extend(_phase_3_section(events, epoch_snapshots, final_draft))
    parts.extend(_phase_4_section(events, audit, variants))

    return "\n".join(parts)


# ---- per-phase sections -------------------------------------------------


def _phase_0_section(events: list[dict]) -> list[str]:
    out = ["## Phase 0 — Intake", ""]
    # No invocations in Phase 0; just look for the routing event.
    for ev in events:
        if (ev.get("kind") == "routing_decision"
                and ev.get("extras", {}).get("phase") == "phase_0"):
            msg = ev["extras"].get("message", "Intake")
            out.append(f"- {msg}")
            break
    out.append("")
    return out


def _phase_1_section(
    events: list[dict],
    themes: dict | None,
    blueprint: dict | None,
) -> list[str]:
    out = ["## Phase 1 — Themes and Blueprint", ""]
    win = _phase_window(events, "phase_1")
    if win is not None:
        out.append(f"- duration: {_format_duration((win[1] - win[0]).total_seconds())}")
    if themes:
        ts = themes.get("themes", [])
        out.append(f"- {len(ts)} themes extracted by the SME")
        # Show the top 3 by rank
        ranked = sorted(ts, key=lambda t: t.get("rank", 99))[:3]
        for t in ranked:
            out.append(f"  - rank {t.get('rank')}: {t.get('text', '')[:140]}")
    if blueprint:
        cells = blueprint.get("cells", [])
        total_pts = sum(c.get("target_points", 0) for c in cells)
        total_items = sum(c.get("target_item_count", 0) for c in cells)
        out.append(
            f"- blueprint: {len(cells)} cells totaling "
            f"{total_pts} points and {total_items} items"
        )
        bloom_counts = Counter(c.get("bloom_level", "?") for c in cells)
        out.append("  - Bloom distribution across cells: "
                   + ", ".join(f"{b}={n}" for b, n in bloom_counts.most_common()))
        cov = blueprint.get("coverage_check", {})
        warnings = cov.get("warnings", []) if isinstance(cov, dict) else []
        if warnings:
            out.append(f"  - blueprint coverage warnings: {len(warnings)}")
            for w in warnings[:3]:
                out.append(f"    - {str(w)[:200]}")
    # Checkpoint
    for ev in events:
        if ev.get("kind") == "checkpoint_reached" and ev["extras"].get("checkpoint") == 1:
            out.append(f"- Checkpoint 1: {ev['extras'].get('message', 'reached')}")
            break
    out.append("")
    return out


def _phase_2_section(events: list[dict], p2_items: dict | None) -> list[str]:
    out = ["## Phase 2 — Per-cell item generation", ""]
    win = _phase_window(events, "phase_2")
    if win is not None:
        out.append(f"- duration: {_format_duration((win[1] - win[0]).total_seconds())}")
    if p2_items:
        accepted = p2_items.get("accepted", [])
        rejected_recs = p2_items.get("rejected", [])
        out.append(f"- {len(accepted)} items accepted, {len(rejected_recs)} rejected during proposal")

        # Bucket rejections by reason prefix
        reasons = Counter()
        for r in rejected_recs:
            reason = r.get("reason", "")
            if "loa_misaligned" in reason or "bloom" in reason.lower():
                reasons["LOA Bloom/CLO mismatch"] += 1
            elif "grounding_failed" in reason:
                reasons["Grounding: cited chunks don't support answer"] += 1
            elif "grounding_missing_chunks" in reason:
                reasons["Grounding: missing chunk_id"] += 1
            else:
                reasons["other"] += 1
        if reasons:
            out.append("- rejection causes:")
            for cat, n in reasons.most_common():
                out.append(f"  - {n}: {cat}")

        # LOA realignment events (look in events.jsonl for realignment routing decisions)
        realignment_count = sum(
            1 for ev in events
            if ev.get("kind") == "invocation_completed"
            and ev.get("agent") == "learning_outcomes_alignment"
            and ev.get("verb") == "suggest_realignment"
        )
        if realignment_count:
            out.append(f"- LOA realignment fallback fired {realignment_count} time(s) (recovering Bloom-mismatch items)")

        # Per-item Bloom + difficulty distribution
        if accepted:
            bloom = Counter(it.get("bloom_level") for it in accepted)
            diff = Counter(it.get("difficulty_est") for it in accepted)
            out.append("- accepted items by Bloom: " + ", ".join(f"{b}={n}" for b, n in bloom.most_common()))
            out.append("- accepted items by difficulty: " + ", ".join(f"{d}={n}" for d, n in diff.most_common()))
    # Checkpoint 2
    for ev in events:
        if ev.get("kind") == "checkpoint_reached" and ev["extras"].get("checkpoint") == 2:
            out.append(f"- Checkpoint 2: {ev['extras'].get('message', 'reached')}")
            break
    out.append("")
    return out


def _phase_3_section(
    events: list[dict],
    epoch_snapshots: list[Path],
    final_draft: dict | None,
) -> list[str]:
    out = ["## Phase 3 — Refinement epochs", ""]
    win = _phase_window(events, "phase_3")
    if win is not None:
        out.append(f"- duration: {_format_duration((win[1] - win[0]).total_seconds())}")
    if not epoch_snapshots:
        out.append("- Phase 3 was skipped or did not run.")
        out.append("")
        return out
    out.append(f"- {len(epoch_snapshots)} epoch(s) executed")
    out.append("")
    for path in epoch_snapshots:
        data = _load(path)
        if data is None:
            continue
        metrics = data.get("metrics", {})
        epoch_num = metrics.get("epoch", "?")
        out.append(f"### Epoch {epoch_num}")
        sev = metrics.get("new_objections_by_severity", {}) or {}
        if sev:
            out.append("- new objections raised: " + ", ".join(
                f"{k}={v}" for k, v in sev.items() if v
            ))
        out.append(
            f"- SME outcomes: {metrics.get('resolved_via_edit', 0)} resolved via edit, "
            f"{metrics.get('rebutted', 0)} rebutted, {metrics.get('deferred', 0)} deferred"
        )
        if metrics.get("items_rejected"):
            out.append(f"- {metrics['items_rejected']} item(s) rejected after post-edit re-verify failed")
        out.append(
            f"- end of epoch: {metrics.get('critical_high_open_at_end', 0)} critical/high "
            f"objections still open"
        )
        if metrics.get("converged"):
            out.append("- **converged** (no critical/high open) — Phase 3 exited early")
        out.append("")
    if final_draft:
        items = final_draft.get("items", [])
        survivors = [i for i in items if i.get("status") != "rejected"]
        out.append(
            f"- final state: {len(survivors)} items surviving "
            f"({len(items) - len(survivors)} rejected across all epochs); "
            f"{sum(i.get('points', 0) for i in survivors)} total points"
        )
        out.append(
            f"- {len(final_draft.get('objections_open', []))} objections still open, "
            f"{len(final_draft.get('objections_resolved', []))} resolved"
        )
    out.append("")
    return out


def _phase_4_section(
    events: list[dict],
    audit: dict | None,
    variants: dict | None,
) -> list[str]:
    out = ["## Phase 4 — Audit and Export", ""]
    win = _phase_window(events, "phase_4")
    if win is not None:
        out.append(f"- duration: {_format_duration((win[1] - win[0]).total_seconds())}")
    if not audit:
        out.append("- Phase 4 did not run (no audit snapshot found).")
        out.append("")
        return out

    report = audit.get("report", {})
    bd = report.get("bloom_distribution", [])
    if bd:
        out.append("- Bloom distribution (actual): " + ", ".join(
            f"{r.get('bloom_level')}={r.get('item_count')}({r.get('points')}pts)"
            for r in bd
        ))
    dc = report.get("difficulty_curve", {})
    if dc:
        out.append(
            f"- difficulty curve actual: easy={dc.get('easy_count')} / "
            f"med={dc.get('medium_count')} / hard={dc.get('hard_count')} "
            f"(targets {dc.get('target_easy_ratio'):.2f} / "
            f"{dc.get('target_medium_ratio'):.2f} / "
            f"{dc.get('target_hard_ratio'):.2f})"
        )
    cov = report.get("clo_coverage", [])
    uncovered = [c for c in cov if not c.get("is_covered")]
    if uncovered:
        out.append(f"- {len(uncovered)} MLO(s) uncovered: " + ", ".join(c.get("clo_id", "?") for c in uncovered))
    notes = report.get("imbalance_notes", [])
    if notes:
        out.append(f"- {len(notes)} imbalance note(s):")
        for n in notes[:5]:
            out.append(f"  - {str(n)[:200]}")

    objections = audit.get("objections", [])
    if objections:
        out.append(f"- {len(objections)} exam-level objection(s) raised by the Psychometrician:")
        for o in objections[:5]:
            out.append(
                f"  - [{o.get('severity')}] {o.get('category')}: "
                f"{str(o.get('claim', ''))[:180]}"
            )

    n_variants = len(variants.get("variants", [])) if variants else 0
    out.append(f"- variants generated: {n_variants}")

    # Checkpoint 3
    for ev in events:
        if ev.get("kind") == "checkpoint_reached" and ev["extras"].get("checkpoint") == 3:
            out.append(f"- Checkpoint 3: {ev['extras'].get('message', 'reached')}")
            break

    out.append("")
    return out
