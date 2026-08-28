"""OCR and eval splitting.

The scanned PDF contains every student's paper concatenated together, 1..n
pages each. We render each page, ask the vision model to (a) decide whether the
page *begins* a new student's submission (it carries a filled-in Name header),
(b) transcribe the page to structured markdown, (c) read the handwritten name,
and (d) locate each answer vertically on the page so graded comments can be
anchored near the right answer later.

Consecutive pages are then grouped into per-student evals.
"""
from __future__ import annotations

from typing import Callable

from . import pdfutil, roster as roster_mod
from .llm import LLMClient

PAGE_PROMPT = """You are an OCR and document-structure engine for grading scanned, hand-filled exam papers. \
Every student's paper starts with a header line containing a "Name:" field with the student's handwritten name. \
A single student's submission may span multiple pages; continuation pages do NOT repeat the Name header.

Look at THIS ONE page image and return ONLY a JSON object (no prose, no code fences) with EXACTLY these keys:

{
  "is_new_submission": true or false,   // true ONLY if this page shows a "Name:" header at the top that begins a new student's paper
  "student_name": "",                    // the handwritten name in the Name field, transcribed as best you can; "" if no Name header on this page
  "markdown": "",                        // a faithful, structured Markdown transcription of everything on the page: printed questions AND the student's handwritten answers. Mark handwriting clearly. Preserve question numbers.
  "answers": [                            // one entry per question whose answer region appears on this page
     { "question": "1", "y": 0.35 }       // question number (as a string) and the vertical center of that answer region, 0.0=top of page ... 1.0=bottom
  ]
}

Be precise about is_new_submission: a page is a new submission ONLY if it has the Name header. If unsure and the page looks like a continuation of answers, use false. \
Transcribe faithfully; do not invent answers the student did not write. Return valid JSON only."""


def ocr_page(llm: LLMClient, exam_path: str, page_index: int) -> dict:
    """OCR one page. Returns a dict with the schema described in PAGE_PROMPT."""
    png = pdfutil.render_page_png(exam_path, page_index)
    data = llm.vision_json(PAGE_PROMPT, [png], max_tokens=8000)
    if not isinstance(data, dict):
        # Resilient fallback: treat as a continuation page with no structure.
        data = {}
    return {
        "is_new_submission": bool(data.get("is_new_submission", False)),
        "student_name": (data.get("student_name") or "").strip(),
        "markdown": (data.get("markdown") or "").strip(),
        "answers": data.get("answers") if isinstance(data.get("answers"), list) else [],
    }


def run_ocr(llm: LLMClient, exam_path: str, roster: list[dict], concurrency: int = 5,
            progress: Callable[[int, int, str], None] | None = None) -> list[dict]:
    """OCR every page of the exam and group pages into evals.

    Returns a list of eval dicts:
      {
        "id": "eval_1",
        "page_indices": [0, 1],
        "ocr_markdown": "...",
        "detected_name": "Emily Nguyen",
        "student_key": "Emily Nguyen" | "",   # matched roster display name; blank if no confident match
        "anchors": [ {"question": "1", "page": 0, "y": 0.35}, ... ],
        "grade": null
      }
    """
    # Per-page vision calls are independent, so OCR up to `concurrency` pages at
    # once, then reassemble in page order (grouping into evals is order-sensitive).
    from concurrent.futures import ThreadPoolExecutor, as_completed

    n = pdfutil.page_count(exam_path)
    pages: list[dict] = [None] * n  # type: ignore[list-item]
    workers = max(1, min(int(concurrency), n))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(ocr_page, llm, exam_path, i): i for i in range(n)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                pages[i] = fut.result()
            except Exception:  # noqa: BLE001 - treat a failed page as an empty continuation
                pages[i] = {"is_new_submission": False, "student_name": "",
                            "markdown": "", "answers": []}
            done += 1
            if progress:
                progress(done - 1, n, f"OCR page {done} of {n}")

    # ---- Group into evals ---------------------------------------------------
    evals: list[dict] = []
    current: dict | None = None

    def start_eval(page_idx: int) -> dict:
        return {
            "id": "",
            "page_indices": [],
            "ocr_markdown": "",
            "detected_name": "",
            "student_key": "",
            "anchors": [],
            "grade": None,
        }

    for i, pg in enumerate(pages):
        starts = pg["is_new_submission"] or current is None
        if starts:
            if current is not None:
                evals.append(current)
            current = start_eval(i)
        # append this page to the current eval
        local_page = len(current["page_indices"])
        current["page_indices"].append(i)
        if current["ocr_markdown"]:
            current["ocr_markdown"] += f"\n\n---\n\n*(page {local_page + 1})*\n\n"
        current["ocr_markdown"] += pg["markdown"]
        if not current["detected_name"] and pg["student_name"]:
            current["detected_name"] = pg["student_name"]
        for a in pg["answers"]:
            try:
                current["anchors"].append({
                    "question": str(a.get("question", "")).strip(),
                    "page": local_page,
                    "y": float(a.get("y", 0.5)),
                })
            except (TypeError, ValueError):
                continue
    if current is not None:
        evals.append(current)

    # ---- Finalize: ids + roster match --------------------------------------
    for idx, ev in enumerate(evals, start=1):
        ev["id"] = f"eval_{idx}"
        matched = roster_mod.match_name(ev["detected_name"], roster)
        ev["student_key"] = matched or ""

    return evals
