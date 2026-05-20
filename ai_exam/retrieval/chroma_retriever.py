"""ChromaRetriever: real vector retrieval over a persistent Chroma collection."""

from pathlib import Path

import chromadb
from chromadb.api.models.Collection import Collection

from models import Chunk

from retrieval.embedder import Embedder


class ChromaRetriever:
    """Retriever backed by a persistent Chroma collection.

    Chunks are stored with `is_prior_exam` in metadata so search() can filter
    them out by default. get() returns by chunk id for the Grounding Verifier.
    """

    def __init__(
        self,
        persist_dir: Path,
        collection_name: str,
        embedder: Embedder,
    ) -> None:
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._collection: Collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._embedder = embedder

    @property
    def collection(self) -> Collection:
        return self._collection

    def count(self) -> int:
        return self._collection.count()

    def search(
        self,
        query: str,
        *,
        k: int = 8,
        include_prior_exams: bool = False,
    ) -> list[Chunk]:
        query_embedding = self._embedder.embed([query])[0]
        where = None if include_prior_exams else {"is_prior_exam": False}
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=where,
        )
        # Chroma returns lists-of-lists keyed by query; we sent one query, so index 0.
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        chunks: list[Chunk] = []
        for cid, doc, meta in zip(ids, documents, metadatas):
            meta = meta or {}
            chunks.append(Chunk(
                id=cid,
                text=doc or "",
                source_doc=str(meta.get("source_doc", "")),
                locator=meta.get("locator"),
                is_prior_exam=bool(meta.get("is_prior_exam", False)),
            ))
        return chunks

    def get(self, chunk_id: str) -> Chunk:
        result = self._collection.get(ids=[chunk_id])
        ids = result.get("ids") or []
        if not ids:
            raise KeyError(chunk_id)
        doc = (result.get("documents") or [""])[0]
        meta = (result.get("metadatas") or [{}])[0] or {}
        return Chunk(
            id=ids[0],
            text=doc or "",
            source_doc=str(meta.get("source_doc", "")),
            locator=meta.get("locator"),
            is_prior_exam=bool(meta.get("is_prior_exam", False)),
        )
