import random
from datetime import datetime, timezone

import streamlit as st

from utils.llm import DEFAULT_INSTRUCTIONS, PROVIDERS, QUESTIONS_PER_CHUNK, generate_questions
from utils.pdf_utils import process_files
from utils.quiz_state import (
    generate_quiz_id,
    list_question_banks,
    load_question_bank,
    load_bank,
    quiz_exists,
    save_bank,
    save_question_bank,
)

st.set_page_config(
    page_title="Setup | QuizBlast",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
[data-testid="stSidebar"] { display: none; }
[data-testid="collapsedControl"] { display: none; }
</style>
""", unsafe_allow_html=True)

if st.button("← Back to Home"):
    st.switch_page("Home.py")

st.title("⚙️ Setup")

# ── Session state init ────────────────────────────────────────────────────────
for key, default in [
    ("bank_questions", None),       # questions just generated (pre-save)
    ("bank_saved_id", None),        # bank_id after save
    ("quiz_saved_id", None),        # quiz code after quiz save
    ("last_bank_id_for_quiz", None),# tracks which bank is loaded in Phase 2
    ("llm_provider", PROVIDERS[0]), # selected LLM provider
]:
    if key not in st.session_state:
        st.session_state[key] = default

# =============================================================================
# PHASE 1 — BUILD A QUESTION BANK
# =============================================================================
st.header("Phase 1 — Build a Question Bank")
st.caption("Upload source materials and generate a reusable pool of questions.")

provider = st.radio(
    "LLM Provider",
    PROVIDERS,
    index=PROVIDERS.index(st.session_state.llm_provider),
    horizontal=True,
)
st.session_state.llm_provider = provider

st.divider()

uploaded_files = st.file_uploader(
    "Upload PDFs, Markdown, or text files",
    type=["pdf", "md", "markdown", "txt"],
    accept_multiple_files=True,
    key="bank_uploader",
)

if uploaded_files:
    names = ", ".join(f.name for f in uploaded_files)
    st.caption(f"Files: {names}")

supplemental_instructions = st.text_area(
    "Supplemental instructions for AI",
    value=DEFAULT_INSTRUCTIONS,
    height=90,
)
st.caption(f"Questions generated automatically — {QUESTIONS_PER_CHUNK} per 40k-character chunk.")

can_generate = bool(uploaded_files)
if not can_generate:
    st.info("Upload at least one file to generate a question bank.")

if st.button("🤖 Generate Question Bank", disabled=not can_generate, type="primary"):
    st.session_state.bank_questions = None
    st.session_state.bank_saved_id = None

    with st.spinner("Processing files…"):
        chunks = process_files(uploaded_files)

    from utils.llm import _questions_for_chunk
    est = sum(_questions_for_chunk(c) for c in chunks)
    st.info(f"Text extracted in {len(chunks)} chunk(s). Generating ~{est} questions…")

    with st.spinner(f"Calling {provider} (context-only mode)…"):
        questions = generate_questions(chunks, supplemental_instructions, provider)

    if not questions:
        st.error("No questions returned. Check your API settings and try again.")
    else:
        st.session_state.bank_questions = questions
        st.success(f"✅ Generated {len(questions)} questions — review below, then save.")
        st.rerun()

# ── Review generated questions ────────────────────────────────────────────────
if st.session_state.bank_questions:
    questions = st.session_state.bank_questions
    st.markdown(f"**{len(questions)} questions generated** — expand to review/edit before saving.")

    for i, q in enumerate(questions):
        with st.expander(f"Q{i + 1}: {q['question'][:100]}", expanded=False):
            q["question"] = st.text_area(
                "Question", value=q["question"], key=f"bq_text_{i}", height=70
            )
            st.markdown("**Answers** — check correct answer(s):")
            new_answers, new_correct = [], []
            for j, ans in enumerate(q["answers"]):
                c1, c2 = st.columns([1, 8])
                with c1:
                    chk = st.checkbox(
                        "correct", value=(j in q.get("correct_indices", [])),
                        key=f"bchk_{i}_{j}", label_visibility="collapsed",
                    )
                with c2:
                    txt = st.text_input(
                        f"A{j+1}", value=ans, key=f"bans_{i}_{j}",
                        label_visibility="collapsed",
                    )
                new_answers.append(txt)
                if chk:
                    new_correct.append(j)
            q["answers"] = new_answers
            q["correct_indices"] = new_correct
            q["multiple_select"] = len(new_correct) > 1
            if q.get("explanation"):
                st.caption(f"💡 {q['explanation']}")

    st.divider()
    save_col, status_col = st.columns([2, 3])
    with save_col:
        if st.button("💾 Save Question Bank", type="primary", use_container_width=True):
            bank_name = ", ".join(f.name for f in uploaded_files)
            bank_id = generate_quiz_id()
            save_question_bank(bank_id, {
                "bank_id": bank_id,
                "name": bank_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "questions": st.session_state.bank_questions,
            })
            st.session_state.bank_saved_id = bank_id
            st.rerun()

    with status_col:
        if st.session_state.bank_saved_id:
            bid = st.session_state.bank_saved_id
            n = len(st.session_state.bank_questions)
            st.success(f"✅ Bank saved — {n} questions  ·  ID: `{bid}`")

st.divider()

# =============================================================================
# PHASE 2 — CREATE A QUIZ FROM A BANK
# =============================================================================
st.header("Phase 2 — Create a Quiz from a Bank")
st.caption("Pick a saved bank, randomly sample N questions, adjust selection, then save.")

banks = list_question_banks()

if not banks:
    st.info("No question banks yet — complete Phase 1 first.")
    st.stop()

# Bank selector
bank_options = {f"{b['name']}  ({b['total_questions']} Qs)": b["bank_id"] for b in banks}
selected_label = st.selectbox("Select a question bank", list(bank_options.keys()))
selected_bank_id = bank_options[selected_label]

# When the bank selection changes, clear the checkbox states from the old bank
if st.session_state.last_bank_id_for_quiz != selected_bank_id:
    qbank = load_question_bank(selected_bank_id)
    if qbank:
        for i in range(len(qbank["questions"])):
            st.session_state.pop(f"selq_{i}", None)
    st.session_state.last_bank_id_for_quiz = selected_bank_id

qbank = load_question_bank(selected_bank_id)
if qbank is None:
    st.error("Could not load the selected bank.")
    st.stop()

all_questions = qbank["questions"]
bank_size = len(all_questions)

col1, col2, col3 = st.columns(3)
with col1:
    quiz_title = st.text_input("Quiz Title", placeholder="e.g., Week 3 Review")
with col2:
    n_quiz_q = st.number_input(
        "Questions in this quiz",
        min_value=1, max_value=bank_size, value=min(10, bank_size),
        help=f"Bank has {bank_size} questions.",
    )
with col3:
    time_per_q = st.number_input(
        "Seconds per question", min_value=5, max_value=120, value=30
    )

# Randomize button
if st.button("🎲 Randomize Selection", help="Randomly pick N questions from the bank"):
    selected_indices = random.sample(range(bank_size), min(n_quiz_q, bank_size))
    selected_set = set(selected_indices)
    for i in range(bank_size):
        st.session_state[f"selq_{i}"] = i in selected_set
    st.rerun()

# ── Question checklist ────────────────────────────────────────────────────────
n_checked = sum(1 for i in range(bank_size) if st.session_state.get(f"selq_{i}", False))
any_initialized = any(f"selq_{i}" in st.session_state for i in range(bank_size))

if not any_initialized:
    st.info("Click **Randomize Selection** to pick a starting set, then adjust below.")
else:
    delta_color = "normal" if n_checked == n_quiz_q else "inverse"
    st.markdown(
        f"**{n_checked} selected** "
        + (f"✅" if n_checked == n_quiz_q else f"— target is {n_quiz_q}, adjust below"),
    )

    for i, q in enumerate(all_questions):
        label = f"**Q{i+1}:** {q['question'][:120]}"
        st.checkbox(label, key=f"selq_{i}")

    # Recompute after render
    n_checked = sum(1 for i in range(bank_size) if st.session_state.get(f"selq_{i}", False))

    st.divider()
    can_save = bool(quiz_title.strip()) and n_checked > 0
    if not can_save:
        st.info("Enter a quiz title and select at least one question to save.")

    save_col2, status_col2 = st.columns([2, 3])
    with save_col2:
        if st.button(
            f"💾 Save Quiz ({n_checked} questions)",
            type="primary",
            disabled=not can_save,
            use_container_width=True,
        ):
            chosen = [
                all_questions[i]
                for i in range(bank_size)
                if st.session_state.get(f"selq_{i}", False)
            ]
            qid = generate_quiz_id()
            while quiz_exists(qid):
                qid = generate_quiz_id()

            save_bank(qid, {
                "quiz_id": qid,
                "title": quiz_title.strip(),
                "time_per_question": int(time_per_q),
                "questions": chosen,
            })
            st.session_state.quiz_saved_id = qid
            st.rerun()

    with status_col2:
        if st.session_state.quiz_saved_id:
            qid = st.session_state.quiz_saved_id
            saved = load_bank(qid)
            n_saved = len(saved["questions"]) if saved else "?"
            st.success("Quiz saved!")
            st.markdown(f"""
            <div style="background:#0e1117; border:2px solid #4fc3f7; padding:16px;
                        border-radius:10px; text-align:center;">
                <p style="color:#aaa; margin:0 0 4px 0; font-size:0.9em;">Quiz Code</p>
                <span style="color:#4fc3f7; font-size:2.8em; font-weight:bold;
                             letter-spacing:10px;">{qid}</span>
                <p style="color:#aaa; margin:4px 0 0 0; font-size:0.85em;">
                    {n_saved} questions · share with participants
                </p>
            </div>
            """, unsafe_allow_html=True)

            if st.button("🎯 Host This Quiz Now", use_container_width=True):
                st.session_state["host_quiz_id"] = qid
                st.switch_page("pages/2_Host.py")
