"""Ollama embedder.

Hits a local Ollama server's /api/embed endpoint. Batches multiple texts in one
call when supported by the model; falls back to per-text loop otherwise. Used
by the ingestion pipeline (embed chunks) and by ChromaRetriever (embed queries).
"""

from typing import Protocol

import httpx


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    def __init__(
        self,
        host: str,
        model: str,
        *,
        timeout_s: float = 60.0,
    ) -> None:
        self._host = host.rstrip("/")
        self._model = model
        self._client = httpx.Client(timeout=timeout_s)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.post(
            f"{self._host}/api/embed",
            json={"model": self._model, "input": texts},
        )
        response.raise_for_status()
        payload = response.json()
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError(
                f"Ollama /api/embed returned unexpected payload: "
                f"len(embeddings)={len(embeddings) if isinstance(embeddings, list) else 'N/A'}, "
                f"len(texts)={len(texts)}"
            )
        return embeddings

    def close(self) -> None:
        self._client.close()
