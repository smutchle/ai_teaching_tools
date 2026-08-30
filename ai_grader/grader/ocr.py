"""OCR and eval splitting.

The scanned PDF contains every student's paper concatenated together, 1..n
pages each. We render each page, ask the vision model to (a) decide whether the
page *begins* a new student's submission (it carries a filled-in Name header),
(b) transcribe the page to structured markdown, (c) read the handwritten name,
and (d) locate each answer vertically on the page so graded comments can be
anchored near the right answer later.

Consecutive pages are then grouped into per-student evals on those
`is_start` boundaries, which the Review UI lets the grader correct by hand.
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
    """OCR one page. Returns a dict with the schema described in PAGE_PROMPT,
    plus an "error" string that is empty on success.
    """
    png = pdfutil.render_page_png(exam_path, page_index)
    data = llm.vision_json(PAGE_PROMPT, [png], max_tokens=8000)
    if not isinstance(data, dict):
        raise RuntimeError(
            "vision model did not return a JSON object for this page "
            "(check that OPENAI_VISION_MODEL names a multimodal model)"
        )
    is_start = bool(data.get("is_new_submission", False))
    return {
        "is_new_submission": is_start,
        "is_start": is_start,          # user-editable split boundary
        "student_name": (data.get("student_name") or "").strip(),
        "markdown": (data.get("markdown") or "").strip(),
        "answers": data.get("answers") if isinstance(data.get("answers"), list) else [],
        "error": "",
    }


def _failed_page(msg: str) -> dict:
    return {"is_new_submission": False, "is_start": False, "student_name": "",
            "markdown": "", "answers": [], "error": msg}


def ocr_pages(llm: LLMClient, exam_path: str, concurrency: int = 5,
              progress: Callable[[int, int, str], None] | None = None) -> list[dict]:
    """OCR every page of the exam, in page order.

    Per-page vision calls are independent, so up to `concurrency` run at once.
    A page that fails is recorded with its error message rather than silently
    becoming an empty continuation page — an empty page looks exactly like a
    working continuation page, which is how a whole exam collapses into one
    submission without anything obviously going wrong.
    """
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
            except Exception as e:  # noqa: BLE001 - one bad page must not abort the run
                pages[i] = _failed_page(f"{type(e).__name__}: {e}")
            done += 1
            if progress:
                progress(done - 1, n, f"OCR page {done} of {n}")
    return pages


def failed_pages(pages: list[dict]) -> list[int]:
    """0-based indices of pages whose OCR failed."""
    return [i for i, p in enumerate(pages) if p.get("error")]


def build_evals(pages: list[dict], roster: list[dict],
                prior: list[dict] | None = None) -> list[dict]:
    """Group consecutive pages into per-student evals on the `is_start` flags.

    `is_start` starts out as the vision model's `is_new_submission` and can be
    corrected by hand in the UI; page 0 always starts a submission. Passing the
    previous eval list as `prior` carries student assignments across a re-split,
    and carries a grade over only when that submission's pages are unchanged.

    Returns a list of eval dicts:
      {
        "id": "eval_1",
        "page_indices": [0, 1],
        "ocr_markdown": "...",
        "detected_name": "Emily Nguyen",
        "student_key": "Nguyen, Emily" | "",  # roster name; blank if no confident match
        "anchors": [ {"question": "1", "page": 0, "y": 0.35}, ... ],
        "grade": null
      }
    """
    by_first: dict[int, dict] = {}
    for ev in (prior or []):
        idxs = ev.get("page_indices") or []
        if idxs:
            by_first[idxs[0]] = ev

    evals: list[dict] = []
    current: dict | None = None
    for i, pg in enumerate(pages):
        if current is None or pg.get("is_start"):
            current = {
                "id": "", "page_indices": [], "ocr_markdown": "",
                "detected_name": "", "student_key": "", "anchors": [], "grade": None,
            }
            evals.append(current)
        local_page = len(current["page_indices"])
        current["page_indices"].append(i)
        if pg.get("markdown"):
            if current["ocr_markdown"]:
                current["ocr_markdown"] += f"\n\n---\n\n*(page {local_page + 1})*\n\n"
            current["ocr_markdown"] += pg["markdown"]
        if not current["detected_name"] and pg.get("student_name"):
            current["detected_name"] = pg["student_name"]
        for a in pg.get("answers") or []:
            try:
                current["anchors"].append({
                    "question": str(a.get("question", "")).strip(),
                    "page": local_page,
                    "y": float(a.get("y", 0.5)),
                })
            except (TypeError, ValueError):
                continue

    for idx, ev in enumerate(evals, start=1):
        ev["id"] = f"eval_{idx}"
        old = by_first.get(ev["page_indices"][0])
        if old and old.get("student_key"):
            ev["student_key"] = old["student_key"]
        else:
            ev["student_key"] = roster_mod.match_name(ev["detected_name"], roster) or ""
        if old and old.get("page_indices") == ev["page_indices"]:
            ev["grade"] = old.get("grade")
    return evals


def eval_for_page(evals: list[dict], page_index: int) -> dict | None:
    for ev in evals:
        if page_index in (ev.get("page_indices") or []):
            return ev
    return None
