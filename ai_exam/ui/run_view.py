"""Setup + kick-off page.

User uploads materials, edits the three spec forms (CourseSpec, ExamSpec,
TradeOffPolicy), and clicks Start. We:
- write the PDF + JSONs to `uploads/run_<ts>/`
- create `runs/run_<ts>/`
- launch `python run.py --pdf ... --inputs-dir ... --outputs-dir ...` as a
  detached subprocess (start_new_session=True)
- jump the user to the Job Monitor page focused on this run with Live ON
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import streamlit as st

from ui.model_picker import TierChoice, render_tier_picker
from ui.run_forms import (
    render_course_spec_form,
    render_exam_spec_form,
    render_policy_form,
)
from ui.run_launcher import LaunchSpec, launch_run


def _load_default(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


_CONFIG_ENVELOPE_VERSION = 1


def _init_state(project_root: Path) -> None:
    """One-time-per-session: seed form state from test_data defaults."""
    test_dir = project_root / "test_data"
    if "course_spec_form" not in st.session_state:
        st.session_state["course_spec_form"] = _load_default(test_dir / "course_spec.json") or {
            "clos": [], "topics": [], "guiding_principles": "",
        }
    if "exam_spec_form" not in st.session_state:
        st.session_state["exam_spec_form"] = _load_default(test_dir / "exam_spec.json") or {}
    if "policy_form" not in st.session_state:
        st.session_state["policy_form"] = _load_default(test_dir / "policy.json") or {}
    if "max_epochs_override" not in st.session_state:
        st.session_state["max_epochs_override"] = None


def _build_config_envelope() -> dict[str, Any]:
    """Serialize the current form state into a single export-friendly object."""
    return {
        "envelope_version": _CONFIG_ENVELOPE_VERSION,
        "course_spec": st.session_state.get("course_spec_form", {}),
        "exam_spec": st.session_state.get("exam_spec_form", {}),
        "policy": st.session_state.get("policy_form", {}),
        "max_epochs_override": st.session_state.get("max_epochs_override"),
    }


def _apply_config_envelope(envelope: dict[str, Any]) -> tuple[bool, str]:
    """Restore form state from an uploaded envelope. Returns (ok, message)."""
    if not isinstance(envelope, dict):
        return False, "Envelope is not a JSON object."
    if envelope.get("envelope_version") != _CONFIG_ENVELOPE_VERSION:
        return False, (
            f"Envelope version {envelope.get('envelope_version')} not recognized; "
            f"this build expects {_CONFIG_ENVELOPE_VERSION}."
        )
    course_spec = envelope.get("course_spec")
    exam_spec = envelope.get("exam_spec")
    policy = envelope.get("policy")
    if not all(isinstance(x, dict) for x in (course_spec, exam_spec, policy)):
        return False, "Envelope is missing one of course_spec / exam_spec / policy."
    st.session_state["course_spec_form"] = course_spec
    st.session_state["exam_spec_form"] = exam_spec
    st.session_state["policy_form"] = policy
    st.session_state["max_epochs_override"] = envelope.get("max_epochs_override")
    return True, "Loaded."


def _render_config_io() -> None:
    """Top-of-page download + upload of the full configuration."""
    cols = st.columns([2, 2, 3])
    with cols[0]:
        envelope = _build_config_envelope()
        st.download_button(
            "💾 Export configuration",
            data=json.dumps(envelope, indent=2),
            file_name="ai_exam_config.json",
            mime="application/json",
            use_container_width=True,
            help="Save the current course / exam / policy as a single JSON file. "
                 "Reload it later via 'Import configuration' to skip the form again.",
        )
    with cols[1]:
        uploaded = st.file_uploader(
            "Import configuration",
            type=["json"],
            accept_multiple_files=False,
            key="config_uploader",
            label_visibility="collapsed",
            help="Upload a previously-exported ai_exam_config.json to restore the form.",
        )
    # Apply on each rerun if we have a fresh upload (Streamlit's file_uploader
    # re-emits the same UploadedFile across reruns; we key off the file name
    # in session state so we apply exactly once.)
    if uploaded is not None:
        sig = (uploaded.name, uploaded.size)
        if st.session_state.get("_last_imported_sig") != sig:
            try:
                envelope = json.loads(uploaded.read().decode("utf-8"))
            except json.JSONDecodeError as exc:
                st.error(f"Could not parse JSON: {exc}")
            else:
                ok, msg = _apply_config_envelope(envelope)
                if ok:
                    st.session_state["_last_imported_sig"] = sig
                    st.toast(f"Imported {uploaded.name}", icon="✅")
                    st.rerun()
                else:
                    st.error(msg)


def _validate_specs(
    course_spec: dict, exam_spec: dict, policy: dict,
) -> list[str]:
    """Return a list of human-readable errors; empty if all good."""
    from models import CourseSpec, ExamSpec  # local import: keep page light
    from moderator.policy import TradeOffPolicy

    errors: list[str] = []
    try:
        CourseSpec.model_validate(course_spec)
    except Exception as exc:
        errors.append(f"CourseSpec: {exc}")
    try:
        ExamSpec.model_validate(exam_spec)
    except Exception as exc:
        errors.append(f"ExamSpec: {exc}")
    try:
        TradeOffPolicy.model_validate(policy)
    except Exception as exc:
        errors.append(f"TradeOffPolicy: {exc}")
    # Cross-check: priority_rank must have 5 distinct dimensions.
    pr = policy.get("priority_rank", [])
    if len(set(pr)) != 5:
        errors.append("policy.priority_rank must list 5 distinct dimensions")
    # Item-type counts vs total — soft warning, not blocking, since the
    # blueprint may legitimately deviate when the materials require it.
    return errors


def _stage_inputs(
    project_root: Path,
    run_ts: str,
    pdf_bytes: bytes,
    pdf_name: str,
    course_spec: dict,
    exam_spec: dict,
    policy: dict,
) -> tuple[Path, Path]:
    """Write the PDF and the 3 spec JSONs to `uploads/run_<ts>/`. Returns
    (pdf_path, inputs_dir)."""
    inputs_dir = project_root / "uploads" / f"run_{run_ts}"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    # Preserve the original PDF name (run.py prints it). Default to a stable
    # name if upload was nameless somehow.
    safe_name = pdf_name or "materials.pdf"
    pdf_path = inputs_dir / safe_name
    pdf_path.write_bytes(pdf_bytes)
    (inputs_dir / "course_spec.json").write_text(
        json.dumps(course_spec, indent=2), encoding="utf-8",
    )
    (inputs_dir / "exam_spec.json").write_text(
        json.dumps(exam_spec, indent=2), encoding="utf-8",
    )
    (inputs_dir / "policy.json").write_text(
        json.dumps(policy, indent=2), encoding="utf-8",
    )
    return pdf_path, inputs_dir


def render_run_page(*, project_root: Path) -> None:
    st.title("AI Exam Builder")
    st.caption(
        "Configure a course and exam, then run the multi-agent pipeline to "
        "produce a draft exam plus instructor notes, answer key, and audit report."
    )
    _init_state(project_root)
    _render_config_io()
    st.divider()

    # --- Materials ---
    st.markdown("## 1. Materials")
    cols = st.columns([4, 2])
    with cols[0]:
        uploaded = st.file_uploader(
            "Upload course materials (PDF)",
            type=["pdf"],
            accept_multiple_files=False,
            help="The lecture notes, textbook excerpt, or other source material the "
                 "exam will draw on. PDF only for now.",
        )
    with cols[1]:
        use_test = st.toggle(
            "Use bundled test corpus",
            value=True,
            help="Use test_data/pchem_notes.pdf instead of uploading. "
                 "Toggle off when you have your own PDF ready.",
        )

    pdf_bytes: bytes | None = None
    pdf_name: str | None = None
    if use_test:
        test_pdf = project_root / "test_data" / "pchem_notes.pdf"
        if test_pdf.exists():
            pdf_bytes = test_pdf.read_bytes()
            pdf_name = test_pdf.name
            st.caption(f"Using `{test_pdf.relative_to(project_root)}` "
                       f"({len(pdf_bytes):,} bytes)")
        else:
            st.error(f"Test corpus not found at {test_pdf}")
    elif uploaded is not None:
        pdf_bytes = uploaded.getvalue()
        pdf_name = uploaded.name
        st.caption(f"Loaded `{uploaded.name}` ({len(pdf_bytes):,} bytes)")

    st.divider()

    # --- Materials spec ---
    st.markdown("## 2. Materials spec")
    st.caption(
        "Describe what the uploaded materials cover — the learning objectives "
        "students should be tested on (MLOs), the syllabus topics those "
        "materials map to, and any free-text principles you want every agent "
        "to honor. Scope these to the **uploaded PDF**, not the whole course."
    )
    with st.container(border=True):
        course_spec = render_course_spec_form("course_spec_form")

    # --- Exam spec ---
    st.markdown("## 3. Exam spec")
    with st.container(border=True):
        exam_spec = render_exam_spec_form("exam_spec_form")

    # --- Policy ---
    st.markdown("## 4. Trade-off policy")
    with st.container(border=True):
        policy = render_policy_form("policy_form")

    # --- Run knobs ---
    st.markdown("## 5. Run options")
    st.caption(
        "Two-tier model routing: HIGH-tier agents (SME, Blueprint Architect, "
        "Adversarial Student) do content generation; LOW-tier agents (IWS, LOA, "
        "Grounding, Accessibility, Psychometrician) do verification and audit. "
        "A typical run is ~30 HIGH calls and ~80 LOW calls — putting LOW on a "
        "free local model and HIGH on a strong cloud model is usually the "
        "sweet spot for cost vs. quality."
    )
    import config as _cfg  # local — used to read OLLAMA_MODEL default
    high = render_tier_picker(
        label="HIGH tier",
        help="Content generation: SME, Blueprint Architect, Adversarial Student.",
        state_key_provider="tier_high_provider",
        state_key_model="tier_high_model",
        ollama_fallback_model=_cfg.OLLAMA_MODEL,
        default=TierChoice(provider="anthropic", model=_cfg.ANTHROPIC_MODEL_SONNET),
    )
    low = render_tier_picker(
        label="LOW tier",
        help="Verification + audit: IWS, LOA, Grounding, Accessibility, Psychometrician.",
        state_key_provider="tier_low_provider",
        state_key_model="tier_low_model",
        ollama_fallback_model=_cfg.OLLAMA_MODEL,
        default=TierChoice(provider="anthropic", model="claude-haiku-4-5-20251001"),
    )

    # Dynamic default: read from the live policy form so changing the policy's
    # max_epochs there flows through here. Falls back to 4 if the form hasn't
    # populated yet (shouldn't happen, but defensive).
    _policy_form = st.session_state.get("policy_form", {}) or {}
    _policy_max = int(_policy_form.get("max_epochs", 4)) or 4
    max_epochs = st.number_input(
        "Max Phase 3 refinement epochs",
        min_value=1, max_value=10,
        value=int(st.session_state.get("max_epochs_override") or _policy_max),
        step=1,
        help="Hard cap on the Phase 3 refinement loop. Each epoch is one full "
             "critique → SME-rebut/edit → reverify cycle over every surviving "
             "item. Convergence (no critical or high objections left open) "
             "exits the loop earlier than the cap.",
    )
    st.session_state["max_epochs_override"] = max_epochs

    st.divider()

    # --- Validate + Start ---
    errors = _validate_specs(course_spec, exam_spec, policy)
    if pdf_bytes is None:
        errors.append("Upload a PDF (or toggle 'Use bundled test corpus').")
    for e in errors:
        st.error(e)

    start_disabled = bool(errors)
    if st.button("▶️ Start Run", type="primary", disabled=start_disabled,
                 use_container_width=True):
        run_ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        assert pdf_bytes is not None and pdf_name is not None  # guarded above
        pdf_path, inputs_dir = _stage_inputs(
            project_root, run_ts, pdf_bytes, pdf_name,
            course_spec, exam_spec, policy,
        )
        outputs_dir = project_root / "runs" / f"run_{run_ts}"
        info = launch_run(project_root, LaunchSpec(
            pdf_path=pdf_path,
            inputs_dir=inputs_dir,
            outputs_dir=outputs_dir,
            max_epochs=st.session_state.get("max_epochs_override"),
            high_provider=high.provider,
            high_model=high.model,
            low_provider=low.provider,
            low_model=low.model,
        ))
        # Tell the Job Monitor page to focus on this run with Live ON.
        st.session_state["focus_run"] = f"run_{run_ts}"
        st.session_state["focus_run_live"] = True
        # One-shot flag picked up by streamlit_app.main() to switch pages.
        st.session_state["_switch_to_transcript"] = True
        st.toast(f"Started run_{run_ts} (PID {info['pid']})", icon="🚀")
        st.rerun()

    # --- Reset forms back to test_data defaults ---
    with st.expander("Form actions"):
        if st.button("↺ Reset all forms to test_data defaults"):
            for k in ("course_spec_form", "exam_spec_form", "policy_form"):
                st.session_state.pop(k, None)
            st.rerun()
