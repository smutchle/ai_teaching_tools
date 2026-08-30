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


def grade_eval(llm: LLMClient, eval_rec: dict, *, quiz_name: str, rubric_text: str,
               grounding_text: str, additional: str, max_points: int,
               student_display: str) -> dict:
    """Grade a single eval. Returns a grade dict with per-question scores.

    Resilient: on malformed model output, returns a zeroed grade with an error
    note rather than raising, so one bad paper never aborts a whole batch.
    """
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
        return {
            "questions": [],
            "raw_total": 0,
            "curve_added": 0,
            "curved_total": 0,
            "final_total": 0,
            "overall_comment": f"[Grading error — please re-run this paper. {err}]".strip(),
            "error": True,
        }

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
              roster: list[dict], concurrency: int = 5,
              progress: Callable[[int, int, str], None] | None = None) -> None:
    """Grade every eval in place (only those with an assigned student).

    Grading is I/O-bound (each paper is one streaming LLM request), so up to
    `concurrency` papers are graded at once with a thread pool. The shared
    OpenAI client is thread-safe. Progress is reported from this (calling)
    thread as each paper finishes, so it is safe to update the Streamlit UI.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    gradable = [e for e in evals if e.get("student_key")]
    if not gradable:
        return
    total = len(gradable)
    workers = max(1, min(int(concurrency), total))

    def _one(ev: dict) -> tuple[dict, dict]:
        display = _display_for(ev)
        grade = grade_eval(
            llm, ev, quiz_name=quiz_name, rubric_text=rubric_text,
            grounding_text=grounding_text, additional=additional,
            max_points=max_points, student_display=display,
        )
        return ev, grade

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, ev): ev for ev in gradable}
        for fut in as_completed(futures):
            ev = futures[fut]
            display = _display_for(ev)
            try:
                _, grade = fut.result()
            except Exception as e:  # noqa: BLE001 - never let one paper abort the batch
                grade = {
                    "questions": [], "raw_total": 0, "curve_added": 0,
                    "curved_total": 0, "final_total": 0,
                    "overall_comment": f"[Grading error — please re-run this paper. {e}]",
                    "error": True,
                }
            ev["grade"] = grade
            done += 1
            if progress:
                progress(done - 1, total, f"Graded {display} ({done}/{total})")


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
    summary = {"n": len(graded), "raw_avg": 0.0, "added": 0,
               "final_avg": 0.0, "target_reached": True}
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
