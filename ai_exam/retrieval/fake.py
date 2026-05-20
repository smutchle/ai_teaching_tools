"""In-memory FakeRetriever for tests and local development."""

import re
from collections import Counter

from models import Chunk

_TOKEN = re.compile(r"\w+")


def _tokens(s: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(s)]


class FakeRetriever:
    """Deterministic in-memory retriever.

    Ranks chunks by case-insensitive token-overlap with the query; ties broken
    by insertion order. Suitable for unit tests and for wiring the orchestrator
    end-to-end before a real vector store is configured. Not for production
    use — has no embedding model, no persistence, and scales linearly.
    """

    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks: list[Chunk] = list(chunks)
        self._by_id: dict[str, Chunk] = {}
        self._chunk_tokens: list[Counter[str]] = []
        for c in self._chunks:
            if c.id in self._by_id:
                raise ValueError(f"duplicate chunk id: {c.id!r}")
            self._by_id[c.id] = c
            self._chunk_tokens.append(Counter(_tokens(c.text)))

    def search(
        self,
        query: str,
        *,
        k: int = 8,
        include_prior_exams: bool = False,
    ) -> list[Chunk]:
        query_tokens = Counter(_tokens(query))
        if not query_tokens:
            return []
        scored: list[tuple[int, int, Chunk]] = []
        for idx, chunk in enumerate(self._chunks):
            if chunk.is_prior_exam and not include_prior_exams:
                continue
            overlap = sum((query_tokens & self._chunk_tokens[idx]).values())
            if overlap > 0:
                # Negative score so ascending sort gives descending relevance;
                # idx is the deterministic tiebreaker.
                scored.append((-overlap, idx, chunk))
        scored.sort()
        return [c for _, _, c in scored[:k]]

    def get(self, chunk_id: str) -> Chunk:
        return self._by_id[chunk_id]
