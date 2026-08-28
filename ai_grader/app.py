"""AI Grader — a Streamlit app for grading scanned paper exams with an LLM.

Run:  streamlit run app.py   (inside the `ai_grader` conda env)

Pipeline: Config -> OCR/split -> Grade (+curve) -> Download graded PDFs.
The whole project lives in one working directory under /tmp, backed by a single
state.json that can be saved/loaded at any time.
"""
from __future__ import annotations

import io
import os
import zipfile

import pandas as pd
import streamlit as st

from grader import grading, ocr, pdfutil, roster as roster_mod, state as state_mod
from grader.llm import LLMClient
from vt_banner import render_vt_banner

st.set_page_config(page_title="AI Grader", page_icon="📝", layout="wide")
render_vt_banner()


# ------------------------------------------------------------------ helpers
def get_state() -> dict:
    if "state" not in st.session_state:
        s = state_mod.default_state()
        s["config"]["working_dir"] = state_mod.new_working_dir()
        st.session_state.state = s
        st.session_state.uploaded_ids = {}
    return st.session_state.state


def cfg() -> dict:
    return get_state()["config"]


def save_uploaded(uploaded, dest_name: str) -> str:
    """Persist a Streamlit UploadedFile into the working dir. Returns basename."""
    wd = cfg()["working_dir"]
    os.makedirs(wd, exist_ok=True)
    path = os.path.join(wd, dest_name)
    with open(path, "wb") as f:
        f.write(uploaded.getbuffer())
    return dest_name


def load_roster_safe() -> list[dict]:
    wd = cfg()["working_dir"]
    path = state_mod.abspath(wd, cfg().get("roster_csv", ""))
    if not path or not os.path.exists(path):
        return []
    try:
        return roster_mod.load_roster(path)
    except ValueError as e:
        st.error(str(e))
        return []


def get_llm() -> LLMClient:
    return LLMClient(api_key_override=cfg().get("api_key_override", ""))


def do_save() -> None:
    path = state_mod.save_state(get_state())
    st.session_state["last_save_msg"] = f"Saved to {path}"


# ------------------------------------------------------------------ header
st.title("📝 AI Grader")
state = get_state()
if st.session_state.get("last_save_msg"):
    st.success(st.session_state.pop("last_save_msg"))

tab_config, tab_ocr, tab_grade, tab_download = st.tabs(
    ["⚙️ Config", "🔍 OCR & Split", "✅ Grading", "⬇️ Download"]
)

# =====================================================================
# TAB 1 — CONFIG
# =====================================================================
with tab_config:
    c = cfg()
    st.subheader("Working directory")
    col1, col2 = st.columns([4, 1])
    with col1:
        wd_input = st.text_input(
            "Working dir (created in /tmp; edit + Load to open an existing project)",
            value=c["working_dir"], key="wd_input",
        )
    with col2:
        st.write("")
        st.write("")
        if st.button("Load", use_container_width=True):
            if os.path.exists(state_mod.state_path(wd_input)):
                try:
                    st.session_state.state = state_mod.load_state(wd_input)
                    st.session_state.uploaded_ids = {}
                    st.success(f"Loaded project from {wd_input}")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Failed to load: {e}")
            else:
                st.error(f"No state.json found in {wd_input}")
    c["working_dir"] = wd_input

    st.divider()
    st.subheader("LLM / API")
    c["api_key_override"] = st.text_input(
        "ARC API Key (optional — overrides .env)", value=c.get("api_key_override", ""),
        type="password",
        help="Leave blank to use OPENAI_APIKEY from .env.",
    )

    st.divider()
    st.subheader("Exam details")
    c["quiz_name"] = st.text_input("Quiz / Exam name", value=c.get("quiz_name", ""))

    colA, colB, colC = st.columns(3)
    with colA:
        c["max_points"] = st.number_input(
            "Max points (required)", value=int(c.get("max_points", 100)), step=1)
    with colB:
        c["min_points"] = st.number_input(
            "Min points (required)", value=int(c.get("min_points", 0)), step=1)
    with colC:
        curve_on = st.checkbox(
            "Apply curve", value=c.get("curve_min_avg") is not None)
        curve_val = st.number_input(
            "Curve: Min average score (optional)",
            value=int(c["curve_min_avg"]) if c.get("curve_min_avg") is not None else 90,
            step=1, disabled=not curve_on)
        c["curve_min_avg"] = int(curve_val) if curve_on else None

    c["additional_instructions"] = st.text_area(
        "Additional grading instructions to the LLM (optional)",
        value=c.get("additional_instructions", state_mod.DEFAULT_ADDITIONAL_INSTRUCTIONS),
        height=100,
    )

    st.divider()
    st.subheader("Files")

    # --- Roster CSV
    st.markdown(
        "**Class roster** — CSV with **`last_name, first_name, student_id, email`** "
        "columns (exact names, any order)."
    )
    roster_up = st.file_uploader("Roster CSV", type=["csv"], key="roster_up")
    if roster_up is not None and st.session_state.uploaded_ids.get("roster") != roster_up.file_id:
        c["roster_csv"] = save_uploaded(roster_up, "roster.csv")
        st.session_state.uploaded_ids["roster"] = roster_up.file_id
    if c.get("roster_csv"):
        roster = load_roster_safe()
        st.caption(f"Roster loaded: **{len(roster)} students** ({c['roster_csv']})")

    # --- Exam PDF (single)
    st.markdown("**Exam scan** — single PDF containing all students' papers.")
    exam_up = st.file_uploader("Exam PDF", type=["pdf"], key="exam_up")
    if exam_up is not None and st.session_state.uploaded_ids.get("exam") != exam_up.file_id:
        c["exam_pdf"] = save_uploaded(exam_up, "exam.pdf")
        st.session_state.uploaded_ids["exam"] = exam_up.file_id
    if c.get("exam_pdf"):
        p = state_mod.abspath(c["working_dir"], c["exam_pdf"])
        if os.path.exists(p):
            st.caption(f"Exam loaded: **{pdfutil.page_count(p)} pages** ({c['exam_pdf']})")

    # --- Rubric PDF (single)
    st.markdown("**Rubric & answer key** — single PDF.")
    rubric_up = st.file_uploader("Rubric & answers PDF", type=["pdf"], key="rubric_up")
    if rubric_up is not None and st.session_state.uploaded_ids.get("rubric") != rubric_up.file_id:
        c["rubric_pdf"] = save_uploaded(rubric_up, "rubric.pdf")
        st.session_state.uploaded_ids["rubric"] = rubric_up.file_id
    if c.get("rubric_pdf"):
        st.caption(f"Rubric loaded: {c['rubric_pdf']}")

    # --- Grounding PDFs (multiple)
    st.markdown("**Grounding materials** — one or more PDFs the LLM must stick to.")
    ground_up = st.file_uploader(
        "Grounding PDFs", type=["pdf"], accept_multiple_files=True, key="ground_up")
    if ground_up:
        ids = tuple(f.file_id for f in ground_up)
        if st.session_state.uploaded_ids.get("ground") != ids:
            names = []
            for i, f in enumerate(ground_up, start=1):
                names.append(save_uploaded(f, f"grounding_{i}.pdf"))
            c["grounding_pdfs"] = names
            st.session_state.uploaded_ids["ground"] = ids
    if c.get("grounding_pdfs"):
        st.caption(f"Grounding materials: {len(c['grounding_pdfs'])} file(s)")

    st.divider()
    colS, colR = st.columns([1, 1])
    with colS:
        if st.button("💾 Save project", type="primary", use_container_width=True):
            do_save()
            st.rerun()
    with colR:
        if st.session_state.get("confirm_reset"):
            st.warning("This will erase all config, OCR, and grades. Are you sure?")
            cc1, cc2 = st.columns(2)
            if cc1.button("Yes, reset everything", type="primary", use_container_width=True):
                s = state_mod.default_state()
                s["config"]["working_dir"] = state_mod.new_working_dir()
                st.session_state.state = s
                st.session_state.uploaded_ids = {}
                st.session_state.confirm_reset = False
                st.rerun()
            if cc2.button("Cancel", use_container_width=True):
                st.session_state.confirm_reset = False
                st.rerun()
        else:
            if st.button("♻️ Reset…", use_container_width=True):
                st.session_state.confirm_reset = True
                st.rerun()


# =====================================================================
# TAB 2 — OCR & SPLIT
# =====================================================================
with tab_ocr:
    c = cfg()
    st.subheader("Step 1 — OCR & split the scanned exam")
    exam_path = state_mod.abspath(c["working_dir"], c.get("exam_pdf", ""))
    roster = load_roster_safe()
    options = [""] + roster_mod.roster_options(roster)

    disabled = not (exam_path and os.path.exists(exam_path))
    if disabled:
        st.info("Upload an Exam PDF on the Config tab first.")

    if st.button("🔍 Perform OCR", type="primary", disabled=disabled):
        prog = st.progress(0.0)
        status = st.empty()

        def _cb(i, n, msg):
            prog.progress(min(1.0, (i + 1) / max(1, n)))
            status.write(msg)

        try:
            llm = get_llm()
            evals = ocr.run_ocr(llm, exam_path, roster, progress=_cb)
            get_state()["evals"] = evals
            status.write(f"Done — split into {len(evals)} submission(s).")
            do_save()
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"OCR failed: {e}")

    evals = get_state().get("evals", [])
    if evals:
        st.divider()
        st.subheader(f"Step 2 — Review {len(evals)} submission(s)")
        st.caption(
            "Assign each submission to a student (required). The dropdown is "
            "pre-filled from OCR when a confident match was found; override as needed."
        )
        unassigned = 0
        for ev in evals:
            pages = ", ".join(str(p + 1) for p in ev["page_indices"])
            detected = ev.get("detected_name") or "—"
            current = ev.get("student_key", "")
            idx = options.index(current) if current in options else 0
            col1, col2 = st.columns([2, 3])
            with col1:
                sel = st.selectbox(
                    f"**{ev['id']}** · pages {pages} · OCR name: *{detected}*",
                    options, index=idx, key=f"sel_{ev['id']}",
                    format_func=lambda x: x or "— select student —",
                )
                ev["student_key"] = sel
                if not sel:
                    unassigned += 1
                    st.markdown(":red[**Required — assign a student**]")
            with col2:
                graded = ev.get("grade")
                if graded and not graded.get("error"):
                    st.caption(f"Graded: raw {graded['raw_total']} → final {graded['final_total']}")
            with st.expander(f"OCR markdown — {ev['id']}"):
                st.markdown(ev.get("ocr_markdown") or "*(empty)*")
            st.divider()

        if unassigned:
            st.warning(f"{unassigned} submission(s) still need a student assigned before grading.")

        if st.button("💾 Save", type="primary", key="save_ocr"):
            do_save()
            st.rerun()


# =====================================================================
# TAB 3 — GRADING
# =====================================================================
with tab_grade:
    c = cfg()
    st.subheader("Step 3 — Grade")
    st.text_area(
        "Additional grading instructions (read-only — edit on Config tab)",
        value=c.get("additional_instructions", ""), height=90, disabled=True,
    )

    evals = get_state().get("evals", [])
    roster = load_roster_safe()
    assigned = [e for e in evals if e.get("student_key")]
    ready = bool(assigned)

    if not evals:
        st.info("Run OCR first (OCR & Split tab).")
    elif not ready:
        st.warning("Assign students to submissions on the OCR & Split tab first.")

    colg1, colg2 = st.columns([1, 3])
    with colg1:
        grade_click = st.button("✅ Grade all", type="primary", disabled=not ready)

    if grade_click:
        rubric_text = pdfutil.extract_text(
            state_mod.abspath(c["working_dir"], c.get("rubric_pdf", "")))
        grounding_text = "\n\n".join(
            pdfutil.extract_text(state_mod.abspath(c["working_dir"], g))
            for g in c.get("grounding_pdfs", [])
        )
        prog = st.progress(0.0)
        status = st.empty()

        def _cb(i, n, msg):
            prog.progress(min(1.0, (i + 1) / max(1, n)))
            status.write(msg)

        try:
            llm = get_llm()
            grading.grade_all(
                llm, evals, quiz_name=c.get("quiz_name", ""),
                rubric_text=rubric_text, grounding_text=grounding_text,
                additional=c.get("additional_instructions", ""),
                max_points=int(c["max_points"]), roster=roster, progress=_cb,
            )
            summary = grading.apply_curve(
                evals, max_points=int(c["max_points"]),
                min_points=int(c["min_points"]), curve_min_avg=c.get("curve_min_avg"),
            )
            get_state()["curve_summary"] = summary
            do_save()
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"Grading failed: {e}")

    # Results
    graded = [e for e in evals if e.get("grade")]
    if graded:
        summary = get_state().get("curve_summary", {})
        if summary:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Graded", summary.get("n", 0))
            m2.metric("Raw avg", f"{summary.get('raw_avg', 0):.1f}")
            m3.metric("Curve added", f"+{summary.get('added', 0)} each")
            m4.metric("Final avg", f"{summary.get('final_avg', 0):.1f}")
            if summary.get("target_reached") is False:
                st.warning(
                    f"Curve target ({c.get('curve_min_avg')}) exceeds max points "
                    f"({int(c['max_points'])}); every score is at max but the average "
                    "cannot reach the target."
                )

        rows = []
        for e in graded:
            g = e["grade"]
            rows.append({
                "Student": e.get("student_key") or e.get("detected_name") or e["id"],
                "Raw": g.get("raw_total"),
                "Curve": f"+{g.get('curve_added', 0)}",
                "Final": g.get("final_total"),
                "Status": "⚠️ error" if g.get("error") else "ok",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Per-question detail")
        for e in graded:
            g = e["grade"]
            name = e.get("student_key") or e.get("detected_name") or e["id"]
            with st.expander(f"{name} — final {g.get('final_total')}/{int(c['max_points'])}"):
                if g.get("error"):
                    st.error(g.get("overall_comment"))
                else:
                    for q in g.get("questions", []):
                        st.markdown(
                            f"**Q{q['number']}: {q['score']}/{q['max']}** — "
                            f":red[{q.get('comment', '')}]"
                        )
                    if g.get("overall_comment"):
                        st.markdown(f"**Overall:** :red[{g['overall_comment']}]")


# =====================================================================
# TAB 4 — DOWNLOAD
# =====================================================================
with tab_download:
    c = cfg()
    st.subheader("Step 4 — Download graded PDFs")
    evals = get_state().get("evals", [])
    roster = load_roster_safe()
    exam_path = state_mod.abspath(c["working_dir"], c.get("exam_pdf", ""))
    graded = [e for e in evals if e.get("grade") and not e["grade"].get("error")
              and e.get("student_key")]

    if not graded:
        st.info("Grade submissions first (Grading tab).")
    else:
        st.caption(f"{len(graded)} graded submission(s) ready. "
                   "Files are named `last_name_first_name_studentid.pdf`.")
        if st.button("🖨️ Render & build ZIP", type="primary"):
            out_dir = os.path.join(c["working_dir"], "graded")
            os.makedirs(out_dir, exist_ok=True)
            buf = io.BytesIO()
            prog = st.progress(0.0)
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, e in enumerate(graded):
                    row = roster_mod.find_row(roster, e["student_key"]) or {}
                    ln = (row.get("last_name") or "last").replace(" ", "")
                    fn = (row.get("first_name") or "first").replace(" ", "")
                    sid = row.get("student_id") or "id"
                    fname = f"{ln}_{fn}_{sid}.pdf"
                    display = e["student_key"]
                    out_path = os.path.join(out_dir, fname)
                    pdfutil.annotate_graded_pdf(
                        exam_path, e["page_indices"], e["grade"], display,
                        int(c["max_points"]), out_path,
                    )
                    zf.write(out_path, arcname=fname)
                    prog.progress((i + 1) / len(graded))
            buf.seek(0)
            st.session_state["zip_bytes"] = buf.getvalue()
            st.success(f"Rendered {len(graded)} PDF(s) into {out_dir}")

        if st.session_state.get("zip_bytes"):
            quiz = (c.get("quiz_name") or "graded").replace(" ", "_")
            st.download_button(
                "⬇️ Download all graded PDFs (ZIP)",
                data=st.session_state["zip_bytes"],
                file_name=f"{quiz}_graded.zip", mime="application/zip",
                type="primary",
            )
