import streamlit as st

from langchain_chatbot import LangChainChatBot
from config import PROVIDER_CONFIG


def create_chatbot(chatbot_type, model, api_key_override=None):
    """
    Creates a LangChainChatBot for VT ARC.
    A key entered in the Settings tab is used instead of the key in .env;
    the .env key is only used when the Settings field is blank.
    """
    config = PROVIDER_CONFIG.get(chatbot_type)
    if not config:
        st.error(f"Invalid chatbot type specified: {chatbot_type}")
        return None

    if not model:
        st.error("No model selected.")
        return None

    key = (api_key_override or "").strip() or config.get("api_key")
    if not key:
        st.error("No VT ARC API key: enter yours in the Settings tab (or set ANTHROPIC_API_KEY in .env).")
        return None

    try:
        return LangChainChatBot(chatbot_type, model, api_key=key, endpoint=config["endpoint"])
    except Exception as e:
        st.error(f"Error initializing {chatbot_type} chatbot for model {model}: {e}")
        return None


def create_chatbot_from_session():
    """Creates a chatbot using the current session state LLM settings."""
    return create_chatbot(
        st.session_state.selected_provider,
        st.session_state.selected_model,
        st.session_state.api_key_input,
    )
