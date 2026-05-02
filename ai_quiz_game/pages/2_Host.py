import time

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.quiz_state import (
    create_session,
    get_leaderboard,
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
    st.switch_page("Home.py")

st.title("🎯 Host a Quiz")

# ── Quiz code entry ───────────────────────────────────────────────────────────
if "host_quiz_id" not in st.session_state:
    st.session_state.host_quiz_id = None

if not st.session_state.host_quiz_id:
    qid_input = st.text_input("Enter Quiz Code", max_chars=8, placeholder="6-digit code")
    if st.button("Load Quiz", type="primary"):
        qid = qid_input.strip()
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

st.markdown(f"### 📋 {bank['title']} &nbsp;|&nbsp; Code: **{quiz_id}**")

# ── Reset button (sidebar-like controls) ─────────────────────────────────────
with st.expander("⚠️ Session Controls", expanded=False):
    if st.button("🔄 Reset Session (clear participants & restart)"):
        create_session(quiz_id)
        st.rerun()

st.divider()

# =============================================================================
# LOBBY
# =============================================================================
if status == "lobby":
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
        transition_status(
            quiz_id,
            "question",
            current_question_idx=0,
            question_start_time=time.time(),
        )
        st.rerun()

    time.sleep(2)
    st.rerun()

# =============================================================================
# QUESTION
# =============================================================================
elif status == "question":
    question = questions[q_idx]
    elapsed = time.time() - session["question_start_time"]
    remaining = max(0.0, time_per_q - elapsed)

    # Progress bar + timer
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

    # Question card
    st.markdown(f"""
    <div style="background:#1a1a2e; padding:24px; border-radius:14px; margin:12px 0; text-align:center;">
        <h2 style="color:white; margin:0;">{question['question']}</h2>
    </div>
    """, unsafe_allow_html=True)

    if question.get("multiple_select"):
        st.info("⚠️ Multiple correct answers — participants must select all that apply.")

    # Answer tiles (display only — host doesn't answer)
    c1, c2 = st.columns(2)
    for i, (ans, color, shape) in enumerate(zip(question["answers"], COLORS, SHAPES)):
        col = c1 if i % 2 == 0 else c2
        with col:
            st.markdown(f"""
            <div style="background:{color}; padding:18px; border-radius:10px; margin:6px 0;
                         color:white; font-size:1.1em; text-align:center;">
                <strong style="font-size:1.3em;">{shape}</strong>&nbsp;&nbsp;{ans}
            </div>
            """, unsafe_allow_html=True)

    # Answer count
    participants = session.get("participants", {})
    answered = sum(1 for p in participants.values() if str(q_idx) in p.get("answers", {}))
    total_p = len(participants)
    st.markdown(f"**{answered} / {total_p} answered**")
    st.progress(answered / max(total_p, 1))

    if st.button("⏭ Show Results Now"):
        transition_status(quiz_id, "answer_reveal")
        st.rerun()

    if remaining <= 0:
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

    st.subheader(f"✅ Results — Question {q_idx + 1} of {total_q}")

    # Question recap
    st.markdown(f"""
    <div style="background:#1a1a2e; padding:16px; border-radius:12px; margin-bottom:16px; text-align:center;">
        <h3 style="color:white; margin:0;">{question['question']}</h3>
    </div>
    """, unsafe_allow_html=True)

    # Answers highlighted
    c1, c2 = st.columns(2)
    for i, (ans, color, shape) in enumerate(zip(question["answers"], COLORS, SHAPES)):
        col = c1 if i % 2 == 0 else c2
        is_correct = i in correct_set
        opacity = "1.0" if is_correct else "0.4"
        border = "3px solid #FFD700" if is_correct else "1px solid transparent"
        check = " ✓" if is_correct else ""
        with col:
            st.markdown(f"""
            <div style="background:{color}; padding:16px; border-radius:10px; margin:6px 0;
                         color:white; font-size:1.1em; border:{border};
                         opacity:{opacity}; text-align:center;">
                <strong>{shape}</strong>&nbsp;&nbsp;{ans}{check}
            </div>
            """, unsafe_allow_html=True)

    if question.get("explanation"):
        st.info(f"💡 {question['explanation']}")

    # Leaderboard chart
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

    # Navigation
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if q_idx < total_q - 1:
            if st.button("▶ Next Question", type="primary", use_container_width=True):
                transition_status(
                    quiz_id,
                    "question",
                    current_question_idx=q_idx + 1,
                    question_start_time=time.time(),
                )
                st.rerun()
        else:
            if st.button("🏁 End Quiz & Show Final Results", type="primary", use_container_width=True):
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
