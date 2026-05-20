"""Retriever Protocol.

Defines the interface that any concrete retriever (Chroma, LanceDB, GraphRAG,
in-memory fake) must satisfy to be injectable into agents that ground their
output in uploaded course materials.
"""

from typing import Protocol, runtime_checkable

from models import Chunk


@runtime_checkable
class Retriever(Protocol):
    def search(
        self,
        query: str,
        *,
        k: int = 8,
        include_prior_exams: bool = False,
    ) -> list[Chunk]:
        """Return up to k chunks ranked by relevance to query.

        Prior-exam chunks must be excluded by default; callers opt in via
        include_prior_exams when they specifically want style retrieval.
        """
        ...

    def get(self, chunk_id: str) -> Chunk:
        """Return the chunk with this id, or raise KeyError."""
        ...
