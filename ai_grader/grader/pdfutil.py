"""PDF helpers: text extraction, rasterization, per-eval splitting, and
annotation of graded feedback onto the original scanned pages.
"""
from __future__ import annotations

import io
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


def annotate_graded_pdf(exam_path: str, page_indices: list[int], grade: dict,
                        student_display: str, max_points: int, out_path: str) -> str:
    """Render a graded copy of one student's eval.

    The original scanned pages are left untouched. All feedback is placed on a
    dedicated grade report appended after the student's pages: the final score,
    each question's score + comment, and the overall comment — all in red. Long
    reports flow onto additional pages so nothing is ever clipped.
    """
    src = fitz.open(exam_path)
    doc = fitz.open()
    for idx in page_indices:
        doc.insert_pdf(src, from_page=idx, to_page=idx)
    src.close()

    questions = grade.get("questions", []) or []
    final = grade.get("final_total", grade.get("raw_total", 0))
    overall = (grade.get("overall_comment") or "").strip()

    # ---- Appended grade report page(s) -------------------------------------
    PAGE_W, PAGE_H = 612, 792          # US Letter
    LEFT, RIGHT, TOP, BOTTOM = 50, 562, 50, 752
    WRAP = 96                          # chars per line at 10pt helv within margins

    def new_report_page():
        return doc.new_page(width=PAGE_W, height=PAGE_H)

    page = new_report_page()
    y = TOP

    def write(text: str, *, size: float, bold: bool, wrap: int, gap: float):
        # Draw line-by-line with insert_text (which always renders) rather than
        # insert_textbox (which silently renders nothing if the box is a hair
        # too short). Lines that run past the bottom margin start a new page.
        nonlocal page, y
        lh = size * 1.35
        fn = "hebo" if bold else "helv"
        lines: list[str] = []
        for ln in text.split("\n"):
            lines.extend(_wrap(ln, wrap) if ln else [""])
        for ln in lines:
            if y + lh > BOTTOM:
                page = new_report_page()
                y = TOP
            y += lh
            page.insert_text((LEFT, y), ln, fontsize=size, fontname=fn, color=RED)
        y += gap

    # Header
    write(_san(f"GRADE REPORT - {student_display}"), size=15, bold=True, wrap=70, gap=4)
    write(_san(f"FINAL SCORE: {final} / {max_points}"), size=13, bold=True, wrap=70, gap=14)

    # Per-question feedback
    for q in questions:
        num = q.get("number", "?")
        qmax = q.get("max", "")
        score = q.get("score", "")
        comment = (q.get("comment") or "").strip()
        write(_san(f"Question {num}:  {score} / {qmax}"), size=11, bold=True, wrap=WRAP, gap=2)
        if comment:
            write(_san(comment), size=10, bold=False, wrap=WRAP, gap=10)
        else:
            y += 8

    # Overall
    if overall:
        write("Overall", size=11, bold=True, wrap=WRAP, gap=2)
        write(_san(overall), size=10, bold=False, wrap=WRAP, gap=6)

    doc.save(out_path)
    doc.close()
    return out_path
