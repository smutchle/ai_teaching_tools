import random
from datetime import datetime, timezone

import streamlit as st
from vt_banner import render_vt_banner

from utils.llm import (
    DEFAULT_INSTRUCTIONS,
    IMPORT_CHARS_PER_CHUNK,
    PROVIDERS,
    QUESTIONS_PER_CHUNK,
    generate_questions,
    import_questions,
)
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
render_vt_banner()

st.markdown("""
<style>
[data-testid="stSidebar"] { display: none; }
[data-testid="collapsedControl"] { display: none; }
</style>
""", unsafe_allow_html=True)

if st.button("← Back to Home"):
    st.switch_page("ai_quiz_game_app.py")

st.title("⚙️ Setup")

# ── Session state init ────────────────────────────────────────────────────────
for key, default in [
    ("bank_questions", None),       # questions just generated (pre-save)
    ("bank_saved_id", None),        # bank_id after save
    ("bank_source_name", None),     # uploaded filename(s) the bank was built from
    ("quiz_saved_id", None),        # quiz code after quiz save
    ("llm_provider", PROVIDERS[0]), # selected LLM provider
]:
    if key not in st.session_state:
        st.session_state[key] = default

# =============================================================================
# PHASE 1 — BUILD A QUESTION BANK
# =============================================================================
st.header("Phase 1 — Build a Question Bank")
st.caption(
    "Upload source materials to generate a reusable pool of questions, "
    "or import questions you've already written."
)

def _reset_bank_review(source_files: list) -> None:
    """Clear the previous bank and its review-widget state before a new build."""
    st.session_state.bank_questions = None
    st.session_state.bank_saved_id = None
    st.session_state.bank_source_name = ", ".join(f.name for f in source_files)
    for k in [k for k in st.session_state if k.startswith(("bq_text_", "bchk_", "bans_"))]:
        del st.session_state[k]


MODE_GENERATE = "🤖 Generate from materials"
MODE_IMPORT = "📥 Import pre-built questions"
bank_mode = st.radio("Source", [MODE_GENERATE, MODE_IMPORT], horizontal=True)

provider = st.radio(
    "LLM Provider",
    PROVIDERS,
    index=PROVIDERS.index(st.session_state.llm_provider),
    horizontal=True,
)
st.session_state.llm_provider = provider

st.divider()

if bank_mode == MODE_GENERATE:
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
        _reset_bank_review(uploaded_files)

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

else:
    uploaded_files = st.file_uploader(
        "Upload your questions (PDF, Markdown, or text)",
        type=["pdf", "md", "markdown", "txt"],
        accept_multiple_files=True,
        key="import_uploader",
    )
    with st.expander("Formatting tips"):
        st.markdown(
            "Write the questions however you like, but mark the correct answer(s) clearly. "
            "Each question needs 2–4 options; true/false works too. The AI copies your wording "
            "and answer order as written and doesn't invent anything. Questions with no "
            "identifiable answer are skipped.\n\n"
            "```text\n"
            "1. Which planet is closest to the sun?\n"
            "   a) Venus\n"
            "   b) Mercury\n"
            "   c) Mars\n"
            "   d) Earth\n"
            "   Answer: b\n"
            "   Explanation: Mercury orbits at about 0.39 AU.\n\n"
            "2. Select all prime numbers:  2 (correct), 4, 7 (correct), 9\n\n"
            "3. True or false: water boils at 90°C at sea level.  Answer: False\n"
            "```"
        )

    if uploaded_files:
        names = ", ".join(f.name for f in uploaded_files)
        st.caption(f"Files: {names}")
    else:
        st.info("Upload at least one file containing your questions and answers.")

    if st.button("📥 Import Question Bank", disabled=not uploaded_files, type="primary"):
        _reset_bank_review(uploaded_files)

        with st.spinner("Processing files…"):
            chunks = process_files(uploaded_files, max_chars=IMPORT_CHARS_PER_CHUNK)

        with st.spinner(f"Calling {provider} to parse {len(chunks)} chunk(s)…"):
            questions = import_questions(chunks, provider)

        if not questions:
            st.error(
                "No questions could be parsed. Make sure each question has options "
                "and a clearly marked correct answer."
            )
        else:
            st.session_state.bank_questions = questions
            st.success(
                f"✅ Imported {len(questions)} questions. Check the count and the correct "
                "answers below, then save."
            )
            st.rerun()

# ── Review generated / imported questions ─────────────────────────────────────
if st.session_state.bank_questions:
    questions = st.session_state.bank_questions
    st.markdown(f"**{len(questions)} questions** — expand to review/edit before saving.")

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
            bank_name = st.session_state.bank_source_name
            bank_id = generate_quiz_id()
            while load_question_bank(bank_id) is not None:
                bank_id = generate_quiz_id()
            save_question_bank(bank_id, {
                "bank_id": bank_id,
                "name": bank_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "questions": st.session_state.bank_questions,
            })
            st.session_state.bank_saved_id = bank_id
            st.session_state.quiz_bank_select = bank_id
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

# Bank selector. Options are bank IDs (labels can collide: same file, same count)
# and the widget is keyed so its selection survives the bank list changing.
bank_by_id = {b["bank_id"]: b for b in banks}
if st.session_state.get("quiz_bank_select") not in bank_by_id:
    st.session_state.quiz_bank_select = banks[0]["bank_id"]
selected_bank_id = st.selectbox(
    "Select a question bank",
    list(bank_by_id),
    format_func=lambda bid: (
        f"{bank_by_id[bid]['name']}  ({bank_by_id[bid]['total_questions']} Qs)  ·  ID {bid}"
    ),
    key="quiz_bank_select",
)


def _sel_key(i: int) -> str:
    """Checkbox key for question i of the selected bank."""
    return f"selq_{selected_bank_id}_{i}"


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
        st.session_state[_sel_key(i)] = i in selected_set
    st.rerun()

# ── Question checklist ────────────────────────────────────────────────────────
n_checked = sum(1 for i in range(bank_size) if st.session_state.get(_sel_key(i), False))
any_initialized = any(_sel_key(i) in st.session_state for i in range(bank_size))

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
        st.checkbox(label, key=_sel_key(i))

    # Recompute after render
    n_checked = sum(1 for i in range(bank_size) if st.session_state.get(_sel_key(i), False))

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
                if st.session_state.get(_sel_key(i), False)
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
