"""Build RAG context strings to inject into LLM prompts."""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from rag.neo4j_graph_rag import Neo4jGraphRAG

MAX_CONTEXT_WINDOW = 100_000   # tokens
# Reserve tokens for the main prompt + response
RESERVED_TOKENS = 20_000


def _truncate_to_budget(text: str, token_budget: int) -> str:
    char_budget = token_budget * 4
    if len(text) <= char_budget:
        return text
    return text[:char_budget] + "\n\n[...context truncated to fit token budget...]"


def build_rag_context(
    rag: "Neo4jGraphRAG",
    query: str,
    token_budget: int,
    top_k: int = 8,
) -> Optional[str]:
    """Retrieve semantically relevant chunks and build a context block."""
    results = rag.similarity_search(query, top_k=top_k, include_neighbors=True)
    if not results:
        return None

    sections: List[str] = []
    used_tokens = 0
    for _cid, text, _score in results:
        chunk_tokens = max(1, len(text) // 4)
        if used_tokens + chunk_tokens > token_budget:
            break
        sections.append(text)
        used_tokens += chunk_tokens

    if not sections:
        return None

    body = "\n\n---\n\n".join(sections)
    return (
        "## Reference Materials (retrieved via semantic search)\n\n"
        + _truncate_to_budget(body, token_budget)
    )


def build_full_context(
    rag: "Neo4jGraphRAG",
    token_budget: int,
) -> Optional[str]:
    """Concatenate ALL stored chunks up to token_budget."""
    all_chunks = rag.get_all_chunks()
    if not all_chunks:
        return None

    sections: List[str] = []
    used_tokens = 0
    current_source = None
    for source, text in all_chunks:
        chunk_tokens = max(1, len(text) // 4)
        if used_tokens + chunk_tokens > token_budget:
            break
        if source != current_source:
            sections.append(f"### Source: {source}")
            current_source = source
        sections.append(text)
        used_tokens += chunk_tokens

    if not sections:
        return None

    body = "\n\n".join(sections)
    return (
        "## Reference Materials (full course materials)\n\n"
        + _truncate_to_budget(body, token_budget)
    )


def get_context(
    rag: Optional["Neo4jGraphRAG"],
    mode: str,        # "None", "RAG", "Full Materials"
    query: str,
    token_budget: int,
    top_k: int = 8,
) -> Optional[str]:
    """Unified entry point called from generators."""
    if mode == "None" or rag is None:
        return None
    if mode == "Full Materials":
        return build_full_context(rag, token_budget)
    # RAG
    return build_rag_context(rag, query, token_budget, top_k=top_k)
