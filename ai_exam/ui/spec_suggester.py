"""UI helper that turns an uploaded PDF into a CourseSpec draft.

Used by the "Suggest from materials" button on the Run page. Extracts text
from the PDF via pypdf, dispatches one SpecSuggesterAgent call (Haiku by
default), returns the resulting CourseSpec.

Cap on submitted text: most Haiku context windows comfortably absorb ~100k
characters (~25k tokens), and beyond that the model's attention degrades
anyway. We truncate at a generous limit so the call stays fast and cheap.
"""

from __future__ import annotations

import io
from pathlib import Path

import pypdf

import config
from agents.spec_suggester import SpecSuggesterAgent
from models import CourseSpec


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PERSONA_DIR = _PROJECT_ROOT / "persona"

# Soft cap on the materials text we feed to Haiku. ~100k chars is roughly
# 25k tokens, well within Haiku's window and short enough that the model
# stays focused. Larger corpora get truncated to the first ~100k chars
# (typically chapter 1-3 of a textbook), which gives the suggester enough
# breadth to draft a reasonable spec. The instructor edits afterward.
_MAX_MATERIALS_CHARS = 100_000


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Return the concatenated text of every page in a PDF."""
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def suggest_course_spec(pdf_bytes: bytes) -> CourseSpec:
    """Draft a CourseSpec from a single PDF's bytes. Single Haiku call."""
    return suggest_course_spec_multi([pdf_bytes])


def suggest_course_spec_multi(pdfs: list[bytes]) -> CourseSpec:
    """Draft a CourseSpec from one-or-more PDFs. Single Haiku call, no logging.

    The text of every PDF is concatenated (separated by a doc boundary) and
    truncated to _MAX_MATERIALS_CHARS before being handed to Haiku.
    """
    if not pdfs:
        raise ValueError("suggest_course_spec_multi requires at least one PDF.")
    parts: list[str] = []
    for idx, pdf_bytes in enumerate(pdfs):
        text = extract_pdf_text(pdf_bytes)
        if text.strip():
            parts.append(f"=== DOCUMENT {idx + 1} ===\n{text}")
    if not parts:
        raise ValueError(
            "Could not extract any text from the uploaded PDF(s). They may be "
            "image-only (scanned) — those would need OCR before this "
            "suggester can read them."
        )
    raw_text = "\n\n".join(parts)
    materials = raw_text[:_MAX_MATERIALS_CHARS]
    agent = SpecSuggesterAgent(
        persona_dir=_PERSONA_DIR,
        provider=config.make_provider("spec_suggester"),
        event_log=None,
        max_tokens=4096,
    )
    return agent.suggest(materials)
