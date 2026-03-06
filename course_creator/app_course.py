import base64
import os
import uuid

import pandas as pd
import streamlit as st

from config import PROVIDER_CONFIG, OLLAMA_END_POINT
from styles import inject_css
import tabs.settings_tab as settings_tab
import tabs.course_tab as course_tab
import tabs.lectures_tab as lectures_tab
import tabs.topics_tab as topics_tab
import tabs.outputs_tab as outputs_tab

# --- Page Config ---
_logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.jpg")
try:
    from PIL import Image as _PILImage
    _page_icon = _PILImage.open(_logo_path) if os.path.exists(_logo_path) else "📚"
except ImportError:
    _page_icon = "📚"

st.set_page_config(
    layout="wide",
    initial_sidebar_state="collapsed",
    page_title="AI Course Builder",
    page_icon=_page_icon,
)

inject_css()

# --- Header ---
def _load_logo_b64():
    if os.path.exists(_logo_path):
        with open(_logo_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return None

_LOGO_B64 = _load_logo_b64()
_logo_html = f'<img src="data:image/jpeg;base64,{_LOGO_B64}" alt="Logo">' if _LOGO_B64 else ''
st.markdown(f"""
<div class="app-header">
    {_logo_html}
    <div class="app-header-text">
        <h1>AI Course Builder</h1>
        <p>AI-Powered Course Design Tool</p>
    </div>
    <span class="app-header-badge">Beta</span>
</div>
""", unsafe_allow_html=True)

# --- Session State Initialization ---
if "lecture_df" not in st.session_state:
    st.session_state.lecture_df = pd.DataFrame(columns=["title", "description", "selected"])
if "topics_df" not in st.session_state:
    st.session_state.topics_df = pd.DataFrame(columns=["lecture_title", "topic_title", "topic_description", "selected"])

if "selected_provider" not in st.session_state:
    first_provider = "Anthropic"
    for p, c in PROVIDER_CONFIG.items():
        if c.get("models"):
            first_provider = p
            break
    st.session_state.selected_provider = first_provider

if "selected_model" not in st.session_state:
    st.session_state.selected_model = None

if "lecture_level" not in st.session_state:
    st.session_state.lecture_level = "graduate"

if "output_path" not in st.session_state:
    st.session_state.output_path = f"/tmp/{uuid.uuid4().hex}"

if "api_key_input" not in st.session_state:
    st.session_state.api_key_input = ""

if "ollama_endpoint_input" not in st.session_state:
    st.session_state.ollama_endpoint_input = PROVIDER_CONFIG["Ollama"]["endpoint"]

# --- Tabs ---
tab_settings, tab_course, tab_lectures, tab_topics, tab_notebooks = st.tabs(
    ["Settings", "Course", "Modules", "Topics", "Outputs"]
)

with tab_settings:
    settings_tab.render()

with tab_course:
    course_tab.render()

with tab_lectures:
    lectures_tab.render()

with tab_topics:
    topics_tab.render()

with tab_notebooks:
    outputs_tab.render()
