import streamlit as st

from anthropic import Anthropic, APIError

from config import PROVIDER_CONFIG


def render():
    st.header("Settings")

    # ── Output ────────────────────────────────────────────────────────────────
    st.subheader("Output Settings")
    st.text_input("Output Path", value=st.session_state.output_path, disabled=True)
    st.caption(
        "This is just where your output files are temporarily stored on the server. "
        "You will be able to download them from the **Outputs** tab — individually "
        "or all together as a ZIP."
    )

    # ── LLM (VT ARC) ──────────────────────────────────────────────────────────
    st.subheader("LLM Settings (VT ARC)")
    st.session_state.selected_provider = "ARC"
    arc = PROVIDER_CONFIG["ARC"]

    provider_models = arc.get("models", [])
    if not provider_models:
        st.warning(
            "No VT ARC model found in the .env file. "
            "Please add one (e.g., ANTHROPIC_MODEL=vt-arc-llm) to your .env file."
        )
        st.session_state.selected_model = None
    else:
        current_model_index = 0
        if st.session_state.selected_model in provider_models:
            current_model_index = provider_models.index(st.session_state.selected_model)

        selected_model = st.selectbox(
            "Model",
            provider_models,
            index=current_model_index,
            help="The VT ARC model to use.",
            key="model_selector_ARC",
        )
        st.session_state.selected_model = selected_model

    # ── VT ARC API key: not required, but highly recommended ─────────────────
    st.markdown("##### 🔑 LLM API key — highly recommended")
    st.caption("Please create your own API key for the ARC models.")
    # A stable widget key: with value= instead, the widget's ID changes once a
    # key is entered and the user's next edit (e.g. clearing it) is discarded.
    if "api_key_widget" not in st.session_state:
        st.session_state.api_key_widget = st.session_state.get("api_key_input", "")
    api_key_input = st.text_input(
        "API key",
        key="api_key_widget",
        type="password",
        placeholder="sk-…",
        label_visibility="collapsed",
    ).strip()
    st.session_state.api_key_input = api_key_input
    if api_key_input:
        _show_key_check(api_key_input, arc["endpoint"])
    elif arc.get("api_key"):
        st.warning(
            "**Highly recommended:** enter your own key. Until you do, the app's "
            "shared key from .env is used."
        )
    else:
        st.warning("No key in .env — enter your own key here to run the app.")
    st.markdown(
        "**Get your own VT ARC API key**\n"
        "1. Go to [llm.arc.vt.edu](https://llm.arc.vt.edu/)\n"
        "2. Log in with your VT credentials (PID and password)\n"
        "3. Click your profile icon → **Settings** → **Account** → "
        "**API Keys**\n"
        "4. Click **Create new secret key**, name it, and click "
        "**Create**\n"
        "5. **Copy the key immediately** and paste it above"
    )


def _show_key_check(api_key: str, endpoint: str) -> None:
    """Verify an entered ARC key once (cached per key) and show the result."""
    if st.session_state.get("arc_key_checked") != api_key:
        try:
            Anthropic(
                api_key=api_key,
                base_url=endpoint.rstrip("/").removesuffix("/v1"),
                max_retries=0,
            ).models.list(limit=1)
            st.session_state.arc_key_error = ""
        except APIError as e:
            st.session_state.arc_key_error = f"{type(e).__name__}: {e}"
        st.session_state.arc_key_checked = api_key
    if st.session_state.arc_key_error:
        st.error(f"❌ ARC could not verify this key — {st.session_state.arc_key_error}")
    else:
        st.success("✓ Key verified — using the key entered here.")
