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
from .llm import LLMClient, PermanentLLMError, extract_json

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

# A deliberately tiny fallback used when the full transcription call fails. The
# split matters far more than the transcription - a lost boundary silently merges
# two students, while a lost transcription only sends one paper to hand grading -
# so when the big call fails we still try to recover the boundary on its own. The
# response is a few tokens, so it cannot be truncated by the proxy's output cap.
BOUNDARY_PROMPT = """Look at this scanned exam page. Every student's paper starts with a header line containing a "Name:" field filled in by hand; continuation pages do NOT repeat it.

Return ONLY this JSON object, nothing else:

{"is_new_submission": true, "student_name": ""}

Set is_new_submission to true ONLY if this page shows a filled-in "Name:" header beginning a new student's paper, and put the handwritten name in student_name (use "" when there is no Name header)."""


def _coerce_bool(value) -> bool:
    """Models return the boundary flag as true/1/"true"/"yes" depending on mood.
    A plain bool() call reads the string "false" as True, and a missing key as
    False - either way a boundary is silently wrong."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1", "t", "new", "start"}
    return False


def _read_boundary(data: dict) -> tuple[bool, str]:
    """Decide whether a page starts a submission, from every available signal.

    The prompt guarantees `student_name` is empty unless the page carries a Name
    header, so a transcribed name is independent evidence of a boundary even
    when the model forgot to set the flag.
    """
    name = (data.get("student_name") or "").strip()
    return _coerce_bool(data.get("is_new_submission")) or bool(name), name


# How much of the model's raw reply we keep on a page record. Kept only when
# something went wrong, so the instructor can see *what the scan actually said*
# instead of being told only that it failed - that is the difference between
# "re-run and hope" and "the model replied 'I cannot view images'".
RAW_LIMIT = 6000


def _clip(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= RAW_LIMIT else text[:RAW_LIMIT] + "\n…[truncated]"


def ocr_page(llm: LLMClient, exam_path: str, page_index: int) -> dict:
    """OCR one page. Returns a dict with the schema described in PAGE_PROMPT,
    plus an "error" string that is empty on success and a "raw" string holding
    the model's own reply whenever the result is unusable.
    """
    png = pdfutil.render_page_png(exam_path, page_index)
    raw = ""
    try:
        raw = llm.vision(PAGE_PROMPT, [png], max_tokens=8000)
        data = extract_json(raw)
        if not isinstance(data, dict):
            raise RuntimeError(
                "vision model did not return a JSON object for this page "
                "(check that OPENAI_VISION_MODEL names a multimodal model)"
            )
    except PermanentLLMError:
        raise                          # misconfiguration - abort the whole run
    except Exception as full_err:      # noqa: BLE001
        # Transcription failed. Recover at least the split boundary with a tiny
        # second call, so this page cannot silently swallow the next student.
        raw2 = ""
        try:
            raw2 = llm.vision(BOUNDARY_PROMPT, [png], max_tokens=200)
            data = extract_json(raw2)
        except PermanentLLMError:
            raise
        except Exception as boundary_err:  # noqa: BLE001
            return _failed_page(f"{type(full_err).__name__}: {full_err}",
                                raw=raw or raw2 or str(boundary_err))
        if not isinstance(data, dict):
            return _failed_page(f"{type(full_err).__name__}: {full_err}",
                                raw=raw or raw2)
        is_start, name = _read_boundary(data)
        return {
            "is_new_submission": is_start,
            "is_start": is_start,
            "boundary_confident": True,
            "student_name": name,
            "markdown": "",
            "answers": [],
            "raw": _clip(raw or raw2),
            "error": f"transcription failed ({full_err}); split boundary recovered "
                     "separately, but this page has no text to grade",
        }

    is_start, name = _read_boundary(data)
    markdown = (data.get("markdown") or "").strip()
    return {
        "is_new_submission": is_start,
        "is_start": is_start,          # user-editable split boundary
        "boundary_confident": True,
        "student_name": name,
        "markdown": markdown,
        "answers": data.get("answers") if isinstance(data.get("answers"), list) else [],
        # A page that read cleanly needs no raw copy; one that came back empty
        # does - that is exactly the case where the reply explains itself.
        "raw": "" if markdown else _clip(raw),
        "error": "",
    }


def _failed_page(msg: str, raw: str = "") -> dict:
    """A page we could not read at all.

    `is_start` is True on purpose. We do not know whether this page begins a
    student's paper, and the two ways of being wrong are not symmetric: a
    spurious split shows up as an extra unassigned submission the instructor can
    merge in one click, while a missed split silently swallows the next student
    into the previous paper and hands back a grade covering two people. So an
    unreadable page always breaks the run, and `boundary_confident` marks it for
    review.
    """
    return {"is_new_submission": False, "is_start": True,
            "boundary_confident": False, "student_name": "",
            "markdown": "", "answers": [], "raw": _clip(raw), "error": msg}


def ocr_pages(llm: LLMClient, exam_path: str, concurrency: int = 0,
              progress: Callable[[int, int, str], None] | None = None,
              pages: list[dict] | None = None, indices: list[int] | None = None,
              on_page: Callable[[list[dict]], None] | None = None) -> list[dict]:
    """OCR the exam, returning one record per page in page order.

    Per-page vision calls are independent, so up to `concurrency` run at once.
    A page that fails is recorded with its error message rather than silently
    becoming an empty continuation page - an empty page looks exactly like a
    working continuation page, which is how a whole exam collapses into one
    submission without anything obviously going wrong.

    Pass `pages` (a previous result) and `indices` to re-OCR only certain pages -
    the failed ones, typically - keeping every page that already worked instead
    of paying to transcribe the whole scan again.

    `on_page` fires on the calling thread after each page lands, which lets the
    caller checkpoint to disk; a page that has been transcribed is never
    transcribed twice because the run was interrupted afterwards.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    n = pdfutil.page_count(exam_path)
    if pages is None or len(pages) != n:
        pages = [None] * n  # type: ignore[list-item]
    else:
        pages = list(pages)
    todo = [i for i in (indices if indices is not None else range(n)) if 0 <= i < n]
    # Anything never attempted still needs a record, even outside `indices`.
    for i in range(n):
        if pages[i] is None and i not in todo:
            pages[i] = _failed_page("not OCR'd yet")

    total = len(todo)
    if not total:
        return pages
    # 0 means "whatever the proxy will accept" - the client knows the cap.
    concurrency = concurrency or getattr(llm, "max_inflight", 3)
    workers = max(1, min(int(concurrency), total))

    # Catch a bad model name / key on one page before spending the whole exam
    # discovering it 100 times over.
    preflight = getattr(llm, "check_vision", None)
    if preflight is not None:
        preflight()

    done = 0
    permanent: PermanentLLMError | None = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(ocr_page, llm, exam_path, i): i for i in todo}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                pages[i] = fut.result()
            except PermanentLLMError as e:
                # Not a bad page - a broken configuration. Stop instead of
                # marking every remaining page as failed.
                permanent = permanent or e
                for pending in futures:
                    pending.cancel()
            except Exception as e:  # noqa: BLE001 - one bad page must not abort the run
                pages[i] = _failed_page(f"{type(e).__name__}: {e}")
            done += 1
            if on_page:
                try:
                    on_page(pages)
                except Exception:  # noqa: BLE001 - a failed checkpoint is not a failed page
                    pass
            if progress:
                progress(done - 1, total, f"OCR page {done} of {total}")
    if permanent:
        raise permanent
    for i in range(n):
        if pages[i] is None:
            pages[i] = _failed_page("cancelled")
    return pages


def failed_pages(pages: list[dict]) -> list[int]:
    """0-based indices of pages whose OCR failed."""
    return [i for i, p in enumerate(pages) if p and p.get("error")]


def unconfident_pages(pages: list[dict]) -> list[int]:
    """0-based indices of pages whose split boundary is a guess, not a reading.

    These are the dangerous ones: an unread page could be the start of a
    student's paper, and getting it wrong merges or splits two students.
    """
    return [i for i, p in enumerate(pages)
            if p and not p.get("boundary_confident", True)]


def empty_pages(pages: list[dict]) -> list[int]:
    """0-based indices of pages that OCR'd without error but transcribed to
    nothing. Usually a blank back page, but a run of them means the configured
    vision model is not actually multimodal."""
    return [i for i, p in enumerate(pages)
            if p and not p.get("error") and not p.get("markdown")]


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
        "failed_pages": [],                   # exam page indices whose OCR failed
        "unconfident_pages": [],              # pages whose split boundary is a guess
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
        pg = pg or _failed_page("no OCR record for this page")
        if current is None or pg.get("is_start"):
            current = {
                "id": "", "page_indices": [], "ocr_markdown": "",
                "detected_name": "", "student_key": "", "anchors": [],
                "failed_pages": [], "unconfident_pages": [], "grade": None,
            }
            evals.append(current)
        local_page = len(current["page_indices"])
        current["page_indices"].append(i)
        if pg.get("error"):
            # Recorded per submission so the review UI can say which papers were
            # graded off an incomplete transcription.
            current["failed_pages"].append(i)
        if not pg.get("boundary_confident", True):
            current["unconfident_pages"].append(i)
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
