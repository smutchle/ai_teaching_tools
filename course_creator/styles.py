import streamlit as st


def inject_css():
    st.markdown(
        """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400&family=Syne:wght@400;500;600;700&display=swap');

    /* ── Root Variables ── */
    :root {
        --bg-deep:    #0b0e1a;
        --bg-base:    #111626;
        --bg-surface: #181e30;
        --bg-card:    #1e2638;
        --bg-hover:   #242d42;
        --accent:     #d4a437;
        --accent-dim: #8a6820;
        --accent-glow:#f0c458;
        --teal:       #4ab8b0;
        --teal-dim:   #2a7c76;
        --text-hi:    #eef2f8;
        --text-mid:   #a8b4cc;
        --text-lo:    #5c6880;
        --border:     #2a3249;
        --border-hi:  #3a4560;
        --success:    #4caf82;
        --error:      #e05c6a;
        --warn:       #e0943a;
        --radius-sm:  6px;
        --radius-md:  10px;
        --radius-lg:  16px;
        --shadow-sm:  0 1px 4px rgba(0,0,0,0.4);
        --shadow-md:  0 4px 16px rgba(0,0,0,0.5);
    }

    /* ── Global Reset ── */
    html, body, .stApp {
        background-color: var(--bg-deep) !important;
        color: var(--text-hi) !important;
        font-family: 'Syne', sans-serif !important;
    }

    /* ── Main Content Area ── */
    .main .block-container {
        max-width: 1340px !important;
        padding: 0 2rem 3rem !important;
        background-color: transparent !important;
    }

    /* ── App Header ── */
    .app-header {
        display: flex;
        align-items: center;
        gap: 18px;
        padding: 28px 0 20px;
        border-bottom: 1px solid var(--border);
        margin-bottom: 28px;
    }
    .app-header img {
        width: 56px;
        height: 56px;
        border-radius: var(--radius-md);
        object-fit: cover;
        filter: brightness(1.1) contrast(1.1);
        box-shadow: 0 0 24px rgba(212,164,55,0.25);
    }
    .app-header-text h1 {
        font-family: 'Cormorant Garamond', serif !important;
        font-size: 2.4rem !important;
        font-weight: 600 !important;
        color: var(--text-hi) !important;
        margin: 0 0 2px !important;
        letter-spacing: 0.01em;
        line-height: 1.1;
    }
    .app-header-text p {
        font-family: 'Syne', sans-serif;
        font-size: 0.88rem;
        color: var(--text-lo);
        margin: 0;
        letter-spacing: 0.12em;
        text-transform: uppercase;
    }
    .app-header-badge {
        margin-left: auto;
        background: linear-gradient(135deg, var(--accent-dim), var(--accent));
        color: var(--bg-deep);
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        padding: 4px 10px;
        border-radius: 20px;
        font-family: 'Syne', sans-serif;
    }

    /* ── Headings ── */
    h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
        font-family: 'Cormorant Garamond', serif !important;
        color: var(--text-hi) !important;
        letter-spacing: 0.01em;
    }
    h1 { font-size: 2.1rem !important; font-weight: 600 !important; }
    h2 { font-size: 1.6rem !important; font-weight: 500 !important; color: var(--text-mid) !important; }
    h3 { font-size: 1.3rem !important; font-weight: 500 !important; }

    [data-testid="stHeadingWithActionElements"] h2,
    [data-testid="stHeadingWithActionElements"] h3 {
        font-family: 'Cormorant Garamond', serif !important;
        color: var(--text-hi) !important;
    }

    /* ── Tabs ── */
    [data-testid="stTabs"] { background: transparent !important; }
    [data-testid="stTabsTablist"] {
        background: var(--bg-surface) !important;
        border-radius: var(--radius-md) var(--radius-md) 0 0 !important;
        border-bottom: 1px solid var(--border) !important;
        padding: 0 8px !important;
        gap: 0 !important;
    }
    [data-testid="stTab"] {
        background: transparent !important;
        color: var(--text-lo) !important;
        font-family: 'Syne', sans-serif !important;
        font-size: 0.92rem !important;
        font-weight: 500 !important;
        letter-spacing: 0.06em !important;
        text-transform: uppercase !important;
        padding: 12px 20px !important;
        border: none !important;
        border-bottom: 2px solid transparent !important;
        transition: color 0.2s, border-color 0.2s !important;
    }
    [data-testid="stTab"]:hover {
        color: var(--text-mid) !important;
        border-bottom-color: var(--border-hi) !important;
        background: rgba(255,255,255,0.03) !important;
    }
    [data-testid="stTab"][aria-selected="true"] {
        color: var(--accent-glow) !important;
        border-bottom-color: var(--accent) !important;
        background: transparent !important;
    }
    [data-testid="stTabPanel"] {
        background: var(--bg-surface) !important;
        border: 1px solid var(--border) !important;
        border-top: none !important;
        border-radius: 0 0 var(--radius-md) var(--radius-md) !important;
        padding: 28px !important;
    }

    /* ── Buttons ── */
    .stButton > button {
        background: linear-gradient(135deg, #1e2638, #2a3454) !important;
        color: var(--text-mid) !important;
        border: 1px solid var(--border-hi) !important;
        border-radius: var(--radius-sm) !important;
        font-family: 'Syne', sans-serif !important;
        font-size: 0.92rem !important;
        font-weight: 600 !important;
        letter-spacing: 0.08em !important;
        text-transform: uppercase !important;
        padding: 8px 18px !important;
        transition: all 0.2s ease !important;
        box-shadow: var(--shadow-sm) !important;
    }
    .stButton > button:hover {
        background: linear-gradient(135deg, #2a3454, #323e60) !important;
        color: var(--text-hi) !important;
        border-color: var(--accent-dim) !important;
        box-shadow: 0 0 12px rgba(212,164,55,0.15), var(--shadow-sm) !important;
        transform: translateY(-1px) !important;
    }
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, var(--accent-dim), var(--accent)) !important;
        color: var(--bg-deep) !important;
        border-color: var(--accent) !important;
    }
    .stButton > button[kind="primary"]:hover {
        background: linear-gradient(135deg, var(--accent), var(--accent-glow)) !important;
        box-shadow: 0 0 20px rgba(212,164,55,0.35), var(--shadow-sm) !important;
    }
    .stDownloadButton > button {
        background: linear-gradient(135deg, var(--teal-dim), var(--teal)) !important;
        color: var(--bg-deep) !important;
        border: none !important;
        border-radius: var(--radius-sm) !important;
        font-family: 'Syne', sans-serif !important;
        font-size: 0.8rem !important;
        font-weight: 600 !important;
        letter-spacing: 0.06em !important;
        padding: 8px 18px !important;
        transition: all 0.2s ease !important;
    }
    .stDownloadButton > button:hover {
        box-shadow: 0 0 16px rgba(74,184,176,0.3) !important;
        transform: translateY(-1px) !important;
    }

    /* ── Text Inputs & Text Areas ── */
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea,
    .stNumberInput > div > div > input {
        background: var(--bg-card) !important;
        color: var(--text-hi) !important;
        border: 1px solid var(--border) !important;
        border-radius: var(--radius-sm) !important;
        font-family: 'Syne', sans-serif !important;
        font-size: 1rem !important;
        padding: 10px 14px !important;
        transition: border-color 0.2s, box-shadow 0.2s !important;
    }
    .stTextInput > div > div > input:focus,
    .stTextArea > div > div > textarea:focus,
    .stNumberInput > div > div > input:focus {
        border-color: var(--accent-dim) !important;
        box-shadow: 0 0 0 2px rgba(212,164,55,0.12) !important;
        outline: none !important;
    }
    .stTextInput label, .stTextArea label, .stNumberInput label,
    .stSelectbox label, .stFileUploader label, .stCheckbox label {
        font-family: 'Syne', sans-serif !important;
        font-size: 0.88rem !important;
        font-weight: 600 !important;
        letter-spacing: 0.08em !important;
        text-transform: uppercase !important;
        color: var(--text-lo) !important;
        margin-bottom: 6px !important;
    }

    /* ── Selectbox ── */
    .stSelectbox > div > div {
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-radius: var(--radius-sm) !important;
        color: var(--text-hi) !important;
    }
    .stSelectbox > div > div:hover { border-color: var(--border-hi) !important; }
    [data-testid="stSelectboxVirtualDropdown"] {
        background: var(--bg-card) !important;
        border: 1px solid var(--border-hi) !important;
        border-radius: var(--radius-sm) !important;
    }

    /* ── Data Editor / Tables ── */
    [data-testid="stDataEditor"], .stDataFrame {
        border: 1px solid var(--border) !important;
        border-radius: var(--radius-md) !important;
        overflow: hidden !important;
        background: var(--bg-card) !important;
    }
    [data-testid="stDataEditor"] th {
        background: var(--bg-surface) !important;
        color: var(--text-lo) !important;
        font-family: 'Syne', sans-serif !important;
        font-size: 0.82rem !important;
        font-weight: 700 !important;
        letter-spacing: 0.1em !important;
        text-transform: uppercase !important;
        border-bottom: 1px solid var(--border) !important;
    }
    [data-testid="stDataEditor"] td {
        background: var(--bg-card) !important;
        color: var(--text-mid) !important;
        border-bottom: 1px solid var(--border) !important;
    }

    /* ── Info / Warning / Error / Success Boxes ── */
    [data-testid="stInfo"] {
        background: rgba(74,184,176,0.08) !important;
        border: 1px solid var(--teal-dim) !important;
        border-left: 3px solid var(--teal) !important;
        border-radius: var(--radius-sm) !important;
        color: var(--teal) !important;
    }
    [data-testid="stSuccess"], .stSuccess {
        background: rgba(76,175,130,0.08) !important;
        border: 1px solid #2a6648 !important;
        border-left: 3px solid var(--success) !important;
        border-radius: var(--radius-sm) !important;
        color: var(--success) !important;
    }
    [data-testid="stWarning"], .stWarning {
        background: rgba(224,148,58,0.08) !important;
        border: 1px solid #7a5020 !important;
        border-left: 3px solid var(--warn) !important;
        border-radius: var(--radius-sm) !important;
        color: var(--warn) !important;
    }
    [data-testid="stError"], .stError {
        background: rgba(224,92,106,0.08) !important;
        border: 1px solid #7a2030 !important;
        border-left: 3px solid var(--error) !important;
        border-radius: var(--radius-sm) !important;
        color: var(--error) !important;
    }

    /* ── File Uploader ── */
    [data-testid="stFileUploader"] > div {
        background: var(--bg-card) !important;
        border: 1.5px dashed var(--border-hi) !important;
        border-radius: var(--radius-md) !important;
        transition: border-color 0.2s, background 0.2s !important;
    }
    [data-testid="stFileUploader"] > div:hover {
        border-color: var(--accent-dim) !important;
        background: var(--bg-hover) !important;
    }

    /* ── Progress Bar ── */
    [data-testid="stProgressBar"] > div > div {
        background: linear-gradient(90deg, var(--accent-dim), var(--accent-glow)) !important;
        border-radius: 4px !important;
    }
    [data-testid="stProgressBar"] > div {
        background: var(--bg-card) !important;
        border-radius: 4px !important;
    }

    /* ── Form ── */
    [data-testid="stForm"] {
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-radius: var(--radius-md) !important;
        padding: 20px !important;
    }

    /* ── Code Blocks ── */
    [data-testid="stCode"] {
        background: var(--bg-deep) !important;
        border: 1px solid var(--border) !important;
        border-radius: var(--radius-sm) !important;
    }
    pre code { font-size: 0.92rem !important; color: var(--text-mid) !important; }

    /* ── Scrollbar ── */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: var(--bg-base); }
    ::-webkit-scrollbar-thumb { background: var(--border-hi); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--accent-dim); }

    /* ── Download Links (CSV) ── */
    .stMarkdown a {
        color: var(--teal) !important;
        text-decoration: none !important;
        font-size: 0.95rem !important;
        font-weight: 600 !important;
        transition: color 0.15s !important;
    }
    .stMarkdown a:hover { color: var(--accent-glow) !important; text-decoration: underline !important; }

    /* ── Sidebar ── */
    [data-testid="stSidebar"] {
        background: var(--bg-surface) !important;
        border-right: 1px solid var(--border) !important;
    }

    /* ── Number Input Arrows ── */
    .stNumberInput > div > div > div > button {
        background: var(--bg-card) !important;
        border-color: var(--border) !important;
        color: var(--text-mid) !important;
    }
    .stNumberInput > div > div > div > button:hover {
        background: var(--bg-hover) !important;
        border-color: var(--accent-dim) !important;
        color: var(--accent-glow) !important;
    }

    /* ── Misc ── */
    hr { border-color: var(--border) !important; opacity: 1 !important; margin: 20px 0 !important; }
    [title="Show password text"] { display: none; }
</style>
""",
        unsafe_allow_html=True,
    )
