"""LLM grading and curve application.

Each eval is graded in a clean context window (one independent request) using:
  - the rubric & answer key (authoritative),
  - grounding course materials (source of truth for what counts as correct),
  - the student's OCR'd submission,
  - the instructor's additional instructions.

The grader is CRITICALLY constrained to the supplied rubric and materials.
"""
from __future__ import annotations

from typing import Callable

from . import roster as roster_mod
from .llm import LLMClient

# The base "constitution" for the grading assistant. It is deliberately strict
# about staying inside the supplied rubric and course materials.
GRADING_SYSTEM = """You are an experienced, encouraging mentor and teacher evaluating a scanned, hand-written student exam. \
You judge the work ONLY against the materials you are given, and your scoring must be defensible by the answer key and the grounding course materials.

NON-NEGOTIABLE RULES:
1. The RUBRIC & ANSWER KEY is authoritative for point allocation. Award points per its criteria and per-question maximums. Never invent questions, criteria, or point values that are not in the rubric.
2. The GROUNDING COURSE MATERIALS define what is factually correct for this course. Do not reward claims that contradict them, and do not penalize a correct answer just because its wording differs from the key.
3. Grade ONLY what the student actually wrote (provided as OCR text). Do not assume unstated knowledge. If handwriting is ambiguous or a section is blank, grade what is legible and note it — never fabricate an answer on the student's behalf.
4. Follow the INSTRUCTOR'S ADDITIONAL INSTRUCTIONS where they apply, but they never override the rubric's point caps.
5. Be consistent, numeric, and concrete internally. Every deduction must correspond to a specific, actionable gap in the student's understanding.

VOICE OF YOUR WRITTEN FEEDBACK (critical):
- Write every comment as a mentor and teacher speaking directly to the student — warm, specific, and growth-oriented. Address the student as "you."
- NEVER mention, quote, or allude to "the rubric," "the answer key," "the grading criteria," "points possible," or the grading process itself. That machinery is invisible to the student.
- Instead, frame feedback around the COURSE OBJECTIVES and the COURSE MATERIAL: what concept the question was assessing, what the student demonstrated, and what to revisit or practice. Reference the relevant topic or material by name (e.g. "the discussion of ... in the course material") rather than any grading document.
- Reward critical thinking, name what they did well, and give a concrete next step where they fell short.

You are careful and resilient: even if the OCR is messy, you produce a complete, valid grade for every question in the rubric."""

GRADING_USER_TEMPLATE = """QUIZ/EXAM: {quiz_name}
TOTAL POINTS AVAILABLE (this exam's scale): {max_points}

=================  RUBRIC & ANSWER KEY (authoritative)  =================
{rubric}

=================  GROUNDING COURSE MATERIALS (source of truth)  ========
{grounding}

=================  INSTRUCTOR'S ADDITIONAL INSTRUCTIONS  ================
{additional}

=================  STUDENT SUBMISSION (OCR transcription)  ==============
Student (as identified): {student}
{submission}
========================================================================

Grade this submission now. Identify each question from the rubric and score it against that question's criteria and maximum. \
Return ONLY a JSON object (no prose, no code fences) with EXACTLY this shape:

{{
  "questions": [
    {{
      "number": "1",                 // question number as a string, matching the rubric
      "max": 34,                     // this question's maximum from the rubric (integer)
      "score": 30,                   // points awarded (integer, 0..max)
      "comment": "Mentor-voiced feedback spoken directly to the student. Name the concept/course material involved, what they did well, and a concrete next step. NEVER mention the rubric, answer key, points, or grading."
    }}
  ],
  "overall_comment": "A short, encouraging note to the student in a mentor's voice (2-3 sentences), tied to the course objectives and what to focus on next. NEVER mention the rubric, points, or grading."
}}

Every question in the rubric MUST appear exactly once. Scores must be integers within [0, max]. Return valid JSON only."""


def _fmt(x, fallback=""):
    return x if (x is not None and str(x).strip()) else fallback


def error_grade(message: str) -> dict:
    """A grade record standing in for a paper that was NOT graded.

    It carries no score of its own: `error` is what the rest of the app keys
    off, so the paper is excluded from the curve and routed to hand grading
    instead of being handed back to the student as a zero.
    """
    return {
        "questions": [],
        "raw_total": 0,
        "curve_added": 0,
        "curved_total": 0,
        "final_total": 0,
        "overall_comment": message,
        "error": True,
    }


def hand_grading_reason(ev: dict) -> str:
    """Why this submission can never be machine-graded, or "" if it can be.

    Three things put a paper beyond the model's reach, and none of them is worth
    stopping a run for: nobody was assigned to it, part of it could not be
    transcribed, or none of it could. All three are decided from the scan alone,
    before any request is made, so such a paper is routed straight to hand
    grading instead of being sent to the model - which would answer with a
    confident-looking score for a paper nobody read in full.

    The text is what the instructor reads on the paper's cover sheet, so when
    more than one thing is wrong it says so: a paper can be both unassigned and
    unreadable, and hearing only half of that sends them looking in the wrong
    place.
    """
    parts: list[str] = []
    if not ev.get("student_key"):
        parts.append("No roster student was assigned to this submission.")
    failed = ev.get("failed_pages") or []
    if failed:
        where = ", ".join(str(i + 1) for i in failed)
        parts.append(f"The scan could not read page(s) {where} of this paper, even "
                     "after re-reading them, so part of it has no transcription.")
    elif not (ev.get("ocr_markdown") or "").strip():
        parts.append("The scan produced no readable text for this submission.")
    if not parts:
        return ""
    return " ".join(parts) + (" It was not machine-graded - grade it by hand from "
                              "the scanned pages, which follow this sheet.")


def needs_grading(ev: dict) -> bool:
    """True if this submission still has to be graded (or re-graded).

    A paper that can only be hand graded is never "pending": no amount of
    re-running will produce a grade for it, so it must not sit in the queue
    forever.
    """
    if hand_grading_reason(ev):
        return False
    grade = ev.get("grade")
    return grade is None or bool(grade.get("error"))


def grade_eval(llm: LLMClient, eval_rec: dict, *, quiz_name: str, rubric_text: str,
               grounding_text: str, additional: str, max_points: int,
               student_display: str) -> dict:
    """Grade a single eval. Returns a grade dict with per-question scores.

    Resilient: on malformed model output, returns a zeroed grade with an error
    note rather than raising, so one bad paper never aborts a whole batch.
    """
    reason = hand_grading_reason(eval_rec)
    if reason:
        # Sending an unread (or half-read) paper to the model produces a
        # confident-looking score for something nobody transcribed. Route it to
        # hand grading instead - it is still exported, with its scanned pages.
        return error_grade(f"[Not graded - {reason}]")

    user = GRADING_USER_TEMPLATE.format(
        quiz_name=_fmt(quiz_name, "(unnamed)"),
        max_points=max_points,
        rubric=_fmt(rubric_text, "(no rubric provided)"),
        grounding=_fmt(grounding_text, "(no grounding materials provided)"),
        additional=_fmt(additional, "(none)"),
        student=_fmt(student_display, "(unidentified)"),
        submission=_fmt(eval_rec.get("ocr_markdown"), "(no legible submission)"),
    )

    try:
        data = llm.complete_json(GRADING_SYSTEM, user, max_tokens=12000)
    except Exception as e:  # noqa: BLE001
        data = None
        err = str(e)
    else:
        err = ""

    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        return error_grade(
            f"[Grading error - please re-run this paper. {err}]".strip())

    questions = []
    raw_total = 0
    for q in data["questions"]:
        try:
            qmax = int(round(float(q.get("max", 0))))
            score = int(round(float(q.get("score", 0))))
        except (TypeError, ValueError):
            qmax, score = 0, 0
        score = max(0, min(score, qmax if qmax > 0 else score))
        questions.append({
            "number": str(q.get("number", "?")).strip(),
            "max": qmax,
            "score": score,
            "comment": (q.get("comment") or "").strip(),
        })
        raw_total += score

    # Attach answer-location anchors (from OCR) to questions for annotation.
    anchors = {str(a.get("question", "")).strip(): a for a in eval_rec.get("anchors", [])}
    for q in questions:
        a = anchors.get(q["number"])
        if a:
            q["anchor"] = {"page": a.get("page", 0), "y": a.get("y", 0.5)}

    # If the rubric's total differs from the exam's configured max, scale.
    rubric_total = sum(q["max"] for q in questions)
    if rubric_total and rubric_total != max_points:
        raw_total = int(round(raw_total / rubric_total * max_points))

    return {
        "questions": questions,
        "raw_total": raw_total,
        "curve_added": 0,
        "curved_total": raw_total,
        "final_total": raw_total,
        "overall_comment": (data.get("overall_comment") or "").strip(),
        "error": False,
    }


def _display_for(ev: dict) -> str:
    """'First Last' for the grading prompt / progress line."""
    key = ev.get("student_key") or ""
    if key:
        return roster_mod.friendly_name(key)
    return ev.get("detected_name") or ev["id"]


def grade_all(llm: LLMClient, evals: list[dict], *, quiz_name: str, rubric_text: str,
              grounding_text: str, additional: str, max_points: int,
              roster: list[dict], concurrency: int = 0, only_ungraded: bool = True,
              on_result: Callable[[dict], None] | None = None,
              progress: Callable[[int, int, str], None] | None = None) -> dict:
    """Grade evals in place (only those with an assigned student).

    Grading is I/O-bound (each paper is one streaming LLM request), so up to
    `concurrency` papers are graded at once with a thread pool. The shared
    OpenAI client is thread-safe. Progress is reported from this (calling)
    thread as each paper finishes, so it is safe to update the Streamlit UI.

    `only_ungraded` grades just the papers that still need it - never graded, or
    graded with an error - so a partially finished run can be resumed and a
    handful of failures re-tried without paying to re-grade the whole class.

    `on_result` fires on the calling thread after each paper's grade is stored,
    which is what lets the caller checkpoint state to disk paper by paper. A
    failing `on_result` is swallowed: losing the checkpoint must not lose the
    grades still in memory.

    Returns a summary dict. The batch is written into `evals` as it goes, so
    even an exception escaping this function leaves every completed grade in
    place.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    candidates = [e for e in evals if e.get("student_key")]

    # Papers the model can never grade (an unreadable page, nothing legible) are
    # stamped with their reason here rather than being sent to the model or left
    # with no grade record at all. That is what puts them in needs_grading/ with
    # an accurate cover sheet instead of looking like a paper grading never
    # reached.
    hand: list[dict] = []
    for ev in candidates:
        reason = hand_grading_reason(ev)
        if not reason:
            continue
        hand.append(ev)
        if not (ev.get("grade") or {}).get("questions"):
            ev["grade"] = error_grade(f"[Not graded - {reason}]")
            if on_result:
                try:
                    on_result(ev)
                except Exception:  # noqa: BLE001 - a failed checkpoint is not a failed grade
                    pass

    gradable = [e for e in candidates if needs_grading(e)] if only_ungraded \
        else [e for e in candidates if not hand_grading_reason(e)]
    summary = {"attempted": len(gradable), "ok": 0, "failed": 0,
               "skipped": len(candidates) - len(gradable) - len(hand),
               "hand": len(hand),
               "unassigned": len(evals) - len(candidates)}
    if not gradable:
        return summary
    total = len(gradable)
    concurrency = concurrency or getattr(llm, "max_inflight", 3)
    workers = max(1, min(int(concurrency), total))

    def _one(ev: dict) -> dict:
        return grade_eval(
            llm, ev, quiz_name=quiz_name, rubric_text=rubric_text,
            grounding_text=grounding_text, additional=additional,
            max_points=max_points, student_display=_display_for(ev),
        )

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, ev): ev for ev in gradable}
        for fut in as_completed(futures):
            ev = futures[fut]
            display = _display_for(ev)
            try:
                grade = fut.result()
            except Exception as e:  # noqa: BLE001 - never let one paper abort the batch
                grade = error_grade(f"[Grading error - please re-run this paper. {e}]")
            ev["grade"] = grade
            summary["failed" if grade.get("error") else "ok"] += 1
            done += 1
            if on_result:
                try:
                    on_result(ev)
                except Exception:  # noqa: BLE001 - a failed checkpoint is not a failed grade
                    pass
            if progress:
                progress(done - 1, total, f"Graded {display} ({done}/{total})")
    return summary


# --------------------------------------------------------------------- curve
def apply_curve(evals: list[dict], *, max_points: int, min_points: int,
                curve_min_avg) -> dict:
    """Apply the curve and clamp scores. Mutates each eval's grade in place.

    Curve rule: if the class's raw average is below the target minimum average,
    add the same integer number of points to every paper (reducing each
    deduction), choosing the smallest bump such that the average AFTER clamping
    each score to [min_points, max_points] still meets the target. Because high
    scorers cap at max_points, a naive ceil(target - raw_avg) would fall short;
    we raise the uniform bump until the post-clamp average reaches the target.

    The post-clamp average is monotonic non-decreasing in the bump, so a smallest
    bump always exists and is reachable whenever the target does not exceed
    max_points. Integer points only.
    """
    graded = [e for e in evals if e.get("grade") and not e["grade"].get("error")]
    graded_ids = {id(e) for e in graded}
    n_ungraded = sum(1 for e in evals if id(e) not in graded_ids)
    summary = {"n": len(graded), "raw_avg": 0.0, "added": 0,
               "final_avg": 0.0, "target_reached": True,
               "n_submissions": len(evals), "n_ungraded": n_ungraded,
               "partial": n_ungraded > 0}
    if not graded:
        return summary

    lo, hi = int(min_points), int(max_points)
    raw_scores = [e["grade"]["raw_total"] for e in graded]
    raw_avg = sum(raw_scores) / len(raw_scores)
    summary["raw_avg"] = raw_avg

    def clamped_avg(bump: int) -> float:
        return sum(max(lo, min(hi, r + bump)) for r in raw_scores) / len(raw_scores)

    added = 0
    if curve_min_avg is not None:
        target = float(curve_min_avg)
        # Enough bump for the lowest paper to reach max_points saturates the
        # average at max_points; no larger bump can help beyond that.
        max_bump = max(0, hi - min(raw_scores))
        while added < max_bump and clamped_avg(added) < target:
            added += 1
        summary["target_reached"] = clamped_avg(added) >= target - 1e-9
    summary["added"] = added

    for e in graded:
        g = e["grade"]
        g["curve_added"] = added
        g["curved_total"] = g["raw_total"] + added
        final = max(lo, min(hi, g["curved_total"]))
        g["final_total"] = final

    finals = [e["grade"]["final_total"] for e in graded]
    summary["final_avg"] = sum(finals) / len(finals)
    return summary
