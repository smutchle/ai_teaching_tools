import streamlit as st

from langchain_chatbot import LangChainChatBot
from config import PROVIDER_CONFIG


def create_chatbot(chatbot_type, model, api_key_override=None, endpoint_override=None):
    """
    Creates a LangChainChatBot instance based on the selected provider.
    Uses API keys/endpoints from .env by default, but allows overrides from UI.
    """
    config = PROVIDER_CONFIG.get(chatbot_type)
    if not config:
        st.error(f"Invalid chatbot type specified: {chatbot_type}")
        return None

    if not model:
        st.error("No model selected.")
        return None

    try:
        if chatbot_type == "Anthropic":
            key = api_key_override if api_key_override else config.get("api_key")
            if not key:
                st.error("Anthropic API Key not found in .env or provided in UI.")
                return None
            return LangChainChatBot("Anthropic", model, api_key=key)

        elif chatbot_type == "OpenAI":
            key = api_key_override if api_key_override else config.get("api_key")
            if not key:
                st.error("OpenAI API Key not found in .env or provided in UI.")
                return None
            return LangChainChatBot("OpenAI", model, api_key=key)

        elif chatbot_type == "Google":
            key = api_key_override if api_key_override else config.get("api_key")
            if not key:
                st.error("Google API Key not found in .env or provided in UI.")
                return None
            return LangChainChatBot("Google", model, api_key=key)

        elif chatbot_type == "Ollama":
            endpoint = endpoint_override if endpoint_override else config.get("endpoint")
            if not endpoint:
                st.error("Ollama Endpoint not found in .env or provided in UI.")
                return None
            return LangChainChatBot("Ollama", model, endpoint=endpoint)

        else:
            st.error(f"Unknown provider: {chatbot_type}")
            return None

    except Exception as e:
        st.error(f"Error initializing {chatbot_type} chatbot for model {model}: {e}")
        return None


def create_chatbot_from_session():
    """Creates a chatbot using the current session state LLM settings."""
    return create_chatbot(
        st.session_state.selected_provider,
        st.session_state.selected_model,
        st.session_state.api_key_input,
        st.session_state.ollama_endpoint_input,
    )
