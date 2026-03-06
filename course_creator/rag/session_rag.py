"""
Streamlit session-aware helper: build a context string using the current
RAG settings stored in st.session_state.
"""
from __future__ import annotations

from typing import Optional

import streamlit as st

from rag.context_builder import get_context


def get_session_rag_context(query: str) -> Optional[str]:
    """
    Return a context string (or None) for the given query, based on the
    rag_mode / token_budget / top_k settings stored in session state.
    Called by each generation tab before invoking the LLM.
    """
    mode = st.session_state.get("rag_mode", "None")
    if mode == "None":
        return None

    rag = st.session_state.get("_rag_instance")
    if rag is None:
        # Try to connect lazily
        from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
        from rag.neo4j_graph_rag import Neo4jGraphRAG

        uri = st.session_state.get("neo4j_uri", NEO4J_URI)
        user = st.session_state.get("neo4j_user", NEO4J_USER)
        password = st.session_state.get("neo4j_password", NEO4J_PASSWORD)

        candidate = Neo4jGraphRAG(uri, user, password)
        if candidate.verify_connection():
            st.session_state["_rag_instance"] = candidate
            rag = candidate
        else:
            return None

    token_budget = st.session_state.get("rag_token_budget", 20_000)
    top_k = st.session_state.get("rag_top_k", 8)

    # Map UI label to internal mode string
    internal_mode = "RAG" if mode == "RAG (semantic search)" else mode

    return get_context(rag, internal_mode, query, token_budget, top_k=top_k)
