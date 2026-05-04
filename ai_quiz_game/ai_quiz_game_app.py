import streamlit as st

st.set_page_config(
    page_title="QuizBlast",
    page_icon="🎮",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
[data-testid="stSidebar"] { display: none; }
[data-testid="collapsedControl"] { display: none; }
.stButton button { font-weight: bold; font-size: 1em; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div style="text-align:center; padding: 40px 0 30px 0;">
    <h1 style="font-size: 3.5em; margin-bottom: 0;">🎮 QuizBlast</h1>
    <p style="font-size: 1.2em; color: #888; margin-top: 8px;">
        Live quizzes powered by AI
    </p>
</div>
""", unsafe_allow_html=True)

col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("""
    <div style="text-align:center; padding:20px; background:#1368ce;
                border-radius:12px; margin:0 4px 12px 4px; min-height:140px;">
        <div style="font-size:2.5em;">🙋</div>
        <h3 style="color:white; margin:8px 0 4px 0;">Join a Quiz</h3>
        <p style="color:#cce0ff; font-size:0.9em; margin:0;">Enter a code to participate</p>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Join Quiz", use_container_width=True, key="join_btn"):
        st.switch_page("pages/3_Participant.py")

with col2:
    st.markdown("""
    <div style="text-align:center; padding:20px; background:#e21b3c;
                border-radius:12px; margin:0 4px 12px 4px; min-height:140px;">
        <div style="font-size:2.5em;">🎯</div>
        <h3 style="color:white; margin:8px 0 4px 0;">Host a Quiz</h3>
        <p style="color:#ffd0d8; font-size:0.9em; margin:0;">Run a live quiz session</p>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Host Quiz", use_container_width=True, key="host_btn"):
        st.switch_page("pages/2_Host.py")

with col3:
    st.markdown("""
    <div style="text-align:center; padding:20px; background:#26890c;
                border-radius:12px; margin:0 4px 12px 4px; min-height:140px;">
        <div style="font-size:2.5em;">⚙️</div>
        <h3 style="color:white; margin:8px 0 4px 0;">Create a Quiz</h3>
        <p style="color:#ccf0c0; font-size:0.9em; margin:0;">Upload materials & generate Qs</p>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Create Quiz", use_container_width=True, key="setup_btn"):
        st.switch_page("pages/1_Setup.py")

st.markdown("---")
st.markdown(
    "<p style='text-align:center; color:#666; font-size:0.9em;'>"
    "Upload PDFs or Markdown → AI generates questions → Play live with your class"
    "</p>",
    unsafe_allow_html=True,
)
