import streamlit as st

from OllamaChatBot import OllamaChatBot
from AnthropicChatBot import AnthropicChatBot
from OpenAIChatBot import OpenAIChatBot
from GoogleChatBot import GoogleChatBot
from config import PROVIDER_CONFIG


def create_chatbot(chatbot_type, model, api_key_override=None, endpoint_override=None):
    """
    Creates a chatbot instance based on the selected type.
    Uses API keys/endpoints from .env by default, but allows overrides from UI.
    """
    config = PROVIDER_CONFIG.get(chatbot_type)
    if not config:
        st.error(f"Invalid chatbot type specified: {chatbot_type}")
        return None

    try:
        if chatbot_type == "Anthropic":
            key = api_key_override if api_key_override else config.get("api_key")
            if not key:
                st.error("Anthropic API Key not found in .env or provided in UI.")
                return None
            return AnthropicChatBot(key, model)
        elif chatbot_type == "OpenAI":
            key = api_key_override if api_key_override else config.get("api_key")
            if not key:
                st.error("OpenAI API Key not found in .env or provided in UI.")
                return None
            return OpenAIChatBot(key, model)
        elif chatbot_type == "Google":
            key = api_key_override if api_key_override else config.get("api_key")
            if not key:
                st.error("Google API Key not found in .env or provided in UI.")
                return None
            return GoogleChatBot(key, model)
        elif chatbot_type == "Ollama":
            endpoint = endpoint_override if endpoint_override else config.get("endpoint")
            if not endpoint:
                st.error("Ollama Endpoint not found in .env or provided in UI.")
                return None
            return OllamaChatBot(model, endpoint)
        else:
            st.error(f"Unknown ChatBot type: {chatbot_type}")
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
