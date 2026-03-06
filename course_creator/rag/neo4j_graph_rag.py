"""Neo4j graph RAG: ingest document chunks and query by vector similarity."""
from __future__ import annotations

from typing import List, Optional, Tuple

from neo4j import GraphDatabase

from rag.document_loader import DocChunk


class Neo4jGraphRAG:
    """
    Manages a dedicated Neo4j database for course-creator RAG.

    Graph schema:
        (:Document {name})
        (:Chunk {id, text, source, chunk_index, token_estimate, embedding})
        (:Document)-[:HAS_CHUNK]->(:Chunk)
        (:Chunk)-[:NEXT_CHUNK]->(:Chunk)   # sequential within same document
    """

    VECTOR_INDEX = "chunk_embeddings"
    EMBEDDING_DIM = 384   # all-MiniLM-L6-v2

    def __init__(self, uri: str, user: str, password: str):
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._embedder = None

    # ------------------------------------------------------------------ setup

    def verify_connection(self) -> bool:
        try:
            self._driver.verify_connectivity()
            return True
        except Exception:
            return False

    def _get_embedder(self):
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
        return self._embedder

    def setup_schema(self):
        """Create constraints and vector index (idempotent)."""
        with self._driver.session() as session:
            session.run(
                "CREATE CONSTRAINT chunk_id IF NOT EXISTS "
                "FOR (c:Chunk) REQUIRE c.id IS UNIQUE"
            )
            # Check if vector index exists before creating
            result = session.run(
                "SHOW INDEXES YIELD name WHERE name = $name RETURN name",
                name=self.VECTOR_INDEX,
            )
            if not result.single():
                session.run(
                    f"""
                    CREATE VECTOR INDEX {self.VECTOR_INDEX} IF NOT EXISTS
                    FOR (c:Chunk) ON (c.embedding)
                    OPTIONS {{
                        indexConfig: {{
                            `vector.dimensions`: {self.EMBEDDING_DIM},
                            `vector.similarity_function`: 'cosine'
                        }}
                    }}
                    """
                )

    # ------------------------------------------------------------------ ingest

    def clear_all(self):
        """Delete all Document and Chunk nodes."""
        with self._driver.session() as session:
            session.run("MATCH (c:Chunk) DETACH DELETE c")
            session.run("MATCH (d:Document) DETACH DELETE d")

    def ingest_chunks(self, chunks: List[DocChunk], progress_callback=None):
        """Embed and store chunks, grouped by source document."""
        embedder = self._get_embedder()

        # Group by source
        by_source: dict[str, List[DocChunk]] = {}
        for chunk in chunks:
            by_source.setdefault(chunk.source, []).append(chunk)

        total = len(chunks)
        done = 0

        with self._driver.session() as session:
            for source, doc_chunks in by_source.items():
                # Create Document node
                session.run(
                    "MERGE (:Document {name: $name})",
                    name=source,
                )

                texts = [c.text for c in doc_chunks]
                embeddings = embedder.encode(texts, show_progress_bar=False).tolist()

                prev_id: Optional[str] = None
                for chunk, emb in zip(doc_chunks, embeddings):
                    chunk_id = f"{source}::{chunk.chunk_index}"
                    session.run(
                        """
                        MERGE (c:Chunk {id: $id})
                        SET c.text = $text,
                            c.source = $source,
                            c.chunk_index = $chunk_index,
                            c.token_estimate = $token_estimate,
                            c.embedding = $embedding
                        WITH c
                        MATCH (d:Document {name: $source})
                        MERGE (d)-[:HAS_CHUNK]->(c)
                        """,
                        id=chunk_id,
                        text=chunk.text,
                        source=source,
                        chunk_index=chunk.chunk_index,
                        token_estimate=chunk.token_estimate,
                        embedding=emb,
                    )
                    if prev_id:
                        session.run(
                            """
                            MATCH (prev:Chunk {id: $prev_id}), (cur:Chunk {id: $cur_id})
                            MERGE (prev)-[:NEXT_CHUNK]->(cur)
                            """,
                            prev_id=prev_id,
                            cur_id=chunk_id,
                        )
                    prev_id = chunk_id

                    done += 1
                    if progress_callback:
                        progress_callback(done / total)

    # ------------------------------------------------------------------ query

    def similarity_search(
        self, query: str, top_k: int = 5, include_neighbors: bool = True
    ) -> List[Tuple[str, str, float]]:
        """
        Return list of (chunk_id, text, score) tuples.
        With include_neighbors=True, also pulls the immediately preceding/following
        chunk for each top-k result (graph traversal for richer context).
        """
        embedder = self._get_embedder()
        query_emb = embedder.encode([query], show_progress_bar=False)[0].tolist()

        with self._driver.session() as session:
            result = session.run(
                f"""
                CALL db.index.vector.queryNodes(
                    '{self.VECTOR_INDEX}', $top_k, $embedding
                ) YIELD node AS c, score
                RETURN c.id AS id, c.text AS text, c.source AS source,
                       c.chunk_index AS chunk_index, score
                ORDER BY score DESC
                """,
                top_k=top_k,
                embedding=query_emb,
            )
            rows = result.data()

        if not rows:
            return []

        results: list[tuple[str, str, float]] = []
        seen_ids: set[str] = set()

        for row in rows:
            cid = row["id"]
            if cid not in seen_ids:
                results.append((cid, row["text"], row["score"]))
                seen_ids.add(cid)

            if include_neighbors:
                # Fetch prev and next chunk from the graph
                with self._driver.session() as session:
                    neighbors = session.run(
                        """
                        MATCH (c:Chunk {id: $id})
                        OPTIONAL MATCH (prev:Chunk)-[:NEXT_CHUNK]->(c)
                        OPTIONAL MATCH (c)-[:NEXT_CHUNK]->(nxt:Chunk)
                        RETURN prev.id AS prev_id, prev.text AS prev_text,
                               nxt.id AS nxt_id, nxt.text AS nxt_text
                        """,
                        id=cid,
                    ).single()
                    if neighbors:
                        if neighbors["prev_id"] and neighbors["prev_id"] not in seen_ids:
                            results.append((neighbors["prev_id"], neighbors["prev_text"], row["score"] * 0.9))
                            seen_ids.add(neighbors["prev_id"])
                        if neighbors["nxt_id"] and neighbors["nxt_id"] not in seen_ids:
                            results.append((neighbors["nxt_id"], neighbors["nxt_text"], row["score"] * 0.9))
                            seen_ids.add(neighbors["nxt_id"])

        # Sort by score descending
        results.sort(key=lambda x: x[2], reverse=True)
        return results

    def get_all_chunks(self) -> List[Tuple[str, str]]:
        """Return all (source, text) for full-context mode."""
        with self._driver.session() as session:
            result = session.run(
                "MATCH (c:Chunk) RETURN c.source AS source, c.chunk_index AS idx, c.text AS text "
                "ORDER BY c.source, c.chunk_index"
            )
            return [(row["source"], row["text"]) for row in result]

    def get_stats(self) -> dict:
        with self._driver.session() as session:
            doc_count = session.run("MATCH (d:Document) RETURN count(d) AS n").single()["n"]
            chunk_count = session.run("MATCH (c:Chunk) RETURN count(c) AS n").single()["n"]
            token_sum = session.run(
                "MATCH (c:Chunk) RETURN coalesce(sum(c.token_estimate), 0) AS n"
            ).single()["n"]
        return {"documents": doc_count, "chunks": chunk_count, "total_tokens": int(token_sum)}

    def close(self):
        self._driver.close()
