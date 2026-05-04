import time

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.quiz_state import (
    READING_DURATION,
    create_session,
    get_leaderboard,
    list_quizzes,
    load_bank,
    load_session,
    quiz_exists,
    session_exists,
    transition_status,
)

st.set_page_config(
    page_title="Host | QuizBlast",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
[data-testid="stSidebar"] { display: none; }
[data-testid="collapsedControl"] { display: none; }
</style>
""", unsafe_allow_html=True)

COLORS = ["#e21b3c", "#1368ce", "#d89e00", "#26890c"]
SHAPES = ["▲", "◆", "●", "■"]

# ── Navigation ────────────────────────────────────────────────────────────────
if st.button("← Back to Home"):
    st.switch_page("ai_quiz_game_app.py")

st.title("🎯 Host a Quiz")

# ── Quiz code entry ───────────────────────────────────────────────────────────
if "host_quiz_id" not in st.session_state:
    st.session_state.host_quiz_id = None

if not st.session_state.host_quiz_id:
    quizzes = list_quizzes()
    options = [
        f"{q['title']}  ({q['quiz_id']}, {q['total_questions']} questions)"
        for q in quizzes
    ]
    id_by_label = {label: q["quiz_id"] for label, q in zip(options, quizzes)}

    selection = st.selectbox(
        "Pick an existing quiz or type a 6-digit code",
        options,
        index=None,
        placeholder="Select a quiz or type a code…",
        accept_new_options=True,
    )

    if st.button("Load Quiz", type="primary", disabled=not selection):
        qid = id_by_label.get(selection, (selection or "").strip())
        if not quiz_exists(qid):
            st.error("Quiz not found. Double-check the code.")
        else:
            st.session_state.host_quiz_id = qid
            st.rerun()
    st.stop()

quiz_id = st.session_state.host_quiz_id
bank = load_bank(quiz_id)

if bank is None:
    st.error("Quiz data not found.")
    st.session_state.host_quiz_id = None
    st.stop()

if not session_exists(quiz_id):
    create_session(quiz_id)

session = load_session(quiz_id)
status = session["status"]
q_idx = session["current_question_idx"]
questions = bank["questions"]
total_q = len(questions)
time_per_q = bank["time_per_question"]

title_col, restart_col = st.columns([4, 1])
with title_col:
    st.markdown(f"### 📋 {bank['title']} &nbsp;|&nbsp; Code: **{quiz_id}**")
with restart_col:
    if st.session_state.get("confirm_restart"):
        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button("✅ Confirm", type="primary", use_container_width=True):
                create_session(quiz_id)
                st.session_state.confirm_restart = False
                st.session_state.host_quiz_id = None
                st.switch_page("ai_quiz_game_app.py")
        with cc2:
            if st.button("✖ Cancel", use_container_width=True):
                st.session_state.confirm_restart = False
                st.rerun()
    else:
        if st.button("🔄 Start Over", use_container_width=True,
                     help="Clear participants and scores, exit this quiz"):
            st.session_state.confirm_restart = True
            st.rerun()

st.divider()

# Single placeholder for the dynamic stage UI — re-rendering into this
# st.empty() each rerun guarantees the prior state's widgets/HTML are wiped.
stage = st.empty()

# =============================================================================
# LOBBY
# =============================================================================
if status == "lobby":
    start_clicked = False
    with stage.container():
        st.subheader("👥 Waiting Room")

        participants = session.get("participants", {})
        count = len(participants)

        if participants:
            cols = st.columns(min(count, 8))
            for i, p in enumerate(participants.values()):
                with cols[i % len(cols)]:
                    st.markdown(f"""
                    <div style="text-align:center; padding:12px; background:#1a1a2e;
                                 border-radius:10px; margin:4px;">
                        <div style="font-size:2em;">{p['emoji']}</div>
                        <div style="color:white; font-size:0.9em;">{p['name']}</div>
                    </div>
                    """, unsafe_allow_html=True)
            st.markdown(f"**{count} participant(s) ready**")
        else:
            st.info(f"No participants yet. Ask them to go to the Join page and enter code **{quiz_id}**.")

        if st.button(
            "▶ Start Quiz",
            type="primary",
            disabled=(count == 0),
            use_container_width=False,
        ):
            start_clicked = True

    if start_clicked:
        transition_status(
            quiz_id,
            "reading",
            current_question_idx=0,
            reading_start_time=time.time(),
            question_start_time=None,
        )
        st.rerun()

    time.sleep(2)
    st.rerun()

# =============================================================================
# READING (question shown without answers — gives students time to read)
# =============================================================================
elif status == "reading":
    question = questions[q_idx]
    elapsed = time.time() - session["reading_start_time"]
    remaining = max(0.0, READING_DURATION - elapsed)
    skip_reading = False

    with stage.container():
        prog_col, timer_col = st.columns([5, 1])
        with prog_col:
            st.progress(
                q_idx / total_q,
                text=f"Question {q_idx + 1} of {total_q} — Read carefully…",
            )
        with timer_col:
            st.markdown(f"""
            <div style="background:#1368ce; padding:10px; border-radius:8px; text-align:center;">
                <span style="color:white; font-size:2em; font-weight:bold;">{int(remaining) + (1 if remaining > int(remaining) else 0)}</span>
            </div>
            """, unsafe_allow_html=True)

        st.markdown(f"""
        <div style="background:#1a1a2e; padding:32px; border-radius:14px; margin:12px 0; text-align:center;">
            <h2 style="color:white; margin:0;">{question['question']}</h2>
            <p style="color:#888; margin-top:18px;">Answers appear when the timer ends.</p>
        </div>
        """, unsafe_allow_html=True)

        if st.button("⏭ Show Answers Now"):
            skip_reading = True

    if skip_reading or remaining <= 0:
        transition_status(
            quiz_id,
            "question",
            question_start_time=time.time(),
        )
        st.rerun()

    time.sleep(1)
    st.rerun()

# =============================================================================
# QUESTION
# =============================================================================
elif status == "question":
    question = questions[q_idx]
    elapsed = time.time() - session["question_start_time"]
    remaining = max(0.0, time_per_q - elapsed)
    show_results = False

    with stage.container():
        prog_col, timer_col = st.columns([5, 1])
        with prog_col:
            st.progress(
                q_idx / total_q,
                text=f"Question {q_idx + 1} of {total_q}",
            )
        with timer_col:
            t_color = "#26890c" if remaining > 5 else "#e21b3c"
            st.markdown(f"""
            <div style="background:{t_color}; padding:10px; border-radius:8px; text-align:center;">
                <span style="color:white; font-size:2em; font-weight:bold;">{int(remaining)}</span>
            </div>
            """, unsafe_allow_html=True)

        st.markdown(f"""
        <div style="background:#1a1a2e; padding:24px; border-radius:14px; margin:12px 0; text-align:center;">
            <h2 style="color:white; margin:0;">{question['question']}</h2>
        </div>
        """, unsafe_allow_html=True)

        if question.get("multiple_select"):
            st.info("⚠️ Multiple correct answers — participants must select all that apply.")

        tiles_html = []
        for i, (ans, color, shape) in enumerate(zip(question["answers"], COLORS, SHAPES)):
            tiles_html.append(
                f'<div style="background:{color};padding:18px;border-radius:10px;'
                f'color:white;font-size:1.1em;text-align:center;">'
                f'<strong style="font-size:1.3em;">{shape}</strong>&nbsp;&nbsp;{ans}</div>'
            )
        st.markdown(
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">'
            + "".join(tiles_html) +
            '</div>',
            unsafe_allow_html=True,
        )

        participants = session.get("participants", {})
        answered = sum(1 for p in participants.values() if str(q_idx) in p.get("answers", {}))
        total_p = len(participants)
        st.markdown(f"**{answered} / {total_p} answered**")
        st.progress(answered / max(total_p, 1))

        if st.button("⏭ Show Results Now"):
            show_results = True

    if show_results or remaining <= 0:
        transition_status(quiz_id, "answer_reveal")
        st.rerun()

    time.sleep(1)
    st.rerun()

# =============================================================================
# ANSWER REVEAL
# =============================================================================
elif status == "answer_reveal":
    question = questions[q_idx]
    correct_set = set(question["correct_indices"])
    next_clicked = False
    end_clicked = False

    with stage.container():
        st.subheader(f"✅ Results — Question {q_idx + 1} of {total_q}")

        st.markdown(f"""
        <div style="background:#1a1a2e; padding:16px; border-radius:12px; margin-bottom:16px; text-align:center;">
            <h3 style="color:white; margin:0;">{question['question']}</h3>
        </div>
        """, unsafe_allow_html=True)

        tiles_html = []
        for i, (ans, color, shape) in enumerate(zip(question["answers"], COLORS, SHAPES)):
            is_correct = i in correct_set
            opacity = "1.0" if is_correct else "0.4"
            border = "3px solid #FFD700" if is_correct else "1px solid transparent"
            check = " ✓" if is_correct else ""
            tiles_html.append(
                f'<div style="background:{color};padding:16px;border-radius:10px;'
                f'color:white;font-size:1.1em;border:{border};'
                f'opacity:{opacity};text-align:center;">'
                f'<strong>{shape}</strong>&nbsp;&nbsp;{ans}{check}</div>'
            )
        st.markdown(
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">'
            + "".join(tiles_html) +
            '</div>',
            unsafe_allow_html=True,
        )

        if question.get("explanation"):
            st.info(f"💡 {question['explanation']}")

        session_fresh = load_session(quiz_id)
        lb = get_leaderboard(session_fresh, top_n=5)

        if lb:
            st.subheader("🏆 Top 5")
            df = pd.DataFrame(
                [{"Player": f"{p['emoji']} {p['name']}", "Score": p["score"]} for p in lb]
            )
            fig = px.bar(
                df,
                x="Player",
                y="Score",
                color="Score",
                color_continuous_scale="viridis",
                text="Score",
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(
                plot_bgcolor="#0e1117",
                paper_bgcolor="#0e1117",
                font_color="white",
                showlegend=False,
                coloraxis_showscale=False,
                margin=dict(t=30, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)

        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if q_idx < total_q - 1:
                if st.button("▶ Next Question", type="primary", use_container_width=True):
                    next_clicked = True
            else:
                if st.button("🏁 End Quiz & Show Final Results", type="primary", use_container_width=True):
                    end_clicked = True

    if next_clicked:
        transition_status(
            quiz_id,
            "reading",
            current_question_idx=q_idx + 1,
            reading_start_time=time.time(),
            question_start_time=None,
        )
        st.rerun()
    if end_clicked:
        transition_status(quiz_id, "finished")
        st.rerun()

# =============================================================================
# FINISHED
# =============================================================================
elif status == "finished":
    st.balloons()
    st.markdown("""
    <h1 style="text-align:center; color:gold;">🏆 Quiz Complete!</h1>
    """, unsafe_allow_html=True)

    session_fresh = load_session(quiz_id)
    participants = session_fresh.get("participants", {})
    sorted_p = sorted(participants.values(), key=lambda x: x["score"], reverse=True)

    if sorted_p:
        winner = sorted_p[0]
        st.markdown(f"""
        <div style="text-align:center; padding:24px; background:#1a1a2e;
                     border-radius:14px; margin:16px 0;">
            <div style="font-size:4em;">{winner['emoji']}</div>
            <h2 style="color:gold; margin:8px 0;">🥇 {winner['name']}</h2>
            <h3 style="color:white; margin:0;">{winner['score']:,} points</h3>
        </div>
        """, unsafe_allow_html=True)

        st.subheader("Final Standings")
        medals = ["🥇", "🥈", "🥉"] + [f"{n}." for n in range(4, 51)]
        for rank, p in enumerate(sorted_p, 1):
            st.markdown(f"""
            <div style="display:flex; align-items:center; padding:10px; background:#1a1a2e;
                         border-radius:8px; margin:4px 0;">
                <span style="width:40px; font-size:1.3em;">{medals[rank - 1]}</span>
                <span style="font-size:1.5em; margin:0 10px;">{p['emoji']}</span>
                <span style="color:white; font-size:1.05em; flex:1;">{p['name']}</span>
                <span style="color:gold; font-weight:bold; font-size:1.1em;">
                    {p['score']:,} pts
                </span>
            </div>
            """, unsafe_allow_html=True)

        df = pd.DataFrame(
            [{"Player": f"{p['emoji']} {p['name']}", "Score": p["score"]} for p in sorted_p[:10]]
        )
        fig = px.bar(
            df, x="Player", y="Score", color="Score",
            color_continuous_scale="plasma", text="Score",
            title="Final Scores",
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(
            plot_bgcolor="#0e1117",
            paper_bgcolor="#0e1117",
            font_color="white",
            showlegend=False,
            coloraxis_showscale=False,
        )
        st.plotly_chart(fig, use_container_width=True)

    if st.button("🔄 Host Another Quiz"):
        st.session_state.host_quiz_id = None
        st.rerun()
