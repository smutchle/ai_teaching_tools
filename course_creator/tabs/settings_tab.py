import streamlit as st

from config import PROVIDER_CONFIG, OLLAMA_END_POINT


def render():
    st.header("Settings")

    st.subheader("Output Settings")
    notebook_path = st.text_input("Output Path", value=st.session_state.output_path)
    st.session_state.output_path = notebook_path

    st.subheader("LLM Provider Settings")

    available_providers = list(PROVIDER_CONFIG.keys())
    selected_provider = st.selectbox(
        "Select LLM Provider",
        available_providers,
        index=available_providers.index(st.session_state.selected_provider),
    )
    st.session_state.selected_provider = selected_provider

    provider_models = PROVIDER_CONFIG[selected_provider].get("models", [])
    if not provider_models:
        st.warning(
            f"No models found for {selected_provider} in the .env file. "
            f"Please add models (e.g., {selected_provider.upper()}_MODEL=model1,model2) to your .env file."
        )
        st.session_state.selected_model = None
    else:
        current_model_index = 0
        if st.session_state.selected_model in provider_models:
            current_model_index = provider_models.index(st.session_state.selected_model)

        selected_model = st.selectbox(
            "Select Model",
            provider_models,
            index=current_model_index,
            help=f"Select the {selected_provider} model to use.",
            key=f"model_selector_{selected_provider}",
        )
        st.session_state.selected_model = selected_model

    api_key_label = f"{selected_provider} API Key (Optional Override)"
    endpoint_label = f"{selected_provider} Endpoint (Optional Override)"

    if selected_provider in ["Anthropic", "OpenAI", "Google"]:
        default_key = PROVIDER_CONFIG[selected_provider].get("api_key", "")
        if f"{selected_provider}_api_key_input" not in st.session_state:
            st.session_state[f"{selected_provider}_api_key_input"] = default_key

        api_key_input = st.text_input(
            api_key_label,
            value=st.session_state.get(f"{selected_provider}_api_key_input", default_key),
            type="password",
            help="Leave blank to use the key from .env file, or enter a key to override it.",
            key=f"api_key_input_{selected_provider}",
        )
        st.session_state[f"{selected_provider}_api_key_input"] = api_key_input
        st.session_state.api_key_input = api_key_input
        st.session_state.ollama_endpoint_input = None

    elif selected_provider == "Ollama":
        default_endpoint = PROVIDER_CONFIG[selected_provider].get("endpoint", "http://localhost:11434")
        if "ollama_endpoint_input" not in st.session_state:
            st.session_state.ollama_endpoint_input = default_endpoint

        ollama_endpoint_input = st.text_input(
            endpoint_label,
            value=st.session_state.ollama_endpoint_input,
            help="Leave blank to use the endpoint from .env file (or default), or enter an endpoint to override it.",
            key="ollama_endpoint_input_widget",
        )
        st.session_state.ollama_endpoint_input = ollama_endpoint_input
        st.session_state.api_key_input = None
