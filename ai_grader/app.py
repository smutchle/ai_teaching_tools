"""AI Grader — a Streamlit app for grading scanned paper exams with an LLM.

Run:  streamlit run app.py   (inside the `ai_grader` conda env)

One page, one button. Fill in the exam details, drop in the scan and the
rubric, press **Scan & grade everything**, and the app runs the whole pipeline —
scan & split, a consistency check, grading, curve, and the download bundle —
reporting where it is the entire time.

The run is unattended from end to end. Nothing stops it: a page that fails to
transcribe is re-read automatically and, if it still cannot be read, it comes
out as a PDF to grade by hand along with the paper it belongs to; a paper no
roster student matched is exported unassigned; roster students with no paper are
not reported at all, because a roster always holds students who did not sit the
exam. Every quiz in the scan is in the download, graded or not, assigned or not.

The Check the scan panel is still there afterwards, showing what the scan
produced page by page, for corrections worth re-grading by machine.

Everything lives in one working directory backed by a single state.json that is
checkpointed after every page and every paper.
"""
from __future__ import annotations

import os
import secrets
import time

import pandas as pd
import streamlit as st

from grader import (export, grading, ocr, pdfutil, roster as roster_mod,
                    state as state_mod)
from grader.llm import LLMClient, PermanentLLMError
from vt_banner import render_vt_banner

st.set_page_config(page_title="AI Grader", page_icon="📝", layout="wide")
render_vt_banner()

# The pipeline, in the order it runs. The stepper shows all four at all times so
# it is always clear what has happened, what is happening, and what is left.
PHASES: list[tuple[str, str]] = [
    ("scan", "Scan & split"),
    ("check", "Check the scan"),
    ("grade", "Grade"),
    ("export", "Package"),
]


# ------------------------------------------------------------------ helpers
def session_id() -> str:
    """This browser session's identity.

    `st.session_state` is per-session by construction, so a token minted into it
    is unique to one session and dies with it — which is exactly what the
    project lock needs to tell two simultaneous instructors apart.
    """
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = secrets.token_hex(8)
    return st.session_state["session_id"]


def get_state() -> dict:
    if "state" not in st.session_state:
        s = state_mod.default_state()
        wd = state_mod.new_working_dir()
        s["config"]["working_dir"] = wd
        # A directory this session just created cannot be held by anyone else.
        state_mod.claim_lock(wd, session_id())
        st.session_state.state = s
        st.session_state.uploaded_ids = {}
    return st.session_state.state


def save() -> str:
    """Persist the project and refresh this session's claim on it.

    Every checkpoint goes through here, so a long run heartbeats the lock all by
    itself and is never mistaken for an abandoned session.
    """
    path = state_mod.save_state(get_state())
    state_mod.touch_lock(cfg()["working_dir"], session_id())
    return path


def lock_conflict() -> dict | None:
    """Another live session's lock on this project, if there is one."""
    return state_mod.held_by_other(cfg()["working_dir"], session_id())


def open_project(code: str, *, force: bool = False) -> None:
    """Switch this session to another project, taking the lock with it.

    Raises ValueError for a code that names nothing openable and
    ProjectLockedError when another live session still holds it.
    """
    target = state_mod.resolve_code(code)
    if not (os.path.exists(state_mod.state_path(target))
            or os.path.exists(state_mod.backup_path(target))):
        raise ValueError(
            f"No project found with code `{state_mod.project_code(target)}`.")
    state_mod.claim_lock(target, session_id(), force=force)
    # Only let go of the old project once the new one is actually ours.
    state_mod.release_lock(cfg()["working_dir"], session_id())
    st.session_state.state = state_mod.load_state(target)
    st.session_state.uploaded_ids = {}
    st.session_state.ocr_page_idx = 0
    for k in ("zip_path", "zip_rows", "zip_failures", "run_log", "plan",
              "offer_force_open"):
        st.session_state.pop(k, None)
    st.session_state["scan_token"] = st.session_state.get("scan_token", 0) + 1


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


def exam_path() -> str:
    return state_mod.abspath(cfg()["working_dir"], cfg().get("exam_pdf", ""))


def queue(plan: dict) -> None:
    """Ask for a pipeline run on the next script pass.

    Going through a rerun means every button on the page — including the ones
    below the run panel — starts the run in the same place, with the progress
    display in view.
    """
    st.session_state["plan"] = plan
    st.rerun()


def _hms(seconds: float) -> str:
    m, s = divmod(int(max(0, seconds)), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def _plist(indices: list[int], limit: int = 20) -> str:
    """1-based page numbers, truncated."""
    out = ", ".join(str(i + 1) for i in indices[:limit])
    return out + ("…" if len(indices) > limit else "")


def rebuild_evals() -> None:
    """Re-group pages into submissions after a split boundary was edited."""
    s = get_state()
    s["evals"] = ocr.build_evals(s.get("pages", []), load_roster_safe(),
                                 prior=s.get("evals", []))


# This cache is process-wide — every session shares it — so it is bounded in
# both size and age. The key includes the project's own path, so one session
# can never be served another's page image.
@st.cache_data(show_spinner=False, max_entries=150, ttl=3600)
def page_image(pdf_path: str, mtime: float, page_index: int, dpi: int = 120) -> bytes:
    """Rendered page PNG, cached. `mtime` busts the cache when the PDF changes."""
    return pdfutil.render_page_png(pdf_path, page_index, dpi=dpi)


# ------------------------------------------------------- scan health checks
# How many times a page that failed to transcribe is re-read automatically
# before it is given up on and sent out as paper to hand grade. Transcription
# failures are usually a timeout or a throttled request, so a retry costs one
# page and usually succeeds; this is what a human used to do by hand.
AUTO_RESCAN_PASSES = 2


def scan_issues(pages: list[dict], evals: list[dict],
                roster: list[dict]) -> list[str]:
    """Things about this scan worth a human's eyes afterwards.

    Nothing here stops the run. Every one of these outcomes has an automatic
    answer - the paper is exported for hand grading, with its scanned pages and
    a cover sheet - so the list is a report on what came out, not a gate in
    front of it.

    Roster students with no submission are deliberately absent: a roster holds
    students who did not sit the exam, so that is not news.
    """
    out: list[str] = []
    if not pages:
        return ["The scan has not been run yet."]

    bad = ocr.failed_pages(pages)
    if bad:
        out.append(f"**{len(bad)} page(s) could not be read** — pages {_plist(bad)}, "
                   "each re-read automatically before being given up on. They are "
                   "exported under `unreadable_pages/`, and the paper each one "
                   "belongs to went out for hand grading (under `needs_grading/`, or "
                   "`unassigned/` if no student matched it) rather than being scored "
                   "off an incomplete transcription.")

    unsure = ocr.unconfident_pages(pages)
    if unsure:
        out.append(f"**{len(unsure)} page(s) have a guessed split boundary** — pages "
                   f"{_plist(unsure)}. Each one started a new submission, so the "
                   "papers around them may be split in the wrong place.")

    blank = ocr.empty_pages(pages)
    if len(blank) >= max(1, len(pages) // 2):
        out.append(f"**{len(blank)} of {len(pages)} page(s) transcribed to nothing.** "
                   "The configured vision model is probably not multimodal — "
                   "OPENAI_VISION_MODEL should be `vt-arc-llm`.")
    elif blank:
        out.append(f"{len(blank)} page(s) transcribed to nothing (pages "
                   f"{_plist(blank)}) — usually a blank back page.")

    if not evals:
        out.append("**The scan produced no submissions at all.**")

    unassigned = [e for e in evals if not e.get("student_key")]
    if unassigned:
        out.append(f"{len(unassigned)} submission(s) have no student assigned. They "
                   "are not graded, but they are exported under `unassigned/` with "
                   "the name the scan read — no paper is dropped.")

    hand = [e for e in evals if e.get("student_key") and grading.hand_grading_reason(e)]
    if hand:
        out.append(f"{len(hand)} assigned paper(s) could not be machine-graded and "
                   "are exported under `needs_grading/` with a blank score line.")
    return out


def problem_pages(pages: list[dict], evals: list[dict]) -> list[int]:
    """Every page worth a human's eyes: unread, guessed boundary, or the first
    page of a submission with nobody assigned to it."""
    flagged = set(ocr.failed_pages(pages)) | set(ocr.unconfident_pages(pages))
    flagged |= {e["page_indices"][0] for e in evals
                if not e.get("student_key") and e.get("page_indices")}
    return sorted(flagged)


# ---------------------------------------------------------------- run panel
class RunUI:
    """The live progress display: phase stepper, bar, status line and log.

    Every phase is visible from the first frame, so at no point is the app doing
    something the user cannot name. The log is also kept in session state, which
    is what keeps a finished run readable after Streamlit reruns the script.
    """

    def __init__(self, box) -> None:
        box.markdown("#### Progress")
        self._strip = box.empty()
        self._bar_slot = box.empty()
        self._line = box.empty()
        self._log_slot = box.expander("📋 Run log", expanded=False).empty()
        self.done: list[str] = []
        self.skipped: list[str] = []
        self.current: str = ""
        self.failed: str = ""
        self.log: list[str] = []
        self._bar = None
        self._t0 = time.time()
        self._phase_t0 = time.time()
        self._render_strip()

    # -- rendering -------------------------------------------------------
    def _render_strip(self) -> None:
        parts = []
        for n, (key, label) in enumerate(PHASES, start=1):
            if key == self.failed:
                parts.append(f"❌ ~~{n}. {label}~~")
            elif key in self.skipped:
                parts.append(f"⏭️ {n}. {label}")
            elif key in self.done:
                parts.append(f"✅ {n}. {label}")
            elif key == self.current:
                parts.append(f"🔄 **{n}. {label}**")
            else:
                parts.append(f"⚪ {n}. {label}")
        self._strip.markdown("  &nbsp;·&nbsp;  ".join(parts))

    def say(self, msg: str) -> None:
        self.log.append(f"[{_hms(time.time() - self._t0):>7}] {msg}")
        self._log_slot.code("\n".join(self.log[-300:]), language=None)
        st.session_state["run_log"] = self.log

    # -- phase transitions -----------------------------------------------
    def phase(self, key: str, msg: str) -> None:
        self.current = key
        self._phase_t0 = time.time()
        self._render_strip()
        self._bar = self._bar_slot.progress(0.0, text="starting…")
        self._line.info(f"**{dict(PHASES)[key]}** — {msg}")
        self.say(f"▶ {dict(PHASES)[key]}: {msg}")

    def step(self, i: int, n: int, msg: str) -> None:
        """Progress callback shape used by ocr/grading/export: (index, total, msg)."""
        n = max(1, n)
        done = min(n, i + 1)
        frac = done / n
        elapsed = time.time() - self._phase_t0
        eta = (elapsed / done) * (n - done) if done else 0.0
        if self._bar is not None:
            self._bar.progress(frac, text=f"{done} of {n}  ·  {int(frac * 100)}%")
        self._line.info(
            f"**{dict(PHASES).get(self.current, '')}** — {msg}  \n"
            f"⏱ {_hms(elapsed)} elapsed · about {_hms(eta)} left"
        )
        self.say(msg)

    def complete(self, key: str, msg: str) -> None:
        self.done.append(key)
        self.current = ""
        self._render_strip()
        if self._bar is not None:
            self._bar.progress(1.0, text="done")
        self._line.success(f"**{dict(PHASES)[key]}** — {msg}")
        self.say(f"✔ {dict(PHASES)[key]}: {msg}")

    def skip(self, key: str, msg: str) -> None:
        self.skipped.append(key)
        self._render_strip()
        self.say(f"⏭ {dict(PHASES)[key]}: {msg}")

    def halt(self, key: str, msg: str) -> None:
        """Stop the run at `key`, without pretending the phase succeeded."""
        self.failed = key
        self.current = ""
        self._render_strip()
        self._bar_slot.empty()
        self._line.error(msg)
        self.say(f"■ {dict(PHASES)[key]}: {msg}")

    def finish(self, msg: str) -> None:
        self._bar_slot.empty()
        self._line.success(f"🎉 {msg}  ·  total {_hms(time.time() - self._t0)}")
        self.say(f"🎉 {msg}")


# ----------------------------------------------------------------- pipeline
def run_pipeline(plan: dict, ui: RunUI) -> None:
    """Run scan → check → grade → package, start to finish, without stopping.

    `plan` keys: ``scan`` ("all" | "failed" | None), ``grade`` ("all" | True |
    None), ``export`` (bool).

    Nothing in a scan stops this. A page that fails is re-read automatically and,
    if it still cannot be read, the paper containing it goes to hand grading; a
    submission nobody matched to a roster student is exported unassigned; a
    grading request that dies takes its own paper with it and no other. The run
    always ends at the package step, so every quiz in the scan comes out the far
    end whether or not it could be graded or assigned.
    """
    s = get_state()
    c = s["config"]
    roster = load_roster_safe()
    path = exam_path()

    try:
        llm = get_llm() if (plan.get("scan") or plan.get("grade")) else None
    except RuntimeError as e:
        ui.halt(PHASES[0][0] if plan.get("scan") else "grade",
                f"⛔ {e}  \nSet the API key in the sidebar under **API key** — set "
                "one up for free at https://llm.arc.vt.edu — or put OPENAI_APIKEY "
                "in the app's .env file.")
        return

    # ------------------------------------------------------------ 1. scan
    if plan.get("scan"):
        failed_only = plan["scan"] == "failed"
        prior = s.get("pages", [])
        retry = ocr.failed_pages(prior) if failed_only else None
        n_pages = pdfutil.page_count(path) if os.path.exists(path) else 0
        ui.phase("scan", (f"re-reading {len(retry or [])} failed page(s)" if failed_only
                          else f"reading {n_pages} page(s) with the vision model"))

        def _checkpoint(pages_so_far: list[dict]) -> None:
            """Persist after every page: an interrupted scan is never paid for twice."""
            s["pages"] = pages_so_far
            save()

        pages = list(prior)
        misconfigured = False
        try:
            pages = ocr.ocr_pages(
                llm, path, progress=ui.step, on_page=_checkpoint,
                pages=prior if failed_only else None,
                indices=retry if failed_only else None,
            )
        except PermanentLLMError as e:
            # A bad model name or key, not a bad page. Retrying cannot help, so
            # the only question is whether anything was read before it showed up.
            misconfigured = True
            pages = s.get("pages") or pages
            if not pages:
                ui.halt("scan", f"⛔ {e}\n\nNothing was scanned, so nothing was "
                                "charged for. Fix the model configuration and run "
                                "again.")
                return
            ui.say(f"⚠ Scan stopped: {e}. Continuing with the pages already read — "
                   "the rest go out as pages to grade by hand.")
        except Exception as e:  # noqa: BLE001 - keep whatever pages did land
            pages = s.get("pages") or pages
            if not pages:
                ui.halt("scan", f"Scan failed: {e}")
                return
            ui.say(f"⚠ Scan stopped early: {e}. Every page read before the failure "
                   "was kept; the rest go out as pages to grade by hand.")

        # Re-read the pages that failed, automatically. A transcription failure is
        # usually a timeout or a throttled request, and retrying one page is far
        # cheaper than the paper it would otherwise send to hand grading.
        # ...unless the model itself is misconfigured, in which case every retry
        # would fail the same way.
        for attempt in range(1, (0 if misconfigured else AUTO_RESCAN_PASSES) + 1):
            bad = ocr.failed_pages(pages)
            if not bad:
                break
            ui.phase("scan", f"re-reading {len(bad)} page(s) that failed "
                             f"(automatic pass {attempt} of {AUTO_RESCAN_PASSES})")
            try:
                pages = ocr.ocr_pages(llm, path, progress=ui.step,
                                      on_page=_checkpoint, pages=pages, indices=bad)
            except Exception as e:  # noqa: BLE001 - a failed retry is not a failed run
                ui.say(f"⚠ The re-read pass stopped: {e}. Pages still unread go out "
                       "as paper to grade by hand.")
                break

        s["pages"] = pages
        s["evals"] = ocr.build_evals(
            pages, roster, prior=s.get("evals", []) if failed_only else None)
        save()
        st.session_state["scan_token"] = st.session_state.get("scan_token", 0) + 1
        # Open the review on the first page that needs looking at, not page 1.
        trouble = problem_pages(pages, s["evals"])
        st.session_state["ocr_page_idx"] = trouble[0] if trouble else 0
        still_bad = ocr.failed_pages(pages)
        ui.complete("scan", f"{len(pages)} page(s) → {len(s['evals'])} submission(s)"
                            + (f"; {len(still_bad)} page(s) still unread after "
                               f"{AUTO_RESCAN_PASSES} retr(ies) — exported to grade "
                               "by hand." if still_bad else "; every page was read."))
    else:
        ui.skip("scan", "using the existing scan")

    pages = s.get("pages", [])
    evals = s.get("evals", [])

    # ----------------------------------------------------------- 2. check
    # A report, not a gate. Everything it can find has an automatic answer, and
    # taking that answer beats stopping a batch of 40 papers on page 12.
    if plan.get("grade") or plan.get("export"):
        ui.phase("check", "accounting for every page and every submission")
        issues = scan_issues(pages, evals, roster)
        for msg in issues:
            ui.say("• " + msg.replace("**", ""))
        assigned = sum(1 for e in evals if e.get("student_key"))
        ui.complete("check", f"{len(pages)} page(s), {len(evals)} submission(s), "
                             f"{assigned} with a student."
                    + (f" {len(issues)} thing(s) to look at afterwards — none of "
                       "them stop the run; see section 4."
                       if issues else " Every check passed."))
    else:
        ui.skip("check", "nothing to check in this run")

    # ----------------------------------------------------------- 3. grade
    if plan.get("grade"):
        regrade = plan["grade"] == "all"
        todo = [e for e in evals if e.get("student_key")
                and not grading.hand_grading_reason(e)
                and (regrade or grading.needs_grading(e))]
        ui.phase("grade", f"grading {len(todo)} paper(s) against the rubric")

        rubric_text = pdfutil.extract_text(
            state_mod.abspath(c["working_dir"], c.get("rubric_pdf", "")))
        grounding_text = "\n\n".join(
            pdfutil.extract_text(state_mod.abspath(c["working_dir"], g))
            for g in c.get("grounding_pdfs", []))

        def _on_result(_ev: dict) -> None:
            """Persist after every paper. A run that dies keeps every grade it
            already finished."""
            save()

        result = None
        grade_error = ""
        try:
            result = grading.grade_all(
                llm, evals, quiz_name=c.get("quiz_name", ""),
                rubric_text=rubric_text, grounding_text=grounding_text,
                additional=c.get("additional_instructions", ""),
                max_points=int(c["max_points"]), roster=roster,
                only_ungraded=not regrade, on_result=_on_result, progress=ui.step,
            )
        except Exception as e:  # noqa: BLE001 - keep every grade that did land
            grade_error = str(e)

        # Curve and save regardless, so an aborted run still leaves usable state.
        summary = grading.apply_curve(
            evals, max_points=int(c["max_points"]),
            min_points=int(c["min_points"]), curve_min_avg=c.get("curve_min_avg"))
        s["curve_summary"] = summary
        save()

        if grade_error:
            # Not a halt: the papers that were graded are graded, and the ones
            # that were not are exported for hand grading below.
            ui.say(f"⚠ Grading stopped early: {grade_error}. Grades finished before "
                   "the failure were kept; the rest are packaged for hand grading. "
                   "**Grade the remaining papers** picks up where this left off.")
            ui.complete("grade", f"stopped early ({grade_error}) — finished grades "
                                 "kept, the rest go out for hand grading.")
        else:
            ui.complete("grade", f"{result['ok']} graded, {result['failed']} failed, "
                                 f"{result.get('hand', 0)} sent to hand grading, "
                                 f"{result['skipped']} already had a grade."
                        if result else "nothing needed grading.")
    else:
        ui.skip("grade", "not part of this run")

    # ---------------------------------------------------------- 4. package
    if plan.get("export"):
        ui.phase("export", f"rendering {len(evals)} PDF(s) and building the ZIP")
        if not os.path.exists(path):
            ui.halt("export", f"Exam PDF not found at {path}. Re-upload it above.")
            return
        try:
            bundle = export.build_bundle(
                path, evals, roster, max_points=int(c["max_points"]),
                scan_pages=pages, progress=ui.step,
                out_dir=os.path.join(c["working_dir"], "graded"))
        except Exception as e:  # noqa: BLE001
            ui.halt("export", f"Export failed: {e}")
            return
        # Written to the project directory rather than held in session memory:
        # with several instructors on one server, a class-sized ZIP per session
        # is real memory that never gets released.
        zip_path = os.path.join(c["working_dir"], "submissions.zip")
        with open(zip_path, "wb") as f:
            f.write(bundle["zip_bytes"])
        st.session_state["zip_path"] = zip_path
        st.session_state["zip_rows"] = bundle["rows"]
        st.session_state["zip_failures"] = bundle["failures"]
        counts = bundle["counts"]
        unread = bundle["reconciliation"]["unreadable_pages"]
        ui.complete("export", f"{len(bundle['rows'])} PDF(s) — "
                              f"{counts[export.GRADED]} graded, "
                              f"{counts[export.NEEDS_GRADING]} for hand grading, "
                              f"{counts[export.UNASSIGNED]} unassigned"
                              + (f", plus {len(unread)} unreadable page(s)."
                                 if unread else "."))
    else:
        ui.skip("export", "not part of this run")

    ui.finish("All done — the download is at the bottom of the page.")


# ------------------------------------------------------------------ sidebar
state = get_state()
c = cfg()

with st.sidebar:
    st.markdown("### 📁 Project")
    # The project code is what reopens this work later, and the only thing that
    # can: it names a directory inside the workspace root and nothing else on
    # the server is reachable from this box.
    st.text_input("Your project code", value=state_mod.project_code(c["working_dir"]),
                  disabled=True, key="my_code",
                  help="Copy this if you want to reopen this project later or "
                       "from another browser. It is the only way back to it.")

    open_code = st.text_input(
        "Open another project", value="", key="open_code",
        placeholder="paste a project code",
        help="Projects are private to their code. Opening one takes it over for "
             "this session.")
    sc1, sc2 = st.columns(2)
    if sc1.button("📂 Open", width="stretch", disabled=not open_code.strip()):
        try:
            open_project(open_code)
            st.rerun()
        except state_mod.ProjectLockedError as e:
            st.session_state["offer_force_open"] = open_code.strip()
            st.error(str(e))
        except (ValueError, OSError) as e:
            st.session_state.pop("offer_force_open", None)
            st.error(str(e))
    if sc2.button("💾 Save", width="stretch"):
        save()
        st.success("Saved.")

    if st.session_state.get("offer_force_open"):
        if st.button("⚠️ Take it over anyway", width="stretch",
                     help="Use this only if you know the other session is gone. "
                          "The session holding it will be locked out, and whichever "
                          "of you saves last wins."):
            try:
                open_project(st.session_state["offer_force_open"], force=True)
                st.rerun()
            except (ValueError, OSError) as e:
                st.error(str(e))

    if st.session_state.get("confirm_reset"):
        st.warning("Erase all settings, scan results and grades?")
        rc1, rc2 = st.columns(2)
        if rc1.button("Yes, reset", type="primary", width="stretch"):
            state_mod.release_lock(c["working_dir"], session_id())
            s_new = state_mod.default_state()
            wd_new = state_mod.new_working_dir()
            s_new["config"]["working_dir"] = wd_new
            state_mod.claim_lock(wd_new, session_id())
            st.session_state.state = s_new
            st.session_state.uploaded_ids = {}
            for k in ("zip_path", "zip_rows", "zip_failures", "run_log", "plan"):
                st.session_state.pop(k, None)
            st.session_state.confirm_reset = False
            st.rerun()
        if rc2.button("Cancel", width="stretch"):
            st.session_state.confirm_reset = False
            st.rerun()
    elif st.button("♻️ Start a new project…", width="stretch"):
        st.session_state.confirm_reset = True
        st.rerun()

    with st.expander("Where this is stored"):
        st.caption(f"`{c['working_dir']}`")
        st.caption("Each project is a separate directory and only one session at "
                   "a time may write to it.")

    st.divider()
    # Optional, and kept out of the way: the app reads the key from .env and
    # almost nobody needs to type one here.
    with st.expander("🔑 API key — optional", expanded=False):
        st.caption(
            "**You do not need to enter anything here.** The app already reads "
            "`OPENAI_APIKEY` from its `.env` file.")
        st.caption(
            "You can set up your own key at "
            "[llm.arc.vt.edu](https://llm.arc.vt.edu) and paste it below. "
            "**It costs nothing** — each key is limited in how much it can be "
            "used, so bringing your own leaves room on the shared key for "
            "everyone else.")
        c["api_key_override"] = st.text_input(
            "ARC API key override", value=c.get("api_key_override", ""),
            type="password", placeholder="leave blank to use .env",
            label_visibility="collapsed")
        if c.get("api_key_override"):
            st.info("Using the key typed here instead of the one in .env.")
        elif os.getenv("OPENAI_APIKEY"):
            st.success("Using the key from .env ✓")
        else:
            st.warning("No key in .env — get one free at https://llm.arc.vt.edu "
                       "and enter it here to run the app.")

    st.divider()
    st.markdown("### 📊 Status")
    _pages, _evals = state.get("pages", []), state.get("evals", [])
    _graded = sum(1 for e in _evals if export.status_of(e) == export.GRADED)
    st.markdown(
        f"- Scanned pages: **{len(_pages)}**\n"
        f"- Submissions: **{len(_evals)}**\n"
        f"- Graded: **{_graded}**\n"
        f"- Download ready: "
        f"**{'yes' if st.session_state.get('zip_path') else 'no'}**")

    # The proxy's concurrency cap is per API key, so everyone on the same key
    # shares one budget. Showing it turns "why is my scan slow" into something
    # visible rather than mysterious.
    try:
        _in_use, _waiting, _limit = get_llm().gate_stats()
    except Exception:  # noqa: BLE001 - no key configured yet; nothing to report
        pass
    else:
        st.caption(f"LLM requests in flight: **{_in_use}/{_limit}**"
                   + (f" · **{_waiting}** queued" if _waiting else "")
                   + "  \nShared with any other session using the same API key.")


# ------------------------------------------------------------------- header
st.title("📝 AI Grader")
st.caption("Fill in the exam below, add the scan and the rubric, then press one "
           "button. The app scans, splits, grades, curves and packages everything "
           "in one unattended run — anything it cannot read, grade or assign comes "
           "out as a PDF to grade by hand.")

roster = load_roster_safe()
pages = state.get("pages", [])
evals = state.get("evals", [])

# If another session took this project over, stop before anything writes to it:
# two sessions saving the same state.json lose each other's pages and grades.
conflict = lock_conflict()
if conflict:
    st.error(
        f"🔒 **Another session has taken over this project** (code "
        f"`{state_mod.project_code(c['working_dir'])}`, active "
        f"{int(time.time() - float(conflict.get('heartbeat', 0)))}s ago). This "
        "session can still look, but running or saving here would overwrite that "
        "session's work.\n\n"
        "Start a new project in the sidebar, or reopen this code to take it back.")


# ==================================================================== SETUP
st.markdown("### 1 · The exam")
with st.container(border=True):
    c["quiz_name"] = st.text_input(
        "Quiz / exam name", value=c.get("quiz_name", ""),
        placeholder="e.g. Quiz 3 — Regression")

    g1, g2, g3, g4 = st.columns([1, 1, 1, 2])
    with g1:
        c["max_points"] = st.number_input(
            "Max points", value=int(c.get("max_points", 100)), step=1, min_value=1)
    with g2:
        c["min_points"] = st.number_input(
            "Min points", value=int(c.get("min_points", 0)), step=1)
    with g3:
        curve_on = st.checkbox("Apply a curve",
                               value=c.get("curve_min_avg") is not None)
    with g4:
        curve_val = st.number_input(
            "Curve to this minimum class average", step=1, disabled=not curve_on,
            value=int(c["curve_min_avg"]) if c.get("curve_min_avg") is not None else 90)
    c["curve_min_avg"] = int(curve_val) if curve_on else None

    c["additional_instructions"] = st.text_area(
        "Extra instructions for the grader (optional)",
        value=c.get("additional_instructions",
                    state_mod.DEFAULT_ADDITIONAL_INSTRUCTIONS),
        height=80,
        help="Read alongside the rubric. The rubric's point caps always win.")


# ==================================================================== FILES
st.markdown("### 2 · The files")
with st.container(border=True):
    f1, f2 = st.columns(2)

    with f1:
        st.markdown("**Class roster** — required")
        st.caption('Single-column CSV, one student per line, written '
                   '`"last name, first name"` in double quotes. A `name` header '
                   'row is optional.')
        roster_up = st.file_uploader("Roster CSV", type=["csv"], key="roster_up",
                                     label_visibility="collapsed")
        if roster_up is not None and \
                st.session_state.uploaded_ids.get("roster") != roster_up.file_id:
            c["roster_csv"] = save_uploaded(roster_up, "roster.csv")
            st.session_state.uploaded_ids["roster"] = roster_up.file_id
            roster = load_roster_safe()
        if c.get("roster_csv"):
            st.success(f"✓ {len(roster)} student(s) — `{c['roster_csv']}`")

        st.markdown("**Rubric & answer key** — recommended")
        st.caption("One PDF. Without it the grader has no point allocation to "
                   "work from.")
        rubric_up = st.file_uploader("Rubric PDF", type=["pdf"], key="rubric_up",
                                     label_visibility="collapsed")
        if rubric_up is not None and \
                st.session_state.uploaded_ids.get("rubric") != rubric_up.file_id:
            c["rubric_pdf"] = save_uploaded(rubric_up, "rubric.pdf")
            st.session_state.uploaded_ids["rubric"] = rubric_up.file_id
        if c.get("rubric_pdf"):
            st.success(f"✓ `{c['rubric_pdf']}`")

    with f2:
        st.markdown("**Exam scan** — required")
        st.caption("One PDF holding every student's paper, back to back.")
        exam_up = st.file_uploader("Exam PDF", type=["pdf"], key="exam_up",
                                   label_visibility="collapsed")
        if exam_up is not None and \
                st.session_state.uploaded_ids.get("exam") != exam_up.file_id:
            c["exam_pdf"] = save_uploaded(exam_up, "exam.pdf")
            st.session_state.uploaded_ids["exam"] = exam_up.file_id
        if c.get("exam_pdf") and os.path.exists(exam_path()):
            st.success(f"✓ {pdfutil.page_count(exam_path())} page(s) — "
                       f"`{c['exam_pdf']}`")

        st.markdown("**Course materials** — optional")
        st.caption("One or more PDFs the grader must treat as the source of "
                   "truth for what is correct.")
        ground_up = st.file_uploader("Grounding PDFs", type=["pdf"],
                                     accept_multiple_files=True, key="ground_up",
                                     label_visibility="collapsed")
        if ground_up:
            ids = tuple(f.file_id for f in ground_up)
            if st.session_state.uploaded_ids.get("ground") != ids:
                c["grounding_pdfs"] = [save_uploaded(f, f"grounding_{i}.pdf")
                                       for i, f in enumerate(ground_up, start=1)]
                st.session_state.uploaded_ids["ground"] = ids
        if c.get("grounding_pdfs"):
            st.success(f"✓ {len(c['grounding_pdfs'])} file(s)")


# ====================================================================== RUN
st.markdown("### 3 · Run it")
missing: list[str] = []
if conflict:
    missing.append("sole access to this project (another session has it open)")
if not (c.get("exam_pdf") and os.path.exists(exam_path())):
    missing.append("the exam scan PDF")
if not roster:
    missing.append("the class roster CSV")

with st.container(border=True):
    if missing:
        st.warning("Still needed above: " + ", ".join(missing) + ".")
    elif not c.get("rubric_pdf"):
        st.info("No rubric uploaded. Grading will run, but the model has no answer "
                "key to score against — add one above for usable grades.")

    if st.button("🚀  Scan & grade everything", type="primary",
                 width="stretch", disabled=bool(missing)):
        queue({"scan": "all", "grade": True, "export": True})
    st.caption("Scans and splits the exam → accounts for every page → grades "
               "every paper → applies the curve → builds the download. It runs "
               "start to finish without stopping: a page that cannot be read is "
               "re-read automatically and then exported as paper to grade by "
               "hand, and every quiz comes out in the download whether or not it "
               "could be graded or assigned.")

    if pages:
        with st.expander("Run only part of it"):
            b1, b2, b3, b4 = st.columns(4)
            bad = ocr.failed_pages(pages)
            pending = [e for e in evals if grading.needs_grading(e)]
            if b1.button(f"🔁 Re-scan {len(bad)} failed page(s)", disabled=not bad,
                         width="stretch",
                         help="Re-reads only the pages that errored, keeping every "
                              "page that already worked."):
                queue({"scan": "failed", "grade": True, "export": True})
            if b2.button(f"✅ Grade {len(pending)} remaining", disabled=not pending,
                         width="stretch",
                         help="Grades only papers with no grade yet, plus any that "
                              "errored."):
                queue({"grade": True, "export": True})
            if b3.button("♻️ Re-grade everything",
                         disabled=not any(e.get("student_key") for e in evals),
                         width="stretch",
                         help="Throws away existing grades and grades every "
                              "assigned paper again."):
                queue({"grade": "all", "export": True})
            if b4.button("📦 Rebuild the download", disabled=not evals,
                         width="stretch",
                         help="Re-renders the PDFs and the ZIP from the current "
                              "grades and assignments."):
                queue({"export": True})

    # The pipeline runs here, directly under the button that asked for it.
    plan = st.session_state.pop("plan", None)
    if plan:
        run_pipeline(plan, RunUI(st.container(border=True)))
        # State moved underneath the rest of the page; re-read it.
        pages = state.get("pages", [])
        evals = state.get("evals", [])
    elif st.session_state.get("run_log"):
        with st.expander("📋 Last run log"):
            st.code("\n".join(st.session_state["run_log"]), language=None)


# ============================================================ REVIEW / FIX
if pages:
    # Recomputed every pass, so the panel reflects the corrections made inside
    # it rather than the state of the world when the run finished.
    issues = scan_issues(pages, evals, roster)
    st.markdown("### 4 · Check the scan")

    with st.container(border=True):
        if issues:
            st.info("**The run finished.** Everything below already has an answer "
                    "in the download — unread pages, ungraded papers and "
                    "unassigned papers are all exported as PDFs to grade by hand. "
                    "Correct anything you want graded by machine instead, then "
                    "re-grade.")
            for msg in issues:
                st.markdown(f"- {msg}")
        else:
            st.success("Every page was read, split and assigned — nothing needs "
                       "your attention.")

        # The scan's own output, all in one place: being told a page failed is
        # not the same as being shown what came back for it.
        bad = ocr.failed_pages(pages)
        if bad:
            with st.expander(f"🔬 What the scan returned for the {len(bad)} failed "
                             "page(s)", expanded=True):
                st.caption("These were re-read automatically and still failed. They "
                           "are in the download under `unreadable_pages/`, and the "
                           "paper each one belongs to is under `needs_grading/`.")
                for idx in bad[:20]:
                    st.markdown(f"**Page {idx + 1}** — {pages[idx].get('error')}")
                    raw = pages[idx].get("raw")
                    if raw:
                        st.code(raw, language=None)
                    else:
                        st.caption("The model returned nothing at all for this page.")
                if len(bad) > 20:
                    st.caption(f"…and {len(bad) - 20} more; page through the scan "
                               "below to see them.")

        cc1, cc2, cc3 = st.columns(3)
        if cc1.button(f"🔁 Re-scan {len(bad)} failed page(s), then grade",
                      disabled=not bad, width="stretch",
                      help="Tries the unread pages again. They were already retried "
                           "automatically, so this is worth pressing only after the "
                           "proxy or the model configuration has changed."):
            queue({"scan": "failed", "grade": True, "export": True})
        if cc2.button("▶️ Grade & package again", width="stretch",
                      help="Grades every paper that has a student and a full "
                           "transcription, then rebuilds the download — the way to "
                           "pick up corrections made below."):
            queue({"grade": True, "export": True})
        if cc3.button("💾 Save my corrections", width="stretch"):
            save()
            st.success("Saved.")

    n_pages = len(pages)
    st.markdown("#### What the scan produced")
    st.caption("Page through the scan. Tick **starts a new submission** on every "
               "page carrying a student's Name header to fix the split, correct "
               "the transcription where the model got it wrong, and pick the "
               "student for each submission.")

    st.session_state.setdefault("ocr_page_idx", 0)
    st.session_state.ocr_page_idx = min(st.session_state.ocr_page_idx, n_pages - 1)
    scan_token = st.session_state.get("scan_token", 0)

    def _step(delta: int) -> None:
        st.session_state.ocr_page_idx = max(
            0, min(n_pages - 1, st.session_state.ocr_page_idx + delta))

    def _goto(page_index: int) -> None:
        st.session_state.ocr_page_idx = max(0, min(n_pages - 1, page_index))

    problems = problem_pages(pages, evals)

    def _next_problem() -> None:
        """Jump to the next page that actually needs attention."""
        cur = st.session_state.ocr_page_idx
        if problems:
            _goto(next((p for p in problems if p > cur), problems[0]))
    nav1, nav2, nav3, nav4 = st.columns([1, 1, 4, 2])
    nav1.button("◀ Prev", on_click=_step, args=(-1,), disabled=n_pages < 2,
                width="stretch", key="ocr_prev")
    nav2.button("Next ▶", on_click=_step, args=(1,), disabled=n_pages < 2,
                width="stretch", key="ocr_next")
    with nav3:
        if n_pages > 1:
            # Seed the widget's stored value first: a `value=` argument is
            # ignored once the key exists, so the slider would ignore Prev/Next
            # and the jump buttons.
            st.session_state["ocr_page_slider"] = st.session_state.ocr_page_idx + 1
            st.slider("Page", 1, n_pages, key="ocr_page_slider",
                      on_change=lambda: _goto(st.session_state.ocr_page_slider - 1),
                      label_visibility="collapsed")
    nav4.button(f"⏭ Next problem ({len(problems)})", on_click=_next_problem,
                disabled=not problems, width="stretch",
                key="ocr_next_problem")

    i = st.session_state.ocr_page_idx
    pg = pages[i]
    ev = ocr.eval_for_page(evals, i)

    img_col, ctl_col = st.columns([3, 2], gap="large")

    with img_col:
        st.markdown(f"**Page {i + 1} of {n_pages}**")
        try:
            st.image(page_image(exam_path(), os.path.getmtime(exam_path()), i),
                     width="stretch")
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not render page {i + 1}: {e}")

    with ctl_col:
        if pg.get("error"):
            st.error(f"**This page did not scan.** {pg['error']}")
        elif not pg.get("markdown"):
            st.warning("This page scanned without error but produced no text.")

        def _toggle_start() -> None:
            get_state()["pages"][st.session_state.ocr_page_idx]["is_start"] = \
                st.session_state[f"start_{st.session_state.ocr_page_idx}"]
            rebuild_evals()

        st.session_state[f"start_{i}"] = bool(pg.get("is_start")) or i == 0
        st.checkbox("📄 This page **starts a new submission**", key=f"start_{i}",
                    on_change=_toggle_start, disabled=(i == 0),
                    help="Page 1 always starts the first submission. Tick this on "
                         "every page that shows a student's Name header.")
        st.caption(f"Vision model said new submission: "
                   f"**{'yes' if pg.get('is_new_submission') else 'no'}** · "
                   f"name read: *{pg.get('student_name') or '—'}*"
                   + ("" if pg.get("boundary_confident", True)
                      else " · ⚠️ boundary is a guess"))

        st.divider()
        if ev is None:
            st.warning("This page is not part of any submission.")
        else:
            span = ev["page_indices"]
            st.markdown(f"**Submission {ev['id']}** — pages "
                        f"{span[0] + 1}–{span[-1] + 1} ({len(span)} page(s))")
            st.caption(f"Name read from the scan: *{ev.get('detected_name') or '—'}*")

            sel_key = f"sel_{ev['id']}_{span[0]}"

            def _assign(eval_id: str = ev["id"], key: str = sel_key) -> None:
                for e in get_state().get("evals", []):
                    if e["id"] == eval_id:
                        e["student_key"] = st.session_state[key]

            options = [""] + roster_mod.roster_options(roster)
            current = ev.get("student_key", "")
            st.session_state[sel_key] = current if current in options else ""
            st.selectbox("Student", options, key=sel_key, on_change=_assign,
                         format_func=lambda x: x or "— select student —",
                         disabled=not roster)
            if not ev.get("student_key"):
                st.markdown(":red[**Required — assign a student**]")

            graded = ev.get("grade")
            if graded and not graded.get("error"):
                st.success(f"Graded: raw {graded['raw_total']} → "
                           f"final {graded['final_total']}")

        st.divider()
        st.markdown("**Transcription for this page** — edit it if the model got it "
                    "wrong; what you type here is what gets graded.")
        new_md = st.text_area(
            "Transcription", value=pg.get("markdown", ""), height=260,
            key=f"md_{i}_{scan_token}", label_visibility="collapsed",
            placeholder="(the scan produced no text for this page)")
        if st.button("💾 Apply this transcription", key=f"apply_md_{i}",
                     disabled=new_md == pg.get("markdown", "")):
            s_pg = get_state()["pages"][i]
            s_pg["markdown"] = new_md
            if new_md.strip() and s_pg.get("error"):
                # A page the instructor has transcribed by hand is no longer an
                # unread page, and the paper containing it is gradable again.
                s_pg["error"] = ""
                s_pg["boundary_confident"] = True
                s_pg["hand_edited"] = True
            rebuild_evals()
            save()
            st.rerun()

        if pg.get("raw"):
            with st.expander("🔬 What the model actually replied for this page"):
                st.caption("The raw reply, before any JSON parsing. If it says "
                           "something like “I'm unable to view the image”, the "
                           "configured vision model is not multimodal.")
                st.code(pg["raw"], language=None)

    st.divider()
    st.markdown("**Every submission** — click a row to jump to its first page.")
    for e in evals:
        span = e["page_indices"]
        b_col, t_col = st.columns([1, 6])
        b_col.button(f"→ p{span[0] + 1}", key=f"jump_{e['id']}",
                     on_click=_goto, args=(span[0],), width="stretch")
        mark = "✅" if e.get("student_key") else "⚠️"
        who = e.get("student_key") or \
            f"*unassigned* (scan read: {e.get('detected_name') or '—'})"
        flags = []
        if e.get("failed_pages"):
            flags.append(f"❌ unread pages {_plist(e['failed_pages'])}")
        if e.get("unconfident_pages"):
            flags.append("⚠️ guessed boundary")
        t_col.markdown(f"{mark} **{e['id']}** · pages {span[0] + 1}–{span[-1] + 1} · "
                       f"{who}" + ("  \n" + " · ".join(flags) if flags else ""))


# ================================================================== RESULTS
if any(e.get("grade") for e in evals):
    st.markdown("### 5 · Grades")
    with st.container(border=True):
        summary = state.get("curve_summary", {})
        if summary:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("In the curve", summary.get("n", 0))
            m2.metric("Raw average", f"{summary.get('raw_avg', 0):.1f}")
            m3.metric("Curve added", f"+{summary.get('added', 0)} each")
            m4.metric("Final average", f"{summary.get('final_avg', 0):.1f}")
            if summary.get("target_reached") is False:
                st.warning(
                    f"The curve target ({c.get('curve_min_avg')}) is above max "
                    f"points ({int(c['max_points'])}); every score is at the max "
                    "but the average still cannot reach the target.")
            if summary.get("partial"):
                st.info(
                    f"The curve used the {summary.get('n', 0)} machine-graded "
                    f"paper(s). {summary.get('n_ungraded', 0)} submission(s) go out "
                    "for hand grading and are not in it — score those by hand, then "
                    "re-check the average before releasing scores.")

        rows = []
        for e in evals:
            g = e.get("grade") or {}
            status = export.status_of(e)
            is_graded = status == export.GRADED
            rows.append({
                "Student": e.get("student_key")
                           or f"⚠️ unassigned — scan read: {e.get('detected_name') or '?'}",
                "Pages": f"{e['page_indices'][0] + 1}–{e['page_indices'][-1] + 1}",
                "Raw": g.get("raw_total") if is_graded else None,
                "Curve": f"+{g.get('curve_added', 0)}" if is_graded else "",
                "Final": g.get("final_total") if is_graded else None,
                "Status": {export.GRADED: "✅ graded",
                           export.NEEDS_GRADING: "✋ hand grade",
                           export.UNASSIGNED: "⚠️ unassigned"}[status],
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    with st.expander("Per-submission feedback"):
        for e in evals:
            g = e.get("grade") or {}
            status = export.status_of(e)
            name = e.get("student_key") or e.get("detected_name") or e["id"]
            if status == export.GRADED:
                head = f"✅ {name} — final {g.get('final_total')}/{int(c['max_points'])}"
            else:
                head = f"{'⚠️' if status == export.UNASSIGNED else '✋'} {name} — not graded"
            with st.expander(head):
                if status != export.GRADED:
                    st.warning(export.reason_for(e))
                    st.caption("It is still included in the download as an ungraded "
                               "PDF with a hand-grading cover sheet.")
                if e.get("failed_pages"):
                    st.error("The scan could not read page(s) "
                             + _plist(e["failed_pages"])
                             + " — this paper was not machine-graded off a partial "
                               "transcription. It is in the download under "
                               "`needs_grading/`, and those pages are also in "
                               "`unreadable_pages/`.")
                for q in g.get("questions", []):
                    st.markdown(f"**Q{q['number']}: {q['score']}/{q['max']}** — "
                                f":red[{q.get('comment', '')}]")
                if g.get("overall_comment") and status == export.GRADED:
                    st.markdown(f"**Overall:** :red[{g['overall_comment']}]")


# ================================================================= DOWNLOAD
if evals and os.path.exists(exam_path()):
    st.markdown("### 6 · Download")
    with st.container(border=True):
        rec = export.reconcile(evals, roster, pdfutil.page_count(exam_path()),
                               pages)
        counts = rec["counts"]
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Submissions", rec["n_submissions"])
        d2.metric("✅ Graded", counts[export.GRADED])
        d3.metric("✋ Hand grade", counts[export.NEEDS_GRADING])
        d4.metric("⚠️ Unassigned", counts[export.UNASSIGNED])

        with st.expander("🔎 Reconciliation — every page of the scan accounted for",
                         expanded=bool(rec["orphan_pages"] or rec["unreadable_pages"]
                                       or rec["duplicate_students"])):
            if rec["orphan_pages"]:
                st.warning(f"{len(rec['orphan_pages'])} scanned page(s) belong to no "
                           f"submission: {_plist(rec['orphan_pages'], 40)}. They are "
                           "exported on their own under `unaccounted_pages/` so they "
                           "are not lost — fix the split above and rebuild the "
                           "download if they belong to a student.")
            else:
                st.success(f"All {rec['n_pages']} scanned page(s) are covered by "
                           "exactly one submission.")
            if rec["unreadable_pages"]:
                st.warning(f"{len(rec['unreadable_pages'])} page(s) could not be "
                           f"read: {_plist(rec['unreadable_pages'], 40)}. They are "
                           "exported under `unreadable_pages/`, and the paper each "
                           "belongs to is under `needs_grading/` — grade those by "
                           "hand.")
            else:
                st.success("Every scanned page was transcribed.")
            if rec["duplicate_students"]:
                st.warning("These students have more than one submission, which "
                           "usually means a split boundary is wrong: "
                           + ", ".join(f"{k} ({len(v)})"
                                       for k, v in rec["duplicate_students"].items()))

        if st.session_state.get("zip_failures"):
            st.error("Some submissions could not be rendered normally and were "
                     "exported as raw scanned pages instead (see manifest.csv):\n\n"
                     + "\n".join(f"- {f}" for f in st.session_state["zip_failures"]))

        zip_path = st.session_state.get("zip_path")
        if zip_path and os.path.exists(zip_path):
            quiz = (c.get("quiz_name") or "graded").replace(" ", "_")
            # Streamed from the project directory rather than session memory, so
            # a server with many open sessions is not holding a ZIP for each.
            with open(zip_path, "rb") as zf:
                st.download_button("⬇️  Download all submissions (ZIP)",
                                   data=zf.read(),
                                   file_name=f"{quiz}_submissions.zip",
                                   mime="application/zip", type="primary",
                                   width="stretch")
            st.caption("Inside: `graded/`, `needs_grading/`, `unassigned/`, "
                       "`unreadable_pages/`, plus `manifest.csv`, `scores.csv` "
                       "(blank scores to fill in by hand) and "
                       "`reconciliation.txt`. Every submission is in there — "
                       "nothing is dropped because grading failed, because no "
                       "student matched, or because a page would not read.")
            rows = st.session_state.get("zip_rows") or []
            if rows:
                with st.expander("Manifest — one row per submission"):
                    st.dataframe(pd.DataFrame(rows), width="stretch",
                                 hide_index=True)
        else:
            st.info("No download built yet — press **Scan & grade everything**, or "
                    "**Rebuild the download** under *Run only part of it*.")
