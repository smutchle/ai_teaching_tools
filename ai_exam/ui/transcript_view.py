"""Chat-bubble transcript page.

Reads events.jsonl for a selected run and renders each event as a chat
message bubble. A "Live" toggle uses `st.fragment(run_every=...)` to poll
the file for new events while a pipeline is running.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import streamlit as st

from events import AgentEvent, EventKind
from ui.event_loader import list_runs, load_events, load_sidecar, run_summary
from ui.run_launcher import run_status
from ui.timeline_builder import build_timeline_figure


def _dir_size(p: Path) -> tuple[int, int]:
    """Return (file_count, total_bytes) under `p`. Safe on missing dirs."""
    if not p.exists():
        return (0, 0)
    n = 0
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            n += 1
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return (n, total)


def _delete_run_artifacts(run_dir: Path, uploads_dir: Path) -> None:
    """Remove the run directory AND its matching uploads/run_<ts>/."""
    shutil.rmtree(run_dir, ignore_errors=True)
    matching_upload = uploads_dir / run_dir.name
    if matching_upload.exists():
        shutil.rmtree(matching_upload, ignore_errors=True)


@st.dialog("Delete run")
def _confirm_delete_one(run_dir: Path, uploads_dir: Path) -> None:
    status = run_status(run_dir)
    if status == "running":
        st.error(
            f"`{run_dir.name}` is still running. Stop the pipeline before "
            f"deleting (or the directory will be re-created mid-write)."
        )
        if st.button("OK", use_container_width=True):
            st.rerun()
        return
    n_files, total_bytes = _dir_size(run_dir)
    n_files_up, total_bytes_up = _dir_size(uploads_dir / run_dir.name)
    st.markdown(f"Permanently delete `{run_dir.name}`?")
    st.caption(
        f"`runs/{run_dir.name}/` — {n_files} files, {total_bytes:,} bytes  \n"
        f"`uploads/{run_dir.name}/` — {n_files_up} files, {total_bytes_up:,} bytes"
    )
    confirm = st.checkbox("Yes — I understand this cannot be undone.")
    cols = st.columns(2)
    if cols[0].button("Cancel", use_container_width=True):
        st.rerun()
    if cols[1].button(
        "Delete permanently", disabled=not confirm,
        type="primary", use_container_width=True,
    ):
        _delete_run_artifacts(run_dir, uploads_dir)
        st.toast(f"Deleted {run_dir.name}", icon="🗑️")
        st.rerun()


@st.dialog("Delete prior runs")
def _confirm_delete_prior(
    runs_dir: Path, uploads_dir: Path, keep: Path,
) -> None:
    """Delete every run EXCEPT the currently-selected one and any running ones."""
    runs = list_runs(runs_dir)
    live = [r for r in runs if run_status(r) == "running"]
    deletable = [
        r for r in runs
        if r != keep and run_status(r) != "running"
    ]
    if not deletable:
        st.info(f"Nothing to delete — only the current run (`{keep.name}`) exists.")
        if st.button("OK", use_container_width=True):
            st.rerun()
        return
    total_n, total_b = 0, 0
    for r in deletable:
        nf, tb = _dir_size(r)
        nfu, tbu = _dir_size(uploads_dir / r.name)
        total_n += nf + nfu
        total_b += tb + tbu
    st.markdown(
        f"Permanently delete **{len(deletable)} prior run(s)** and their "
        f"matching upload directories?"
    )
    st.caption(
        f"Keeping the current selection `{keep.name}`. "
        f"Total to delete: {total_n} files, {total_b:,} bytes."
    )
    if live:
        st.warning(
            f"{len(live)} run(s) are still running and will be skipped: "
            + ", ".join(r.name for r in live)
        )
    confirm = st.checkbox("Yes — I understand this cannot be undone.")
    cols = st.columns(2)
    if cols[0].button("Cancel", use_container_width=True):
        st.rerun()
    if cols[1].button(
        "Delete prior permanently", disabled=not confirm,
        type="primary", use_container_width=True,
    ):
        for r in deletable:
            _delete_run_artifacts(r, uploads_dir)
        st.toast(f"Deleted {len(deletable)} prior run(s)", icon="🗑️")
        st.rerun()


def _run_label(run_dir: Path) -> str:
    s = run_summary(run_dir)
    status = run_status(run_dir)
    badge = {"running": "🔴", "finished": "✅", "unknown": "·"}[status]
    marks: list[str] = []
    if s["has_bundle"]:
        marks.append("📦")
    return f"{badge} {s['name']} · {s['events']} events {' '.join(marks)}".rstrip()


def _filter_events(
    events: list[AgentEvent],
    *,
    agents: set[str] | None,
    epoch: int | None,
    show_routing: bool,
) -> list[AgentEvent]:
    out: list[AgentEvent] = []
    for e in events:
        if agents and e.agent not in agents:
            continue
        if epoch is not None and e.epoch != epoch:
            continue
        if not show_routing and e.kind == EventKind.ROUTING_DECISION:
            continue
        out.append(e)
    return out


def render_transcript_page(*, runs_dir: Path) -> None:
    st.title("Job Monitor")

    runs = list_runs(runs_dir)
    if not runs:
        st.info(
            f"No runs found under `{runs_dir}`. Kick off a pipeline with "
            f"`python run.py --pdf <your.pdf>` and refresh."
        )
        return

    # If the Run page just launched a job, default-pick that run + Live ON.
    focus_name = st.session_state.pop("focus_run", None)
    focus_live = st.session_state.pop("focus_run_live", False)
    default_idx = 0
    if focus_name is not None:
        for i, r in enumerate(runs):
            if r.name == focus_name:
                default_idx = i
                break

    # --- Sidebar: run picker + filters ---
    with st.sidebar:
        st.header("Run")
        labels = [_run_label(r) for r in runs]
        picked_idx = st.selectbox(
            "Select run", range(len(runs)),
            format_func=lambda i: labels[i], index=default_idx,
        )
        picked = runs[picked_idx]
        live = st.toggle(
            "Live (poll 2s)", value=focus_live,
            help="Re-read events.jsonl every 2 seconds. Useful while a run is in progress.",
        )
        status = run_status(picked)
        st.caption({
            "running": "🔴 running",
            "finished": "✅ finished",
            "unknown": "· status unknown",
        }[status])
        # --- delete controls ---
        uploads_dir = runs_dir.parent / "uploads"
        if st.button(
            "🗑️ Delete this run", use_container_width=True,
            disabled=(status == "running"),
            help="Removes runs/<this>/ and the matching uploads/<this>/ folder.",
        ):
            _confirm_delete_one(picked, uploads_dir)
        if st.button(
            "🗑️ Delete prior runs", use_container_width=True,
            help="Removes every run EXCEPT the currently selected one. "
                 "Running runs are skipped.",
        ):
            _confirm_delete_prior(runs_dir, uploads_dir, keep=picked)

    events_path = picked / "events" / "events.jsonl"
    calls_dir = picked / "events" / "calls"

    # Load events. If events.jsonl doesn't exist yet (we hit this page right
    # after starting a run, before run.py has emitted its first event), keep
    # going with an empty list — the live fragment below will pick up events
    # when the file appears.
    all_events = load_events(events_path) if events_path.exists() else []
    agents_present = sorted({e.agent for e in all_events})
    epochs_present = sorted({e.epoch for e in all_events if e.epoch})

    with st.sidebar:
        st.divider()
        st.header("Filters")
        agent_filter = st.multiselect(
            "Agents", agents_present, default=[],
            placeholder="(show all)",
        )
        epoch_choices = ["(all)"] + [f"epoch {e}" for e in epochs_present]
        epoch_pick = st.selectbox("Epoch", epoch_choices, index=0)
        show_routing = st.toggle(
            "Show moderator routing", value=True,
            help="Hide to focus on agent messages.",
        )

    agents_set = set(agent_filter) if agent_filter else None
    epoch_val: int | None = None
    if epoch_pick != "(all)":
        epoch_val = int(epoch_pick.split()[-1])

    def _selected_call_id_from_widget(widget_state: Any) -> str | None:
        """Pull the call_id out of Streamlit's plotly_chart selection payload.

        The shape (as of streamlit 1.57) is:
          { "selection": { "points": [ { "customdata": ["<call_id>"], ... } ] } }
        We tucked call_id into customdata in timeline_builder.
        """
        if not widget_state:
            return None
        sel = getattr(widget_state, "selection", None) or widget_state.get("selection") \
            if isinstance(widget_state, dict) else getattr(widget_state, "selection", None)
        if not sel:
            return None
        points = sel.get("points") if isinstance(sel, dict) else getattr(sel, "points", None)
        if not points:
            return None
        cd = points[0].get("customdata") if isinstance(points[0], dict) else None
        if cd and len(cd) > 0:
            return str(cd[0])
        return None

    def _render_drilldown(events: list[AgentEvent], call_id: str | None) -> None:
        """Below-chart detail panel: full event + sidecar for the selected call."""
        if not call_id:
            st.info("Click a bar on the timeline to see the full event + sidecar.")
            return
        matches = [
            e for e in events
            if e.call_id == call_id and e.kind in (
                EventKind.INVOCATION_STARTED,
                EventKind.INVOCATION_COMPLETED,
                EventKind.INVOCATION_FAILED,
            )
        ]
        if not matches:
            st.warning(f"No events found for call_id `{call_id}`.")
            return
        # Prefer the COMPLETED event (it has the output summary + duration).
        primary = next(
            (e for e in matches if e.kind == EventKind.INVOCATION_COMPLETED),
            matches[0],
        )
        bits = [primary.kind.value, primary.agent]
        if primary.verb:
            bits.append(primary.verb)
        if primary.target:
            bits.append(f"→ {primary.target}")
        st.markdown("**" + " · ".join(bits) + "**")
        node: dict[str, Any] = primary.model_dump(mode="json")
        sidecar = load_sidecar(calls_dir, call_id)
        if sidecar is not None:
            node["_sidecar"] = sidecar
        st.json(node, expanded=True)

    def _render_timeline_and_detail(events: list[AgentEvent]) -> None:
        # Live counters inside the fragment so they tick with the chart.
        # Outer header keeps only the run name (which is stable).
        agents_now = sorted({e.agent for e in events})
        epochs_now = sorted({e.epoch for e in events if e.epoch})
        mcols = st.columns([1, 1, 1, 1])
        mcols[0].metric("Events", len(events))
        mcols[1].metric("Agents", len(agents_now))
        mcols[2].metric("Epochs", len(epochs_now) or 0)
        mcols[3].metric("Status", run_status(picked))

        if not events:
            # File may not exist yet (run just started) or may be empty.
            # Live fragment will keep re-rendering this state until events
            # land, at which point the plot replaces the placeholder.
            current_status = run_status(picked)
            if current_status == "running":
                st.info(
                    f"{'🔴 LIVE · ' if live else ''}"
                    f"Waiting for `events.jsonl` to appear in `{picked.name}`..."
                )
            else:
                st.warning(
                    f"No `events.jsonl` in `{picked.name}`. "
                    + ("Toggle 'Live' to auto-poll." if not live else "")
                )
            return
        filtered = _filter_events(
            events, agents=agents_set, epoch=epoch_val, show_routing=show_routing,
        )
        st.caption(
            ("🔴 LIVE · " if live else "")
            + f"{len(filtered)}/{len(events)} events shown"
        )
        fig = build_timeline_figure(filtered)
        widget_state = st.plotly_chart(
            fig, on_select="rerun", selection_mode="points",
            use_container_width=True, key="timeline_plotly",
        )
        st.divider()
        selected_call_id = _selected_call_id_from_widget(widget_state)
        _render_drilldown(events, selected_call_id)

    # Static run anchor — counts move inside the fragment so they live-tick.
    st.markdown(f"**Run:** `{picked.name}`")

    if live:
        @st.fragment(run_every=2.0)
        def _live_body() -> None:
            # Re-read every tick so we pick up the file the moment it appears.
            events = load_events(events_path) if events_path.exists() else []
            _render_timeline_and_detail(events)
        _live_body()
    else:
        _render_timeline_and_detail(all_events)
