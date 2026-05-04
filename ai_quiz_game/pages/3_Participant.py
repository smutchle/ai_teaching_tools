import json
import time
import uuid

import streamlit as st
import streamlit.components.v1 as components

from utils.quiz_state import (
    READING_DURATION,
    add_participant,
    calculate_points,
    create_session,
    get_leaderboard,
    load_bank,
    load_session,
    quiz_exists,
    session_exists,
    submit_answer,
)

st.set_page_config(
    page_title="Play | QuizBlast",
    page_icon="🙋",
    layout="centered",
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

EMOJIS = [
    "🦊", "🐸", "🦁", "🐼", "🦄", "🐶", "🐱", "🐺", "🦋", "🐙",
    "🦅", "🐬", "🦉", "🦎", "🐲", "🚀", "⭐", "🎸", "🏆", "🎯",
    "🔥", "💎", "🌈", "⚡", "🎭", "🎪", "🧸", "🌟", "💫", "🎆",
]


def inject_answer_colors(colors: list[str], shapes: list[str]) -> None:
    """Inject JS via a same-origin iframe to directly style the answer buttons."""
    script = f"""
    <script>
    (function() {{
        const colors = {json.dumps(colors)};
        const shapes = {json.dumps(shapes)};
        function apply() {{
            try {{
                const doc = window.parent.document;
                const btns = Array.from(doc.querySelectorAll('button')).filter(b =>
                    shapes.some(s => b.innerText.trim().startsWith(s))
                );
                btns.forEach(btn => {{
                    const shapeIdx = shapes.findIndex(
                        s => btn.innerText.trim().startsWith(s)
                    );
                    if (shapeIdx < 0) return;
                    btn.style.setProperty('background-color', colors[shapeIdx], 'important');
                    btn.style.setProperty('color', 'white', 'important');
                    btn.style.setProperty('border', 'none', 'important');
                    btn.style.setProperty('min-height', '80px', 'important');
                    btn.style.setProperty('font-size', '1.1em', 'important');
                    btn.style.setProperty('font-weight', 'bold', 'important');
                }});
            }} catch(e) {{}}
        }}
        apply();
        setTimeout(apply, 100);
        setTimeout(apply, 400);
    }})();
    </script>
    """
    components.html(script, height=0)


# ── Session-state init ────────────────────────────────────────────────────────
if "participant_id" not in st.session_state:
    st.session_state.participant_id = str(uuid.uuid4())
if "participant_name" not in st.session_state:
    st.session_state.participant_name = None
if "participant_emoji" not in st.session_state:
    st.session_state.participant_emoji = EMOJIS[0]
if "participant_quiz_id" not in st.session_state:
    st.session_state.participant_quiz_id = None
if "joined" not in st.session_state:
    st.session_state.joined = False
if "selected_emoji" not in st.session_state:
    st.session_state.selected_emoji = EMOJIS[0]

# Restore join state from query params after WebSocket reconnect
if not st.session_state.joined:
    qp = st.query_params
    if all(k in qp for k in ("qid", "pid", "name", "emoji")):
        st.session_state.participant_quiz_id = qp["qid"]
        st.session_state.participant_id = qp["pid"]
        st.session_state.participant_name = qp["name"]
        st.session_state.participant_emoji = qp["emoji"]
        st.session_state.selected_emoji = qp["emoji"]
        st.session_state.joined = True
        add_participant(qp["qid"], qp["pid"], qp["name"], qp["emoji"])

pid = st.session_state.participant_id

# =============================================================================
# JOIN FORM
# =============================================================================
if not st.session_state.joined:
    if st.button("← Back to Home"):
        st.switch_page("ai_quiz_game_app.py")

    st.title("🙋 Join a Quiz")

    form_area = st.empty()
    join_data = None  # set inside the with block, acted on outside it

    with form_area.container():
        quiz_code = st.text_input("Quiz Code", max_chars=8, placeholder="6-digit code")
        player_name = st.text_input("Your Name", max_chars=20, placeholder="Enter your name")

        st.markdown("**Pick your emoji:**")
        rows = [EMOJIS[i : i + 6] for i in range(0, len(EMOJIS), 6)]
        for row in rows:
            cols = st.columns(len(row))
            for col, emo in zip(cols, row):
                with col:
                    if st.button(emo, key=f"ep_{emo}"):
                        st.session_state.selected_emoji = emo
                        st.rerun()

        selected_emoji = st.session_state.selected_emoji
        st.markdown(f"**Selected:** {selected_emoji}")

        can_join = bool(quiz_code.strip()) and bool(player_name.strip())

        if st.button("🚀 Join!", type="primary", disabled=not can_join, use_container_width=True):
            qid = quiz_code.strip()
            if not quiz_exists(qid):
                st.error("Quiz not found. Check the code.")
            else:
                sess = load_session(qid)
                if sess and sess["status"] == "finished":
                    st.error("This quiz has already ended.")
                else:
                    join_data = {"qid": qid, "name": player_name.strip(), "emoji": selected_emoji}

    # empty() is safe here — we are outside the with block
    if join_data:
        form_area.empty()
        with st.spinner("Joining…"):
            if not session_exists(join_data["qid"]):
                create_session(join_data["qid"])
            add_participant(join_data["qid"], pid, join_data["name"], join_data["emoji"])
        st.session_state.participant_quiz_id = join_data["qid"]
        st.session_state.participant_name = join_data["name"]
        st.session_state.participant_emoji = join_data["emoji"]
        st.session_state.joined = True
        st.query_params["qid"] = join_data["qid"]
        st.query_params["pid"] = pid
        st.query_params["name"] = join_data["name"]
        st.query_params["emoji"] = join_data["emoji"]
        st.rerun()

    st.stop()

# =============================================================================
# IN-GAME VIEW
# =============================================================================
quiz_id = st.session_state.participant_quiz_id
name = st.session_state.participant_name
emoji = st.session_state.participant_emoji

bank = load_bank(quiz_id)
session = load_session(quiz_id)

if session is None or bank is None:
    st.warning("Session unavailable — retrying…")
    time.sleep(2)
    st.rerun()

status = session["status"]
q_idx = session["current_question_idx"]
questions = bank["questions"]
time_per_q = bank["time_per_question"]
p_data = session["participants"].get(pid, {"score": 0, "answers": {}})

# Player header
st.markdown(f"""
<div style="display:flex; align-items:center; gap:12px; padding:8px 0 12px 0;
             border-bottom:1px solid #333; margin-bottom:12px;">
    <span style="font-size:2em;">{emoji}</span>
    <span style="font-size:1.2em; font-weight:bold; color:white;">{name}</span>
    <span style="margin-left:auto; color:gold; font-size:1.1em; font-weight:bold;">
        ⭐ {p_data.get('score', 0):,} pts
    </span>
</div>
""", unsafe_allow_html=True)

# Single placeholder that owns the dynamic game UI. Re-rendering into this
# st.empty() each rerun guarantees the prior state's widgets are wiped — no
# leftover answer tiles bleeding from one question into the next.
main = st.empty()

# =============================================================================
# LOBBY WAIT
# =============================================================================
if status == "lobby":
    with main.container():
        st.markdown("""
        <div style="text-align:center; padding:60px 20px; background:#1a1a2e; border-radius:14px;">
            <div style="font-size:3em; margin-bottom:12px;">⏳</div>
            <h2 style="color:white; margin:0;">Waiting for the host to start…</h2>
            <p style="color:#888; margin-top:8px;">Get ready! The quiz is about to begin.</p>
        </div>
        """, unsafe_allow_html=True)

        participants = session.get("participants", {})
        if participants:
            avatars = "  ".join(p["emoji"] for p in participants.values())
            st.markdown(
                f"<p style='font-size:2em; text-align:center; margin-top:16px;'>{avatars}</p>",
                unsafe_allow_html=True,
            )
            st.caption(f"{len(participants)} player(s) in the lobby")

    time.sleep(2)
    st.rerun()

# =============================================================================
# READING (question shown, answers hidden)
# =============================================================================
elif status == "reading":
    question = questions[q_idx]
    elapsed = time.time() - session["reading_start_time"]
    remaining = max(0.0, READING_DURATION - elapsed)
    seconds = int(remaining) + (1 if remaining > int(remaining) else 0)
    progress_pct = int(q_idx / len(questions) * 100)

    main.markdown(
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">'
        f'<div style="flex:1;color:#aaa;font-size:0.95em;">Q{q_idx + 1} of {len(questions)} — Read carefully…'
        '<div style="background:#262730;height:6px;border-radius:3px;margin-top:4px;overflow:hidden;">'
        f'<div style="background:#1368ce;height:100%;width:{progress_pct}%;"></div>'
        '</div></div>'
        '<div style="background:#1368ce;padding:8px 14px;border-radius:8px;min-width:55px;text-align:center;">'
        f'<span style="color:white;font-size:1.8em;font-weight:bold;">{seconds}</span>'
        '</div></div>'
        '<div style="background:#1a1a2e;padding:28px;border-radius:12px;margin:10px 0;'
        'text-align:center;min-height:80px;">'
        f'<h2 style="color:white;margin:0;line-height:1.3;">{question["question"]}</h2>'
        '<p style="color:#888;margin-top:14px;">Answers appear when the timer ends.</p>'
        '</div>',
        unsafe_allow_html=True,
    )

    time.sleep(1)
    st.rerun()

# =============================================================================
# QUESTION
# =============================================================================
elif status == "question":
    question = questions[q_idx]
    elapsed = time.time() - session["question_start_time"]
    remaining = max(0.0, time_per_q - elapsed)
    already_answered = str(q_idx) in p_data.get("answers", {})
    clicked = None
    submit_multi = False

    with main.container():
        # Progress + timer
        p_col, t_col = st.columns([5, 1])
        with p_col:
            st.progress(q_idx / len(questions), text=f"Q{q_idx + 1} of {len(questions)}")
        with t_col:
            t_color = "#26890c" if remaining > 5 else "#e21b3c"
            st.markdown(f"""
            <div style="background:{t_color}; padding:8px; border-radius:8px; text-align:center;">
                <span style="color:white; font-size:1.8em; font-weight:bold;">{int(remaining)}</span>
            </div>
            """, unsafe_allow_html=True)

        # Question card
        st.markdown(f"""
        <div style="background:#1a1a2e; padding:20px; border-radius:12px; margin:10px 0;
                     text-align:center; min-height:80px; display:flex; align-items:center;
                     justify-content:center;">
            <h2 style="color:white; margin:0; line-height:1.3;">{question['question']}</h2>
        </div>
        """, unsafe_allow_html=True)

        if question.get("multiple_select") and not already_answered and remaining > 0:
            st.info("⚠️ Multiple correct answers — select all that apply, then submit.")

        # ── Already answered ──
        if already_answered:
            submitted = p_data["answers"][str(q_idx)]
            pts = submitted["points"]
            picks = ", ".join(
                f"{SHAPES[i]} {question['answers'][i]}"
                for i in submitted["answer_indices"]
            )
            st.markdown(f"""
            <div style="background:#1a1a2e; padding:24px; border-radius:12px;
                         text-align:center; margin:10px 0;">
                <div style="font-size:2.4em;">✅</div>
                <h3 style="color:white; margin:6px 0;">Answer submitted</h3>
                <p style="color:#aaa; margin:0;">Your pick: <strong style="color:white;">{picks}</strong></p>
                <p style="color:gold; font-weight:bold; margin-top:8px;">+{pts} pts pending</p>
                <p style="color:#888; margin-top:14px; font-size:0.9em;">Waiting for the host…</p>
            </div>
            """, unsafe_allow_html=True)

        # ── Time's up but no answer ──
        elif remaining <= 0:
            st.warning("⏰ Time's up!")

        # ── Active answering ──
        else:
            if question.get("multiple_select"):
                c1, c2 = st.columns(2)
                for i, (ans, color, shape) in enumerate(zip(question["answers"], COLORS, SHAPES)):
                    col = c1 if i % 2 == 0 else c2
                    with col:
                        st.markdown(f"""
                        <div style="background:{color}; padding:4px 10px; border-radius:6px 6px 0 0;
                                     margin-bottom:-2px;">
                            <span style="color:white; font-weight:bold; font-size:1.2em;">{shape}</span>
                        </div>
                        """, unsafe_allow_html=True)
                        st.checkbox(ans, key=f"ms_{q_idx}_{i}")

                selected_multi = [
                    i for i in range(4) if st.session_state.get(f"ms_{q_idx}_{i}", False)
                ]
                if selected_multi:
                    if st.button("✅ Submit Answers", type="primary", use_container_width=True):
                        submit_multi = True
            else:
                c1, c2 = st.columns(2)
                for i, (ans, shape) in enumerate(zip(question["answers"], SHAPES)):
                    col = c1 if i % 2 == 0 else c2
                    with col:
                        if st.button(f"{shape}  {ans}", key=f"ans_{q_idx}_{i}",
                                     use_container_width=True):
                            clicked = i
                inject_answer_colors(COLORS, SHAPES)

    # Process answer submissions outside the container (so the rerun-triggered
    # render uses a fresh container, not the old one).
    if submit_multi:
        selected_multi = [
            i for i in range(4) if st.session_state.get(f"ms_{q_idx}_{i}", False)
        ]
        time_taken = time.time() - session["question_start_time"]
        correct_set = set(question["correct_indices"])
        is_correct = set(selected_multi) == correct_set
        pts = calculate_points(time_taken, time_per_q) if is_correct else 0
        submit_answer(quiz_id, pid, selected_multi, time_taken, pts)
        st.rerun()
    if clicked is not None:
        time_taken = time.time() - session["question_start_time"]
        correct_set = set(question["correct_indices"])
        is_correct = clicked in correct_set
        pts = calculate_points(time_taken, time_per_q) if is_correct else 0
        submit_answer(quiz_id, pid, [clicked], time_taken, pts)
        st.rerun()

    time.sleep(1)
    st.rerun()

# =============================================================================
# ANSWER REVEAL
# =============================================================================
elif status == "answer_reveal":
    question = questions[q_idx]
    correct_set = set(question["correct_indices"])
    my_answer = p_data.get("answers", {}).get(str(q_idx))

    # Build the entire reveal view as a single HTML string and render it via
    # main.markdown() — exactly one element in the placeholder, so the next
    # state transition replaces it cleanly with no residue.
    if my_answer:
        my_set = set(my_answer["answer_indices"])
        is_correct = my_set == correct_set
        pts_earned = my_answer["points"]
        if is_correct:
            banner = (
                '<div style="background:#26890c;padding:18px;border-radius:12px;'
                'text-align:center;margin-bottom:16px;">'
                f'<h2 style="color:white;margin:0;">✅ Correct! +{pts_earned} pts</h2>'
                '</div>'
            )
        else:
            banner = (
                '<div style="background:#e21b3c;padding:18px;border-radius:12px;'
                'text-align:center;margin-bottom:16px;">'
                '<h2 style="color:white;margin:0;">❌ Not quite right</h2>'
                '</div>'
            )
    else:
        banner = (
            '<div style="background:#f59f00;padding:14px;border-radius:10px;'
            'text-align:center;margin-bottom:16px;color:white;font-weight:bold;">'
            "⏰ You didn't answer in time — 0 pts"
            '</div>'
        )

    tiles_html = []
    for i, (ans, color, shape) in enumerate(zip(question["answers"], COLORS, SHAPES)):
        is_right = i in correct_set
        opacity = "1.0" if is_right else "0.35"
        border = "3px solid #FFD700" if is_right else "1px solid transparent"
        check = " ✓" if is_right else ""
        tiles_html.append(
            f'<div style="background:{color};padding:14px;border-radius:10px;'
            f'color:white;opacity:{opacity};border:{border};text-align:center;">'
            f'<strong>{shape}</strong>&nbsp;{ans}{check}</div>'
        )
    tiles_section = (
        '<div style="font-weight:bold;margin:6px 0;color:white;">Correct answer(s):</div>'
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">'
        + "".join(tiles_html) +
        '</div>'
    )

    expl_html = ""
    if question.get("explanation"):
        expl_html = (
            '<div style="background:#1c2733;border-left:4px solid #4fc3f7;'
            'padding:12px;border-radius:6px;margin-top:12px;color:#cde;">'
            f'💡 {question["explanation"]}'
            '</div>'
        )

    session_fresh = load_session(quiz_id)
    lb = get_leaderboard(session_fresh, top_n=5)
    lb_html = ""
    if lb:
        rows = ""
        for rank, p in enumerate(lb, 1):
            is_me = p["name"] == name
            bg = "#2d2d5e" if is_me else "#1a1a2e"
            border_str = "border:1px solid #4fc3f7;" if is_me else ""
            rows += (
                f'<div style="display:flex;align-items:center;padding:8px;background:{bg};'
                f'border-radius:8px;margin:3px 0;{border_str}">'
                f'<span style="width:30px;font-weight:bold;color:#aaa;">#{rank}</span>'
                f'<span style="font-size:1.3em;margin:0 8px;">{p["emoji"]}</span>'
                f'<span style="color:white;flex:1;">{p["name"]}</span>'
                f'<span style="color:gold;font-weight:bold;">{p["score"]:,}</span>'
                '</div>'
            )
        lb_html = (
            '<div style="font-weight:bold;margin-top:14px;color:white;">Top 5:</div>'
            + rows
        )

    waiting_html = (
        '<div style="color:#888;font-size:0.9em;text-align:center;margin-top:14px;">'
        'Waiting for host to continue…'
        '</div>'
    )

    main.markdown(banner + tiles_section + expl_html + lb_html + waiting_html,
                  unsafe_allow_html=True)

    time.sleep(1)
    st.rerun()

# =============================================================================
# FINISHED
# =============================================================================
elif status == "finished":
    session_fresh = load_session(quiz_id)
    participants = session_fresh.get("participants", {})
    sorted_p = sorted(participants.values(), key=lambda x: x["score"], reverse=True)
    my_rank = next((i + 1 for i, p in enumerate(sorted_p) if p["name"] == name), None)
    final_score = p_data.get("score", 0)

    if my_rank == 1:
        st.balloons()
        st.markdown(f"""
        <div style="text-align:center; padding:30px; background:#1a1a2e; border-radius:14px;">
            <div style="font-size:4em;">{emoji}</div>
            <h1 style="color:gold; margin:8px 0;">🏆 You Won!</h1>
            <h2 style="color:white; margin:0;">{final_score:,} points</h2>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div style="text-align:center; padding:24px; background:#1a1a2e; border-radius:14px;">
            <div style="font-size:3em;">{emoji}</div>
            <h2 style="color:white; margin:8px 0;">Quiz Complete!</h2>
            <h3 style="color:gold; margin:0;">
                Rank #{my_rank} &nbsp;·&nbsp; {final_score:,} pts
            </h3>
        </div>
        """, unsafe_allow_html=True)

    st.subheader("Final Leaderboard")
    medals = ["🥇", "🥈", "🥉"] + [f"{n}." for n in range(4, 51)]
    for rank, p in enumerate(sorted_p, 1):
        is_me = p["name"] == name
        bg = "#2d2d5e" if is_me else "#1a1a2e"
        st.markdown(f"""
        <div style="display:flex; align-items:center; padding:10px; background:{bg};
                     border-radius:8px; margin:4px 0;
                     {'border:2px solid #4fc3f7;' if is_me else ''}">
            <span style="width:36px; font-size:1.2em;">{medals[rank - 1]}</span>
            <span style="font-size:1.5em; margin:0 8px;">{p['emoji']}</span>
            <span style="color:white; flex:1;">{p['name']}</span>
            <span style="color:gold; font-weight:bold; font-size:1.05em;">
                {p['score']:,} pts
            </span>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br/>", unsafe_allow_html=True)
    if st.button("🏠 Back to Home", use_container_width=True):
        for key in ["joined", "participant_quiz_id", "participant_name",
                    "participant_emoji", "selected_emoji"]:
            st.session_state.pop(key, None)
        st.switch_page("ai_quiz_game_app.py")
