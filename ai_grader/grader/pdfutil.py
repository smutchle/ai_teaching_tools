"""PDF helpers: text extraction, rasterization, per-eval splitting, and
annotation of graded feedback onto the original scanned pages.
"""
from __future__ import annotations

import os

import fitz  # PyMuPDF

# Rendering DPI for the images we send to the vision model. High enough for
# handwriting, low enough to keep payloads reasonable.
OCR_DPI = 150
RED = (0.85, 0.05, 0.05)


def page_count(pdf_path: str) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count


def render_page_png(pdf_path: str, page_index: int, dpi: int = OCR_DPI) -> bytes:
    """Render a single page to PNG bytes."""
    with fitz.open(pdf_path) as doc:
        pix = doc[page_index].get_pixmap(dpi=dpi)
        return pix.tobytes("png")


def extract_text(pdf_path: str, max_chars: int = 120_000) -> str:
    """Extract text from a vector PDF (rubric / grounding materials)."""
    if not pdf_path or not os.path.exists(pdf_path):
        return ""
    parts: list[str] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            parts.append(page.get_text("text"))
    text = "\n".join(parts).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]..."
    return text


def split_eval_pdf(exam_path: str, page_indices: list[int], out_path: str) -> str:
    """Write a new PDF containing only the given (0-based) pages of the exam."""
    src = fitz.open(exam_path)
    dst = fitz.open()
    for idx in page_indices:
        dst.insert_pdf(src, from_page=idx, to_page=idx)
    dst.save(out_path)
    dst.close()
    src.close()
    return out_path


# --------------------------------------------------------------------- render
_SUBST = {
    "—": "-", "–": "-", "‒": "-", "―": "-",   # dashes
    "→": "->", "←": "<-", "↔": "<->",              # arrows
    "‘": "'", "’": "'", "“": '"', "”": '"',   # smart quotes
    "…": "...", "•": "-", " ": " ", "−": "-",  # misc
    "×": "x", "≤": "<=", "≥": ">=",
}


def _san(text: str) -> str:
    """Make text safe for base-14 PDF fonts (Latin-1). Replaces common unicode
    punctuation, then drops anything still outside Latin-1 so glyphs never show
    up as '?' or tofu boxes."""
    if not text:
        return ""
    for k, v in _SUBST.items():
        text = text.replace(k, v)
    return text.encode("latin-1", "replace").decode("latin-1")


def _wrap(text: str, width: int) -> list[str]:
    text = _san(text)
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 <= width:
            cur = f"{cur} {w}".strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


class _Report:
    """Flowing text writer that appends US-Letter pages to a document.

    Text is drawn line-by-line with insert_text (which always renders) rather
    than insert_textbox (which silently renders nothing if the box is a hair too
    short), and lines that run past the bottom margin start a new page, so a
    long report is never clipped.
    """

    PAGE_W, PAGE_H = 612, 792          # US Letter
    LEFT, TOP, BOTTOM = 50, 50, 752
    WRAP = 96                          # chars per line at 10pt helv within margins

    def __init__(self, doc: "fitz.Document", color: tuple = RED):
        self.doc = doc
        self.color = color
        self.page = doc.new_page(width=self.PAGE_W, height=self.PAGE_H)
        self.y = self.TOP

    def write(self, text: str, *, size: float = 10, bold: bool = False,
              wrap: int | None = None, gap: float = 0.0) -> None:
        lh = size * 1.35
        fn = "hebo" if bold else "helv"
        width = wrap or self.WRAP
        lines: list[str] = []
        for ln in (text or "").split("\n"):
            lines.extend(_wrap(ln, width) if ln else [""])
        for ln in lines:
            if self.y + lh > self.BOTTOM:
                self.page = self.doc.new_page(width=self.PAGE_W, height=self.PAGE_H)
                self.y = self.TOP
            self.y += lh
            self.page.insert_text((self.LEFT, self.y), ln, fontsize=size,
                                  fontname=fn, color=self.color)
        self.y += gap

    def space(self, amount: float) -> None:
        self.y += amount


def _copy_pages(doc: "fitz.Document", exam_path: str, page_indices: list[int]) -> list[int]:
    """Append the given exam pages to `doc`. Returns the indices that were
    missing from the exam PDF (rather than raising), so an out-of-range page can
    never cost a student their whole paper."""
    missing: list[int] = []
    with fitz.open(exam_path) as src:
        n = src.page_count
        for idx in page_indices:
            if 0 <= idx < n:
                doc.insert_pdf(src, from_page=idx, to_page=idx)
            else:
                missing.append(idx)
    return missing


def annotate_graded_pdf(exam_path: str, page_indices: list[int], grade: dict,
                        student_display: str, max_points: int, out_path: str) -> str:
    """Render a graded copy of one student's eval.

    The original scanned pages are left untouched. All feedback is placed on a
    dedicated grade report appended after the student's pages: the final score,
    each question's score + comment, and the overall comment - all in red. Long
    reports flow onto additional pages so nothing is ever clipped.
    """
    doc = fitz.open()
    missing = _copy_pages(doc, exam_path, page_indices)

    questions = grade.get("questions", []) or []
    final = grade.get("final_total", grade.get("raw_total", 0))
    overall = (grade.get("overall_comment") or "").strip()

    rep = _Report(doc)
    rep.write(_san(f"GRADE REPORT - {student_display}"), size=15, bold=True,
              wrap=70, gap=4)
    rep.write(_san(f"FINAL SCORE: {final} / {max_points}"), size=13, bold=True,
              wrap=70, gap=14)

    for q in questions:
        num = q.get("number", "?")
        qmax = q.get("max", "")
        score = q.get("score", "")
        comment = (q.get("comment") or "").strip()
        rep.write(_san(f"Question {num}:  {score} / {qmax}"), size=11, bold=True, gap=2)
        if comment:
            rep.write(_san(comment), size=10, gap=10)
        else:
            rep.space(8)

    if overall:
        rep.write("Overall", size=11, bold=True, gap=2)
        rep.write(_san(overall), size=10, gap=6)

    if missing:
        rep.space(8)
        rep.write(_san("NOTE: scanned page(s) "
                       + ", ".join(str(i + 1) for i in missing)
                       + " could not be read from the exam PDF and are not included."),
                  size=10, bold=True)

    doc.save(out_path)
    doc.close()
    return out_path


def write_ungraded_pdf(exam_path: str, page_indices: list[int], out_path: str, *,
                       student_display: str, max_points: int,
                       reason: str = "", detected_name: str = "",
                       partial_grade: dict | None = None) -> str:
    """Render one student's eval for HAND grading.

    Used whenever a submission could not be machine-graded - grading errored,
    grading never ran, or no student was ever assigned. The student's original
    scanned pages are preserved verbatim; a cover sheet goes IN FRONT of them
    (so the instructor sees it on opening the file) stating that this paper was
    not graded, and carrying a blank score line to fill in by hand.

    Any partial machine output is reproduced below the score line as a starting
    point - never as a score - so a half-finished grading run is not thrown away.
    """
    doc = fitz.open()

    rep = _Report(doc)
    rep.write("NOT GRADED - GRADE BY HAND", size=16, bold=True, wrap=70, gap=6)
    rep.write(_san(f"Student: {student_display or '(UNIDENTIFIED)'}"), size=12,
              bold=True, wrap=70, gap=2)
    if detected_name:
        rep.write(_san(f"Name read from the scan: {detected_name}"), size=10, wrap=70, gap=2)
    span = f"{page_indices[0] + 1}-{page_indices[-1] + 1}" if page_indices else "(none)"
    rep.write(_san(f"Scanned pages {span} ({len(page_indices)} page(s)), "
                   f"attached after this sheet."), size=10, wrap=70, gap=2)
    if reason:
        rep.write(_san(f"Why it was not graded: {reason}"), size=10, wrap=70, gap=2)
    rep.space(10)
    rep.write(_san(f"FINAL SCORE:  __________ / {max_points}"), size=14, bold=True,
              wrap=70, gap=14)

    questions = (partial_grade or {}).get("questions") or []
    if questions:
        rep.write("Partial machine output below - review before using it.",
                  size=10, bold=True, gap=6)
        for q in questions:
            rep.write(_san(f"Question {q.get('number', '?')}:  "
                           f"{q.get('score', '')} / {q.get('max', '')}"),
                      size=11, bold=True, gap=2)
            comment = (q.get("comment") or "").strip()
            if comment:
                rep.write(_san(comment), size=10, gap=8)
            else:
                rep.space(6)

    missing = _copy_pages(doc, exam_path, page_indices)
    if missing:
        rep2 = _Report(doc)
        rep2.write(_san("NOTE: scanned page(s) "
                        + ", ".join(str(i + 1) for i in missing)
                        + " could not be read from the exam PDF and are not included."),
                   size=11, bold=True)

    doc.save(out_path)
    doc.close()
    return out_path


def write_unaccounted_pdf(exam_path: str, page_indices: list[int], out_path: str) -> str:
    """Scanned pages that belong to no submission at all.

    A page can end up here if the split was edited oddly or the exam PDF was
    replaced with a longer one after OCR. Reporting such a page is not enough -
    it is somebody's work - so the pages themselves are exported behind a sheet
    explaining what they are and what to do with them.
    """
    doc = fitz.open()
    rep = _Report(doc)
    rep.write("PAGES NOT IN ANY SUBMISSION", size=16, bold=True, wrap=70, gap=6)
    rep.write(_san("These scanned pages were not part of any student's submission, "
                   "so they were never OCR'd into one, never graded, and are not in "
                   "any other PDF in this download."), size=11, wrap=80, gap=8)
    rep.write(_san("Scan page(s): "
                   + ", ".join(str(i + 1) for i in page_indices)), size=11,
              bold=True, wrap=80, gap=8)
    rep.write(_san("They are attached after this sheet. Identify the student, then "
                   "fix the split in the Check the scan panel (tick 'starts a new "
                   "submission' on the right page) and export again - or grade "
                   "these pages by hand."), size=11, wrap=80, gap=4)

    missing = _copy_pages(doc, exam_path, page_indices)
    if missing:
        rep2 = _Report(doc)
        rep2.write(_san("NOTE: scanned page(s) "
                        + ", ".join(str(i + 1) for i in missing)
                        + " could not be read from the exam PDF and are not included."),
                   size=11, bold=True)
    doc.save(out_path)
    doc.close()
    return out_path
