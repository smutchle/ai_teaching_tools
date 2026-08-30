"""AI Grader — a Streamlit app for grading scanned paper exams with an LLM.

Run:  streamlit run app.py   (inside the `ai_grader` conda env)

Pipeline: Config -> OCR/split -> Grade (+curve) -> Download graded PDFs.
The whole project lives in one working directory under /tmp, backed by a single
state.json that can be saved/loaded at any time.
"""
from __future__ import annotations

import io
import os
import re
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


def _slug(text: str) -> str:
    """Filename-safe token: letters, digits and underscores only."""
    return re.sub(r"[^A-Za-z0-9]+", "", text or "")


def _unique_name(fname: str, used: set[str]) -> str:
    """Names alone are not unique (no student id), so de-duplicate filenames."""
    if fname not in used:
        used.add(fname)
        return fname
    stem, ext = os.path.splitext(fname)
    i = 2
    while f"{stem}_{i}{ext}" in used:
        i += 1
    out = f"{stem}_{i}{ext}"
    used.add(out)
    return out


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
                    st.session_state.ocr_page_idx = 0
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
        "**Class roster** — single-column CSV, one student per line, written "
        '**`"last_name, first_name"`** in double quotes. A `name` header row is optional.'
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
@st.cache_data(show_spinner=False)
def page_image(pdf_path: str, mtime: float, page_index: int, dpi: int = 120) -> bytes:
    """Rendered page PNG, cached. `mtime` busts the cache when the PDF changes."""
    return pdfutil.render_page_png(pdf_path, page_index, dpi=dpi)


def rebuild_evals() -> None:
    """Re-group pages into submissions after a split boundary was edited."""
    st_ = get_state()
    st_["evals"] = ocr.build_evals(st_.get("pages", []), load_roster_safe(),
                                   prior=st_.get("evals", []))


with tab_ocr:
    c = cfg()
    exam_path = state_mod.abspath(c["working_dir"], c.get("exam_pdf", ""))
    roster = load_roster_safe()
    options = [""] + roster_mod.roster_options(roster)
    has_exam = bool(exam_path and os.path.exists(exam_path))

    st.subheader("Step 1 — OCR & split the scanned exam")
    if not has_exam:
        st.info("Upload an Exam PDF on the Config tab first.")
    if not roster:
        st.info("Upload a roster CSV on the Config tab to enable student assignment.")

    if st.button("🔍 Perform OCR", type="primary", disabled=not has_exam):
        prog = st.progress(0.0)
        status = st.empty()

        def _cb(i, n, msg):
            prog.progress(min(1.0, (i + 1) / max(1, n)))
            status.write(msg)

        try:
            llm = get_llm()
            pages = ocr.ocr_pages(llm, exam_path, progress=_cb)
            st_ = get_state()
            st_["pages"] = pages
            st_["evals"] = ocr.build_evals(pages, roster)
            st.session_state.ocr_page_idx = 0
            status.write(f"Done — {len(pages)} page(s) → {len(st_['evals'])} submission(s).")
            do_save()
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"OCR failed: {e}")

    pages = get_state().get("pages", [])
    evals = get_state().get("evals", [])

    if pages:
        bad = ocr.failed_pages(pages)
        if bad:
            st.error(
                f"{len(bad)} page(s) failed to OCR: "
                + ", ".join(str(i + 1) for i in bad[:20])
                + ("…" if len(bad) > 20 else "")
                + f". First error: {pages[bad[0]]['error']}"
            )
        blank = [i for i, p in enumerate(pages) if not p.get("error") and not p.get("markdown")]
        if blank:
            st.warning(
                f"{len(blank)} page(s) came back with no transcription "
                f"({', '.join(str(i + 1) for i in blank[:20])}"
                + ("…" if len(blank) > 20 else "")
                + "). If this is most of the exam, the configured vision model is "
                "probably not multimodal — set OPENAI_VISION_MODEL to a vision model."
            )

        n_pages = len(pages)
        unassigned = [e for e in evals if not e.get("student_key")]
        st.divider()
        st.subheader(f"Step 2 — Review {n_pages} page(s) → {len(evals)} submission(s)")
        st.caption(
            "Page through the scan below. Tick **starts a new submission** on each page "
            "that carries a student's Name header to fix the split, and pick the student "
            "for the submission the current page belongs to. Every submission needs a student."
        )
        if unassigned:
            st.warning(f"{len(unassigned)} submission(s) still need a student assigned.")

        # ---- navigation ----------------------------------------------------
        st.session_state.setdefault("ocr_page_idx", 0)
        st.session_state.ocr_page_idx = min(st.session_state.ocr_page_idx, n_pages - 1)

        def _step(delta: int) -> None:
            st.session_state.ocr_page_idx = max(
                0, min(n_pages - 1, st.session_state.ocr_page_idx + delta))

        def _goto(page_index: int) -> None:
            st.session_state.ocr_page_idx = max(0, min(n_pages - 1, page_index))

        def _next_unassigned() -> None:
            cur = st.session_state.ocr_page_idx
            starts = [e["page_indices"][0] for e in evals if not e.get("student_key")]
            if starts:
                _goto(next((p for p in starts if p > cur), starts[0]))

        nav1, nav2, nav3, nav4 = st.columns([1, 1, 4, 2])
        nav1.button("◀ Prev", on_click=_step, args=(-1,), disabled=n_pages < 2,
                    use_container_width=True, key="ocr_prev")
        nav2.button("Next ▶", on_click=_step, args=(1,), disabled=n_pages < 2,
                    use_container_width=True, key="ocr_next")
        with nav3:
            if n_pages > 1:
                # Seed the widget's stored value first: a `value=` argument is
                # ignored once the key exists, so the slider would ignore Prev/
                # Next and the jump buttons.
                st.session_state["ocr_page_slider"] = st.session_state.ocr_page_idx + 1
                st.slider("Page", 1, n_pages, key="ocr_page_slider",
                          on_change=lambda: _goto(st.session_state.ocr_page_slider - 1),
                          label_visibility="collapsed")
        nav4.button("⏭ Next unassigned", on_click=_next_unassigned,
                    disabled=not unassigned, use_container_width=True,
                    key="ocr_next_unassigned")

        i = st.session_state.ocr_page_idx
        pg = pages[i]
        ev = ocr.eval_for_page(evals, i)

        img_col, ctl_col = st.columns([3, 2], gap="large")

        # ---- the page image ------------------------------------------------
        with img_col:
            st.markdown(f"**Page {i + 1} of {n_pages}**")
            try:
                st.image(page_image(exam_path, os.path.getmtime(exam_path), i),
                         use_container_width=True)
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not render page {i + 1}: {e}")

        # ---- controls for this page / its submission -----------------------
        with ctl_col:
            def _toggle_start() -> None:
                pages[st.session_state.ocr_page_idx]["is_start"] = \
                    st.session_state[f"start_{st.session_state.ocr_page_idx}"]
                rebuild_evals()

            st.session_state[f"start_{i}"] = bool(pg.get("is_start")) or i == 0
            st.checkbox(
                "📄 This page **starts a new submission**",
                key=f"start_{i}", on_change=_toggle_start,
                disabled=(i == 0),
                help="Page 1 always starts the first submission. Tick this on every "
                     "page that shows a student's Name header.",
            )
            model_said = "yes" if pg.get("is_new_submission") else "no"
            st.caption(f"Vision model said new submission: **{model_said}** · "
                       f"name read: *{pg.get('student_name') or '—'}*")

            if pg.get("error"):
                st.error(f"OCR error on this page: {pg['error']}")

            st.divider()
            if ev is None:
                st.warning("This page is not part of any submission.")
            else:
                span = ev["page_indices"]
                st.markdown(
                    f"**Submission {ev['id']}** — pages "
                    f"{span[0] + 1}–{span[-1] + 1} ({len(span)} page(s))"
                )
                st.caption(f"OCR name for this submission: *{ev.get('detected_name') or '—'}*")

                sel_key = f"sel_{ev['id']}_{span[0]}"

                def _assign(eval_id: str = ev["id"], key: str = sel_key) -> None:
                    for e in get_state().get("evals", []):
                        if e["id"] == eval_id:
                            e["student_key"] = st.session_state[key]

                current = ev.get("student_key", "")
                st.session_state[sel_key] = current if current in options else ""
                st.selectbox(
                    "Student", options, key=sel_key, on_change=_assign,
                    format_func=lambda x: x or "— select student —",
                    disabled=not roster,
                )
                if not ev.get("student_key"):
                    st.markdown(":red[**Required — assign a student**]")

                graded = ev.get("grade")
                if graded and not graded.get("error"):
                    st.success(f"Graded: raw {graded['raw_total']} → "
                               f"final {graded['final_total']}")

            with st.expander("OCR markdown — this page"):
                st.markdown(pg.get("markdown") or "*(empty)*")

        # ---- all submissions at a glance -----------------------------------
        st.divider()
        st.markdown("**All submissions** — click a row's button to jump to its first page.")
        for e in evals:
            span = e["page_indices"]
            b_col, t_col = st.columns([1, 6])
            b_col.button(f"→ p{span[0] + 1}", key=f"jump_{e['id']}",
                         on_click=_goto, args=(span[0],), use_container_width=True)
            mark = "✅" if e.get("student_key") else "⚠️"
            who = e.get("student_key") or f"*unassigned* (OCR read: {e.get('detected_name') or '—'})"
            t_col.markdown(
                f"{mark} **{e['id']}** · pages {span[0] + 1}–{span[-1] + 1} · {who}"
            )

        st.divider()
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
                   "Files are named `last_name_first_name.pdf`.")
        if st.button("🖨️ Render & build ZIP", type="primary"):
            out_dir = os.path.join(c["working_dir"], "graded")
            os.makedirs(out_dir, exist_ok=True)
            used_names: set[str] = set()
            buf = io.BytesIO()
            prog = st.progress(0.0)
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, e in enumerate(graded):
                    row = roster_mod.find_row(roster, e["student_key"]) or {}
                    ln = _slug(row.get("last_name") or e["student_key"] or "last")
                    fn = _slug(row.get("first_name") or "")
                    stem = f"{ln}_{fn}" if fn else ln
                    fname = _unique_name(f"{stem}.pdf", used_names)
                    display = roster_mod.friendly_name(row or e["student_key"])
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
