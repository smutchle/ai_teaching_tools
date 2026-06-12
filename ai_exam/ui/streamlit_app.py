"""ai_exam Streamlit observation UI — entry point.

Read-only multi-page app over the JSON snapshots and events.jsonl that
`run.py` writes under `runs/run_<ts>/`. Three pages:

- Job Monitor — timeline view of the event stream, with live polling
- Job Outputs — download buttons and inline previews for Phase-4 artifacts
- Personas — edit the agent constitution `.md` files

Run with `./run.sh` (foreground) or `./run_in_background.sh` (nohup).
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
from vt_banner import render_vt_banner


_THIS = Path(__file__).resolve()
_PROJECT_ROOT = _THIS.parent.parent
# Ensure the project root is on sys.path so `import events`, `import models`
# etc. work whether streamlit is launched from the project root or elsewhere.
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_RUNS_DIR = _PROJECT_ROOT / "runs"
_PERSONA_DIR = _PROJECT_ROOT / "persona"


def _run() -> None:
    from ui.run_view import render_run_page
    render_run_page(project_root=_PROJECT_ROOT)


def _transcript() -> None:
    from ui.transcript_view import render_transcript_page
    render_transcript_page(runs_dir=_RUNS_DIR)


def _bundle() -> None:
    from ui.bundle_view import render_bundle_page
    render_bundle_page(runs_dir=_RUNS_DIR)


def _personas() -> None:
    from ui.personas_view import render_personas_page
    render_personas_page(persona_dir=_PERSONA_DIR)


def _docs() -> None:
    from ui.docs_view import render_docs_page
    render_docs_page()


def main() -> None:
    st.set_page_config(
        page_title="AI Exam Builder",
        page_icon="🎓",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    render_vt_banner()
    run_page = st.Page(_run, title="Run", default=True)
    job_monitor_page = st.Page(_transcript, title="Job Monitor")
    bundle_page = st.Page(_bundle, title="Job Outputs")
    personas_page = st.Page(_personas, title="Personas")
    docs_page = st.Page(_docs, title="Documentation")
    nav = st.navigation([run_page, job_monitor_page, bundle_page, personas_page, docs_page])
    # If the Run page set this flag (one-shot), jump to Job Monitor immediately.
    if st.session_state.pop("_switch_to_transcript", False):
        st.switch_page(job_monitor_page)
    nav.run()


main()
