"""Load and chunk PDF, .md, .qmd, and .txt files into text chunks."""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import List

CHUNK_SIZE = 800   # characters (~200 tokens)
CHUNK_OVERLAP = 150


@dataclass
class DocChunk:
    text: str
    source: str
    chunk_index: int
    token_estimate: int = field(init=False)

    def __post_init__(self):
        self.token_estimate = max(1, len(self.text) // 4)


def _split_text(text: str, source: str) -> List[DocChunk]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    idx = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(DocChunk(text=chunk_text, source=source, chunk_index=idx))
            idx += 1
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def load_pdf(file_bytes: bytes, filename: str) -> List[DocChunk]:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(file_bytes))
    full_text = "\n".join(
        page.extract_text() or "" for page in reader.pages
    )
    return _split_text(full_text, filename)


def load_text(file_bytes: bytes, filename: str) -> List[DocChunk]:
    text = file_bytes.decode("utf-8", errors="replace")
    return _split_text(text, filename)


def load_file(file_bytes: bytes, filename: str) -> List[DocChunk]:
    """Dispatch to the right loader based on extension."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return load_pdf(file_bytes, filename)
    return load_text(file_bytes, filename)


def estimate_total_tokens(chunks: List[DocChunk]) -> int:
    return sum(c.token_estimate for c in chunks)
