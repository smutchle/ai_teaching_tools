"""Heading-aware text chunking for academic lecture material.

Splits on top-level headings (single-digit section numbers from pypdf's text
extraction, e.g. "1 Hess's Law", "2 Equilibrium Constant"), then sub-splits
sections that exceed max_chars into paragraph-sized chunks. Each chunk gets a
locator describing where it came from (page + section heading).
"""

import re
from dataclasses import dataclass


# Matches "1 Title", "2 Title", etc. at the start of a line — pypdf renders
# numbered headings this way for the test corpus. Tighter than a general
# heading detector but adequate for the academic-lecture target.
_HEADING_RE = re.compile(r"^(\d+)\s+([A-Z][^\n]{0,80})$", re.MULTILINE)

# Paragraph break: blank line.
_PARA_RE = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class TextChunk:
    """Pre-embedding chunk. Becomes a `models.Chunk` after embedding + storage."""

    text: str
    source_doc: str
    locator: str


def chunk_pages(
    pages_text: list[str],
    source_doc: str,
    *,
    max_chars: int = 1500,
    min_chars: int = 80,
) -> list[TextChunk]:
    """Chunk a document's per-page text into heading-aware chunks.

    Strategy: concatenate pages with explicit page markers so the locator can
    name the original page, split by detected headings into sections, then
    split sections longer than max_chars at paragraph boundaries. Drop chunks
    shorter than min_chars (typically figure captions and page-number noise).
    """
    combined_parts: list[str] = []
    page_offsets: list[tuple[int, int]] = []  # (page_num, char_offset_in_combined)
    cursor = 0
    for i, text in enumerate(pages_text):
        page_num = i + 1
        page_offsets.append((page_num, cursor))
        combined_parts.append(text)
        cursor += len(text) + 2  # +2 for the "\n\n" we'll join with
    combined = "\n\n".join(combined_parts)

    def page_for_offset(offset: int) -> int:
        current = page_offsets[0][0]
        for page_num, start in page_offsets:
            if start <= offset:
                current = page_num
            else:
                break
        return current

    # Find headings and the section boundaries between them.
    headings = list(_HEADING_RE.finditer(combined))
    sections: list[tuple[str, int, int]] = []  # (heading_text, start_offset, end_offset)
    if not headings:
        sections.append(("(no heading)", 0, len(combined)))
    else:
        if headings[0].start() > 0:
            sections.append(("(preamble)", 0, headings[0].start()))
        for idx, m in enumerate(headings):
            heading_text = m.group(0).strip()
            section_start = m.start()
            section_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(combined)
            sections.append((heading_text, section_start, section_end))

    chunks: list[TextChunk] = []
    for heading_text, start, end in sections:
        body = combined[start:end].strip()
        if len(body) < min_chars:
            continue
        page_num = page_for_offset(start)
        if len(body) <= max_chars:
            chunks.append(TextChunk(
                text=body,
                source_doc=source_doc,
                locator=f"p.{page_num} | {heading_text}",
            ))
            continue
        # Section too long — split at paragraph boundaries, accumulating up to max_chars.
        paragraphs = [p.strip() for p in _PARA_RE.split(body) if p.strip()]
        buf: list[str] = []
        buf_chars = 0
        part_num = 1
        for para in paragraphs:
            if buf_chars + len(para) + 2 > max_chars and buf:
                chunks.append(TextChunk(
                    text="\n\n".join(buf),
                    source_doc=source_doc,
                    locator=f"p.{page_num} | {heading_text} (pt {part_num})",
                ))
                part_num += 1
                buf = [para]
                buf_chars = len(para)
            else:
                buf.append(para)
                buf_chars += len(para) + 2
        if buf:
            tail_locator = (
                f"p.{page_num} | {heading_text}"
                if part_num == 1
                else f"p.{page_num} | {heading_text} (pt {part_num})"
            )
            chunks.append(TextChunk(
                text="\n\n".join(buf),
                source_doc=source_doc,
                locator=tail_locator,
            ))

    return chunks
