import uuid

import pandas as pd
import streamlit as st
from vt_banner import render_vt_banner

from styles import inject_css
import tabs.settings_tab as settings_tab
import tabs.materials_tab as materials_tab
import tabs.course_tab as course_tab
import tabs.lectures_tab as lectures_tab
import tabs.topics_tab as topics_tab
import tabs.outputs_tab as outputs_tab

# --- Page Config ---
st.set_page_config(
    layout="wide",
    initial_sidebar_state="collapsed",
    page_title="AI Course Builder",
)
render_vt_banner()

inject_css()

# --- Header ---
st.markdown("""
<div class="app-header">
    <div class="app-header-text">
        <h1>AI Course Builder</h1>
        <p>AI Course Scaffolding Generator</p>
    </div>
</div>
""", unsafe_allow_html=True)

# --- Session State Initialization ---
if "lecture_df" not in st.session_state:
    st.session_state.lecture_df = pd.DataFrame(columns=["title", "description", "selected"])
if "topics_df" not in st.session_state:
    st.session_state.topics_df = pd.DataFrame(columns=["lecture_title", "topic_title", "topic_description", "selected"])

if "selected_provider" not in st.session_state:
    st.session_state.selected_provider = "ARC"

if "selected_model" not in st.session_state:
    st.session_state.selected_model = None

if "lecture_level" not in st.session_state:
    st.session_state.lecture_level = "graduate"

if "output_path" not in st.session_state:
    st.session_state.output_path = f"/tmp/{uuid.uuid4().hex}"

# The user's own VT ARC key ("" = use the shared key from .env).
if "api_key_input" not in st.session_state:
    st.session_state.api_key_input = ""

# RAG session state
if "rag_mode" not in st.session_state:
    st.session_state["rag_mode"] = "None"
if "rag_token_budget" not in st.session_state:
    st.session_state["rag_token_budget"] = 20_000
if "rag_top_k" not in st.session_state:
    st.session_state["rag_top_k"] = 8
if "rag_db_ready" not in st.session_state:
    st.session_state["rag_db_ready"] = False

# --- Tabs ---
tab_settings, tab_materials, tab_course, tab_lectures, tab_topics, tab_notebooks = st.tabs(
    ["Settings", "Materials", "Course", "Modules", "Topics", "Outputs"]
)

with tab_settings:
    settings_tab.render()

with tab_materials:
    materials_tab.render()

with tab_course:
    course_tab.render()

with tab_lectures:
    lectures_tab.render()

with tab_topics:
    topics_tab.render()

with tab_notebooks:
    outputs_tab.render()
