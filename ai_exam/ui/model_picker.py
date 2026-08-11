"""Two-tier model picker for the Streamlit Run page.

Each tier (HIGH / LOW) gets a (provider, model) pair. The provider drives a
short, curated model list for Anthropic and ARC, and a dynamic list pulled
from `localhost:11434/api/tags` for Ollama (filtered to exclude embedding
models). When Ollama is unreachable the picker falls back to whatever value
is in `OLLAMA_MODEL` from `.env`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import httpx
import streamlit as st

import config as _cfg


_PROVIDERS = ["ollama", "arc", "anthropic"]


def _anthropic_models() -> list[str]:
    """Anthropic model ids offered in the picker, sourced from `.env`/config
    (ANTHROPIC_MODEL_OPUS / _SONNET / _HAIKU) so editing `.env` changes the
    dropdown. De-duplicated, order preserved (opus, sonnet, haiku)."""
    ordered = [
        _cfg.ANTHROPIC_MODEL_OPUS,
        _cfg.ANTHROPIC_MODEL_SONNET,
        _cfg.ANTHROPIC_MODEL_HAIKU,
    ]
    seen: set[str] = set()
    return [m for m in ordered if m and not (m in seen or seen.add(m))]


_ARC_MODELS = ["gpt-oss-120b"]


@dataclass(frozen=True)
class TierChoice:
    provider: str
    model: str


def _ollama_models(host: str = "http://localhost:11434") -> list[str]:
    """List chat-capable models on the local Ollama server.

    Filters out embedding models so the picker only shows things that work
    as a chat completion endpoint. Cached for the streamlit session via
    `st.cache_data` so we don't hit the API on every rerun.
    """

    @st.cache_data(ttl=60.0, show_spinner=False)
    def _query(host_: str) -> list[str]:
        try:
            r = httpx.get(f"{host_}/api/tags", timeout=2.0)
            r.raise_for_status()
            names: list[str] = [m.get("name", "") for m in r.json().get("models", [])]
            return sorted(n for n in names if n and "embed" not in n)
        except (httpx.HTTPError, ValueError):
            return []

    return _query(host)


def _models_for(provider: str, fallback: str) -> list[str]:
    if provider == "anthropic":
        return _anthropic_models()
    if provider == "arc":
        return _ARC_MODELS
    if provider == "ollama":
        models = _ollama_models()
        if not models:
            return [fallback]
        return models
    return [fallback]


def render_tier_picker(
    *,
    label: str,
    help: str,
    state_key_provider: str,
    state_key_model: str,
    ollama_fallback_model: str,
    default: TierChoice,
) -> TierChoice:
    """Render one tier's (provider, model) selector pair side-by-side."""
    st.markdown(f"**{label}**")
    st.caption(help)
    cols = st.columns([1, 2])
    with cols[0]:
        cur_provider = st.session_state.get(state_key_provider, default.provider)
        if cur_provider not in _PROVIDERS:
            cur_provider = default.provider
        provider = st.selectbox(
            "Provider",
            _PROVIDERS,
            index=_PROVIDERS.index(cur_provider),
            key=state_key_provider,
            label_visibility="collapsed",
        )
    models = _models_for(provider, ollama_fallback_model)
    with cols[1]:
        cur_model = st.session_state.get(state_key_model, default.model)
        # If the saved model isn't valid for the newly-chosen provider, snap
        # to the first available model for this provider.
        if cur_model not in models:
            cur_model = models[0]
            # Streamlit will overwrite session state when the widget renders;
            # set it now so the selectbox shows the right initial value.
            st.session_state[state_key_model] = cur_model
        model = st.selectbox(
            "Model",
            models,
            index=models.index(cur_model),
            key=state_key_model,
            label_visibility="collapsed",
        )
    return TierChoice(provider=provider, model=model)
