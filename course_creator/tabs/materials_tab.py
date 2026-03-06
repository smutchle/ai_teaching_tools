import streamlit as st

from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from rag.document_loader import load_file, estimate_total_tokens

MAX_CONTEXT_TOKENS = 100_000
RESERVED_TOKENS = 20_000   # reserved for prompt + response


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_rag(force_reconnect: bool = False):
    """Return a connected Neo4jGraphRAG instance, cached in session state."""
    from rag.neo4j_graph_rag import Neo4jGraphRAG

    uri = st.session_state.get("neo4j_uri", NEO4J_URI)
    user = st.session_state.get("neo4j_user", NEO4J_USER)
    password = st.session_state.get("neo4j_password", NEO4J_PASSWORD)

    cached = st.session_state.get("_rag_instance")
    if cached is not None and not force_reconnect:
        return cached

    rag = Neo4jGraphRAG(uri, user, password)
    if rag.verify_connection():
        st.session_state["_rag_instance"] = rag
        return rag
    return None


def _build_database(uploaded_files):
    """Process uploaded files and load them into the Neo4j graph."""
    rag = _get_rag()
    if rag is None:
        st.error("Cannot connect to Neo4j. Check the connection settings below.")
        return False

    all_chunks = []
    for f in uploaded_files:
        chunks = load_file(f.read(), f.name)
        all_chunks.extend(chunks)
        st.write(f"  Loaded **{f.name}**: {len(chunks)} chunks")

    if not all_chunks:
        st.warning("No content extracted from the uploaded files.")
        return False

    st.write(f"Total: **{len(all_chunks)}** chunks (~{estimate_total_tokens(all_chunks):,} tokens)")

    try:
        rag.setup_schema()
        rag.clear_all()

        progress_bar = st.progress(0)
        rag.ingest_chunks(all_chunks, progress_callback=lambda p: progress_bar.progress(p))
        progress_bar.empty()

        stats = rag.get_stats()
        st.session_state["rag_db_stats"] = stats
        st.session_state["rag_db_ready"] = True
        return True
    except Exception as e:
        st.error(f"Error building RAG database: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Main render
# ──────────────────────────────────────────────────────────────────────────────

def render():
    st.header("Background Materials")

    st.info(
        "**Background materials are optional**, but uploading relevant source documents "
        "(textbooks, papers, syllabi, notes) can significantly reduce hallucination and "
        "ground the AI-generated content in your actual course materials. "
        "Once uploaded and indexed, you can choose how much context to pass to the LLM on each call."
    )

    # ── File Upload ───────────────────────────────────────────────────────────
    st.subheader("Upload Source Materials")

    uploaded_files = st.file_uploader(
        "Select files to upload (PDF, .md, .qmd, .txt)",
        type=["pdf", "md", "qmd", "txt"],
        accept_multiple_files=True,
        help="Documents will be chunked, embedded with sentence-transformers, "
             "and stored in a dedicated Neo4j graph database for retrieval.",
        key="source_material_uploader",
    )

    col_build, col_status = st.columns([1, 3])
    with col_build:
        if st.button(
            "Build RAG Database",
            disabled=not uploaded_files,
            key="build_rag_btn",
            type="primary",
        ):
            with st.spinner("Processing files and building graph database..."):
                success = _build_database(uploaded_files)
            if success:
                st.success("RAG database built successfully!")

    with col_status:
        stats = st.session_state.get("rag_db_stats")
        db_ready = st.session_state.get("rag_db_ready", False)
        if db_ready and stats:
            st.info(
                f"Database ready — "
                f"{stats['documents']} document(s), "
                f"{stats['chunks']} chunks, "
                f"~{stats['total_tokens']:,} tokens"
            )
        else:
            try:
                rag = _get_rag()
                if rag:
                    s = rag.get_stats()
                    if s["chunks"] > 0:
                        st.session_state["rag_db_stats"] = s
                        st.session_state["rag_db_ready"] = True
                        st.info(
                            f"Existing database — "
                            f"{s['documents']} document(s), "
                            f"{s['chunks']} chunks, "
                            f"~{s['total_tokens']:,} tokens"
                        )
                    else:
                        st.caption("No database built yet. Upload files and click Build.")
            except Exception:
                st.caption("Neo4j not reachable — RAG disabled.")

    st.divider()

    # ── RAG Context Settings ──────────────────────────────────────────────────
    st.subheader("Context Injection Settings")

    rag_mode = st.radio(
        "Context mode for LLM calls",
        options=["None", "RAG (semantic search)", "Full Materials"],
        index=["None", "RAG (semantic search)", "Full Materials"].index(
            st.session_state.get("rag_mode", "None")
        ),
        horizontal=True,
        help=(
            "**None** — no source material context is injected into prompts.\n\n"
            "**RAG (semantic search)** — the most relevant chunks are retrieved from the "
            "graph via vector similarity and injected. Best for large document sets.\n\n"
            "**Full Materials** — all stored document text is injected up to the token "
            "budget. Best for small, focused document sets."
        ),
    )
    st.session_state["rag_mode"] = rag_mode

    if rag_mode == "None":
        st.caption(
            "No context will be injected. Enable RAG or Full Materials to ground "
            "generation in your uploaded documents and reduce hallucination."
        )
    else:
        max_available = MAX_CONTEXT_TOKENS - RESERVED_TOKENS

        token_budget = st.slider(
            "Context token budget",
            min_value=1_000,
            max_value=max_available,
            value=st.session_state.get("rag_token_budget", 20_000),
            step=1_000,
            format="%d tokens",
            help=(
                f"How many tokens of source material to inject per LLM call. "
                f"The max context window is {MAX_CONTEXT_TOKENS:,} tokens; "
                f"{RESERVED_TOKENS:,} are reserved for the prompt and response."
            ),
        )
        st.session_state["rag_token_budget"] = token_budget

        pct = token_budget / MAX_CONTEXT_TOKENS * 100
        st.caption(
            f"Context budget: **{token_budget:,}** tokens "
            f"({pct:.0f}% of {MAX_CONTEXT_TOKENS:,} max window) — "
            f"**{RESERVED_TOKENS:,}** tokens reserved for prompt + response."
        )

        if rag_mode == "RAG (semantic search)":
            top_k = st.slider(
                "Chunks to retrieve (top-k)",
                min_value=1,
                max_value=30,
                value=st.session_state.get("rag_top_k", 8),
                help="Number of most-relevant chunks to fetch via vector similarity. "
                     "Graph neighbours of each chunk are also included for richer context.",
            )
            st.session_state["rag_top_k"] = top_k

    st.divider()

    # ── Neo4j Connection (advanced) ───────────────────────────────────────────
    with st.expander("Neo4j Connection Settings (advanced)", expanded=False):
        st.caption(
            "The run scripts start a dedicated `course_creator_neo4j` Docker container "
            "automatically. Only change these if you are using a different Neo4j instance."
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            neo4j_uri = st.text_input(
                "Neo4j URI",
                value=st.session_state.get("neo4j_uri", NEO4J_URI),
                key="neo4j_uri_input",
            )
            st.session_state["neo4j_uri"] = neo4j_uri
        with col2:
            neo4j_user = st.text_input(
                "Neo4j User",
                value=st.session_state.get("neo4j_user", NEO4J_USER),
                key="neo4j_user_input",
            )
            st.session_state["neo4j_user"] = neo4j_user
        with col3:
            neo4j_password = st.text_input(
                "Neo4j Password",
                value=st.session_state.get("neo4j_password", NEO4J_PASSWORD),
                type="password",
                key="neo4j_password_input",
            )
            st.session_state["neo4j_password"] = neo4j_password

        if st.button("Test Connection", key="test_neo4j_btn"):
            rag = _get_rag(force_reconnect=True)
            if rag:
                st.success("Neo4j connection successful.")
            else:
                st.error("Failed to connect. Check that the container is running and credentials are correct.")
