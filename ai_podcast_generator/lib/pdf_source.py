"""Extract source text from uploaded PDFs via PyMuPDF."""

from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF

# Sized against Claude Opus 5's 1M-token input window: 1M characters is roughly
# 250k tokens, a quarter of the window, and well past any realistic set of
# course readings. The old 240k figure was not a judgement about focus - it was
# the ARC proxy's 131k window, shared between input and output, working out to
# about 60k input tokens. That ceiling no longer exists.
#
# This is a backstop, not a budget. What it protects against is cost and latency
# on a pathological upload, so it still tells the user when material was dropped
# rather than truncating silently.
MAX_SOURCE_CHARS = 1_000_000


@dataclass(frozen=True)
class SourceDocument:
    """Text extracted from one uploaded PDF."""

    filename: str
    text: str
    page_count: int

    @property
    def char_count(self) -> int:
        return len(self.text)


def extract_pdf(filename: str, data: bytes) -> SourceDocument:
    """Pull the text layer out of one PDF.

    Raises ValueError for a PDF with no extractable text, which almost always
    means a scanned document that would need OCR.
    """
    with fitz.open(stream=data, filetype="pdf") as doc:
        pages = [page.get_text("text") for page in doc]
        page_count = doc.page_count

    text = "\n\n".join(p.strip() for p in pages if p.strip()).strip()
    if not text:
        raise ValueError(
            f"{filename}: no selectable text found across {page_count} page(s). "
            "This is usually a scanned PDF, which would need OCR first."
        )
    return SourceDocument(filename=filename, text=text, page_count=page_count)


def combine_sources(documents: tuple[SourceDocument, ...]) -> tuple[str, bool]:
    """Concatenate documents with filename headers.

    Returns the combined text and whether it had to be truncated.
    """
    if not documents:
        raise ValueError("No source documents supplied")

    blocks = [f"### SOURCE: {d.filename}\n\n{d.text}" for d in documents]
    combined = "\n\n\n".join(blocks)
    if len(combined) <= MAX_SOURCE_CHARS:
        return combined, False
    return combined[:MAX_SOURCE_CHARS], True
