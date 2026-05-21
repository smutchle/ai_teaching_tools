"""Build the Plotly timeline figure from a run's event stream.

One bar per `invocation_started` / `invocation_completed` pair, color-coded
by agent, swimlanes (y-axis) ordered by the canonical pipeline role. Phase
transitions (`routing_decision` events with `extras.phase`) become vertical
dashed lines; checkpoints (`checkpoint_reached`) become vertical solid lines.

The figure attaches `call_id` to each bar's `customdata` so the caller can
recover the selected call when Streamlit reports a click event.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from events import AgentEvent, EventKind


# Fixed swimlane order — keeps the chart visually stable across runs even if
# some agents don't fire in a given phase. Top-to-bottom roughly follows the
# pipeline flow: orchestration → content authoring → mechanical → alignment
# → grounding → critics → psychometric.
_AGENT_ORDER: list[str] = [
    "moderator",
    "sme",
    "blueprint_architect",
    "item_writing_specialist",
    "learning_outcomes_alignment",
    "grounding_verifier",
    "accessibility",
    "adversarial_student",
    "psychometrician",
]


def build_call_records(events: list[AgentEvent]) -> pd.DataFrame:
    """Pair start/end events into one row per call. Unfinished calls (still
    running, or failed without a recorded completion) get end = last seen
    event timestamp, so they show up as in-flight bars during live polling."""
    starts: dict[str, AgentEvent] = {}
    ends: dict[str, AgentEvent] = {}
    last_ts: datetime | None = None
    for ev in events:
        last_ts = ev.timestamp
        if ev.call_id is None:
            continue
        if ev.kind == EventKind.INVOCATION_STARTED:
            starts[ev.call_id] = ev
        elif ev.kind in (EventKind.INVOCATION_COMPLETED, EventKind.INVOCATION_FAILED):
            ends[ev.call_id] = ev

    fallback_end = last_ts or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for cid, s in starts.items():
        e = ends.get(cid)
        if e is not None:
            end_ts = e.timestamp
            status = (
                "completed" if e.kind == EventKind.INVOCATION_COMPLETED else "failed"
            )
        else:
            end_ts = fallback_end
            status = "running"
        rows.append({
            "call_id": cid,
            "agent": s.agent,
            "verb": s.verb or "?",
            "target": s.target or "",
            "epoch": s.epoch or 0,
            "start": s.timestamp,
            "end": end_ts,
            "duration_s": max((end_ts - s.timestamp).total_seconds(), 0.05),
            "status": status,
        })
    return pd.DataFrame(rows)


def _find_phase_markers(events: list[AgentEvent]) -> list[tuple[datetime, str]]:
    out: list[tuple[datetime, str]] = []
    for ev in events:
        if ev.kind != EventKind.ROUTING_DECISION:
            continue
        phase = ev.extras.get("phase")
        if isinstance(phase, str):
            out.append((ev.timestamp, phase))
    return out


def _find_checkpoint_markers(events: list[AgentEvent]) -> list[tuple[datetime, int]]:
    out: list[tuple[datetime, int]] = []
    for ev in events:
        if ev.kind != EventKind.CHECKPOINT_REACHED:
            continue
        cp = ev.extras.get("checkpoint")
        if isinstance(cp, int):
            out.append((ev.timestamp, cp))
    return out


def build_timeline_figure(events: list[AgentEvent]) -> go.Figure:
    """Top-level builder: returns a Plotly figure ready for st.plotly_chart."""
    df = build_call_records(events)
    if df.empty:
        # Empty figure with axis labels so the page still renders.
        fig = go.Figure()
        fig.update_layout(
            xaxis_title="time", yaxis_title="agent",
            annotations=[dict(text="No invocations yet", showarrow=False,
                              x=0.5, y=0.5, xref="paper", yref="paper")],
        )
        return fig

    # Use the fixed lane order but only for agents that actually appear.
    seen_agents = list(df["agent"].unique())
    lane_order = [a for a in _AGENT_ORDER if a in seen_agents] + [
        a for a in seen_agents if a not in _AGENT_ORDER
    ]

    fig = px.timeline(
        df,
        x_start="start", x_end="end", y="agent",
        color="agent",
        hover_data={
            "verb": True, "target": True, "epoch": True,
            "duration_s": ":.2f", "status": True, "call_id": True,
            "start": False, "end": False, "agent": False,
        },
        custom_data=["call_id"],
        category_orders={"agent": lane_order},
    )
    # px.timeline reverses the y-axis by default (alphabetical bottom-up);
    # category_orders + this call puts our explicit order from top to bottom.
    fig.update_yaxes(autorange="reversed", title="")
    fig.update_xaxes(title="")
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=30),
        showlegend=False,
        height=max(280, 40 * len(lane_order) + 80),
        clickmode="event+select",
    )

    # Phase + checkpoint vertical lines. Plotly's `add_vline` annotation path
    # crashes on datetime x values (it tries `mean(int + datetime)` internally),
    # so we draw the line and place the text via add_annotation separately.
    for ts, phase in _find_phase_markers(events):
        fig.add_vline(x=ts, line_dash="dash", line_color="#888")
        fig.add_annotation(
            x=ts, y=1.02, yref="paper", text=phase, showarrow=False,
            font=dict(size=10, color="#666"),
        )
    for ts, cp in _find_checkpoint_markers(events):
        fig.add_vline(x=ts, line_dash="solid", line_color="#2ca02c")
        fig.add_annotation(
            x=ts, y=1.02, yref="paper", text=f"CP{cp}", showarrow=False,
            font=dict(size=10, color="#2ca02c"),
        )
    return fig
