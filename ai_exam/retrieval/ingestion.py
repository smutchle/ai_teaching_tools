"""PDF ingestion: PDF → text → chunks → embed → Chroma collection."""

import hashlib
from pathlib import Path

import pypdf

from models import Chunk

from retrieval.chunking import chunk_pages
from retrieval.chroma_retriever import ChromaRetriever


def _chunk_id(source_doc: str, locator: str, text: str) -> str:
    """Deterministic chunk id so re-ingesting the same doc doesn't dupe entries."""
    h = hashlib.sha1(f"{source_doc}::{locator}::{text[:200]}".encode("utf-8"))
    return f"chunk_{h.hexdigest()[:12]}"


def extract_pdf_pages(pdf_path: Path) -> list[str]:
    reader = pypdf.PdfReader(str(pdf_path))
    return [page.extract_text() or "" for page in reader.pages]


def ingest_pdf(
    pdf_path: Path,
    retriever: ChromaRetriever,
    *,
    is_prior_exam: bool = False,
    max_chars_per_chunk: int = 1500,
) -> list[Chunk]:
    """Extract → chunk → embed → upsert. Returns the Chunks written (with assigned ids).

    Idempotent: chunk ids are deterministic on (source_doc, locator, text), so
    re-ingesting the same PDF replaces in place rather than duplicating.
    """
    source_doc = pdf_path.name
    pages_text = extract_pdf_pages(pdf_path)
    text_chunks = chunk_pages(pages_text, source_doc=source_doc, max_chars=max_chars_per_chunk)
    if not text_chunks:
        return []

    ids = [_chunk_id(c.source_doc, c.locator, c.text) for c in text_chunks]
    documents = [c.text for c in text_chunks]
    metadatas: list[dict[str, str | bool]] = [
        {
            "source_doc": c.source_doc,
            "locator": c.locator,
            "is_prior_exam": is_prior_exam,
        }
        for c in text_chunks
    ]
    embeddings = retriever._embedder.embed(documents)  # noqa: SLF001 — friend access
    retriever.collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,  # type: ignore[arg-type]
        embeddings=embeddings,  # type: ignore[arg-type]
    )
    return [
        Chunk(
            id=cid,
            text=tc.text,
            source_doc=tc.source_doc,
            locator=tc.locator,
            is_prior_exam=is_prior_exam,
        )
        for cid, tc in zip(ids, text_chunks)
    ]
