"""Persona editor page.

Each agent's constitution lives in `persona/<persona_name>.md` and is loaded
once when the agent is instantiated in `run.py`. Edits here take effect on
the *next* pipeline run — a run already in progress will keep using the
persona it loaded at start.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st


def _state_key(persona_path: Path) -> str:
    return f"persona_buf::{persona_path.name}"


def render_personas_page(*, persona_dir: Path) -> None:
    st.title("Personas")
    st.caption(
        f"Editing agent constitutions under `{persona_dir.name}/`. "
        f"Edits take effect on the next `python run.py` invocation; "
        f"runs already in flight keep the persona they loaded at start."
    )

    personas = sorted(p for p in persona_dir.glob("*.md") if p.is_file())
    if not personas:
        st.warning(f"No `.md` files found in {persona_dir}")
        return

    with st.sidebar:
        st.header("Persona")
        picked_idx = st.selectbox(
            "Select persona", range(len(personas)),
            format_func=lambda i: personas[i].stem,
            index=0,
        )
        picked = personas[picked_idx]
        st.caption(f"`{picked.relative_to(persona_dir.parent)}`")

    on_disk = picked.read_text(encoding="utf-8")

    # Per-file buffer in session state — survives reruns and tab switches.
    key = _state_key(picked)
    if key not in st.session_state:
        st.session_state[key] = on_disk
    buf = st.session_state[key]
    is_dirty = buf != on_disk

    # Header row: title + Save / Reset / Reload-from-disk
    title_col, save_col, reset_col, reload_col = st.columns([4, 1, 1, 1])
    with title_col:
        marker = " — *unsaved changes*" if is_dirty else ""
        st.markdown(f"### `{picked.name}`{marker}")
    with save_col:
        if st.button("💾 Save", disabled=not is_dirty, use_container_width=True):
            picked.write_text(buf, encoding="utf-8")
            st.session_state[key] = picked.read_text(encoding="utf-8")
            st.toast(f"Saved {picked.name}", icon="✅")
            st.rerun()
    with reset_col:
        if st.button("↺ Reset", disabled=not is_dirty, use_container_width=True,
                     help="Discard unsaved changes and reload from disk."):
            del st.session_state[key]
            st.rerun()
    with reload_col:
        if st.button("🔁 Reload", use_container_width=True,
                     help="Re-read from disk even when not dirty (in case of external changes)."):
            st.session_state[key] = on_disk
            st.rerun()

    edited = st.text_area(
        "Markdown content",
        value=buf,
        height=600,
        label_visibility="collapsed",
        key=f"editor::{picked.name}",
    )
    if edited != buf:
        st.session_state[key] = edited
        # No rerun here — st.text_area updates session_state on next interaction.

    with st.expander("Tips", expanded=False):
        st.markdown(
            "- Constitutions are agent **system prompts**. Edit with intent: "
            "they shape every call this agent makes.\n"
            "- The first call after a change pays a cache miss on the system "
            "prompt (ephemeral cache_control). Subsequent calls within the same "
            "agent re-cache.\n"
            "- Architectural rules in `CLAUDE.md` still apply — e.g., agents "
            "must remain in their lane (Item-Writing Specialist doesn't critique "
            "content, etc.). Edit personas to clarify, not to redraw boundaries."
        )
